#!/usr/bin/env python3
"""Upload exact captured artifacts to an immutable GitHub Release identity."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


def _file_identity(value: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        stat.S_IFMT(value.st_mode),
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_nlink,
    )


def _capture_asset(path: Path) -> Tuple[str, bytes]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise RuntimeError("release asset must be a regular single-link file: %s" % path.name)
    with path.open("rb") as stream:
        opened_before = os.fstat(stream.fileno())
        data = stream.read()
        opened_after = os.fstat(stream.fileno())
    after = path.lstat()
    identities = [_file_identity(item) for item in (before, opened_before, opened_after, after)]
    if any(identity != identities[0] for identity in identities[1:]) or len(data) != before.st_size:
        raise RuntimeError("release asset identity changed while capturing: %s" % path.name)
    return path.name, data


def _release_asset_names(release: Dict[str, object]) -> List[str]:
    return [name for _asset_id, name in _release_asset_identities(release)]


def _release_asset_identities(release: Dict[str, object]) -> List[Tuple[int, str]]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError("GitHub returned an invalid release asset list")
    identities = []
    seen_ids = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise RuntimeError("GitHub returned an invalid release asset")
        asset_id = asset.get("id")
        name = asset.get("name")
        if (
            not isinstance(asset_id, int)
            or asset_id <= 0
            or asset_id in seen_ids
            or not isinstance(name, str)
            or not name
        ):
            raise RuntimeError("GitHub returned an invalid release asset")
        seen_ids.add(asset_id)
        identities.append((asset_id, name))
    return identities


def _release_asset_records(release: Dict[str, object]) -> List[Dict[str, object]]:
    """Validate and retain all server-visible fields used for ownership binding."""
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError("GitHub returned an invalid release asset list")
    records = []
    seen_ids = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise RuntimeError("GitHub returned an invalid release asset")
        asset_id = asset.get("id")
        name = asset.get("name")
        if not isinstance(asset_id, int) or asset_id <= 0 or asset_id in seen_ids:
            raise RuntimeError("GitHub returned an invalid release asset")
        if not isinstance(name, str) or not name:
            raise RuntimeError("GitHub returned an invalid release asset")
        seen_ids.add(asset_id)
        record = {
            "id": asset_id,
            "name": name,
            "label": asset.get("label"),
            "digest": asset.get("digest"),
            "size": asset.get("size"),
            "state": asset.get("state"),
        }
        for key in ("label", "digest", "state"):
            if record[key] is not None and not isinstance(record[key], str):
                raise RuntimeError("GitHub returned an invalid release asset")
        if record["size"] is not None and (
            not isinstance(record["size"], int) or isinstance(record["size"], bool) or record["size"] < 0
        ):
            raise RuntimeError("GitHub returned an invalid release asset")
        records.append(record)
    return records


def _asset_record_identity(record: Dict[str, object]) -> Tuple[object, ...]:
    return tuple(record.get(key) for key in ("id", "name", "label", "digest", "size", "state"))


def _release_id(release: Dict[str, object]) -> int:
    value = release.get("id")
    if not isinstance(value, int) or value <= 0:
        raise RuntimeError("GitHub returned an invalid release identity")
    return value


def _assert_release_binding(client, repository: str, release_id: int, tag: str, expected_commit: str):
    if client.resolve_tag_commit(repository, tag) != expected_commit:
        raise RuntimeError("release tag identity changed")
    by_id = client.get_release(repository, release_id)
    by_tag = client.get_release_by_tag(repository, tag)
    if (
        _release_id(by_id) != release_id
        or _release_id(by_tag) != release_id
        or by_id.get("tag_name") != tag
        or by_tag.get("tag_name") != tag
    ):
        raise RuntimeError("release identity changed")
    by_id_assets = _release_asset_records(by_id)
    by_tag_assets = _release_asset_records(by_tag)
    if {_asset_record_identity(item) for item in by_id_assets} != {
        _asset_record_identity(item) for item in by_tag_assets
    }:
        raise RuntimeError("release asset inventory identity changed")
    return by_id


class _PublicationOwnershipUncertain(RuntimeError):
    pass


def _rebound_release_asset_records(
    client,
    repository: str,
    release_id: int,
    tag: str,
):
    try:
        by_id = client.get_release(repository, release_id)
        by_tag = client.get_release_by_tag(repository, tag)
        if (
            _release_id(by_id) != release_id
            or _release_id(by_tag) != release_id
            or by_id.get("tag_name") != tag
            or by_tag.get("tag_name") != tag
        ):
            raise RuntimeError("release identity changed")
        by_id_assets = _release_asset_records(by_id)
        by_tag_assets = _release_asset_records(by_tag)
        if {_asset_record_identity(item) for item in by_id_assets} != {
            _asset_record_identity(item) for item in by_tag_assets
        }:
            raise RuntimeError("release asset inventory changed during reconciliation")
    except Exception as exc:
        raise _PublicationOwnershipUncertain("release publication ownership is uncertain") from exc
    return by_id_assets


def _reconcile_upload(
    client,
    repository: str,
    release_id: int,
    tag: str,
    before_assets,
    name: str,
    label: str,
    digest: str,
    size: int,
):
    current_assets = _rebound_release_asset_records(client, repository, release_id, tag)
    before_set = {_asset_record_identity(item) for item in before_assets}
    current_set = {_asset_record_identity(item) for item in current_assets}
    if not before_set.issubset(current_set):
        raise _PublicationOwnershipUncertain("release publication ownership is uncertain")
    candidates = [
        record["id"]
        for record in current_assets
        if record["id"] not in {item["id"] for item in before_assets}
        and record["name"] == name
        and record["label"] == label
        and record["digest"] == digest
        and record["size"] == size
        and record["state"] == "uploaded"
    ]
    if len(candidates) > 1:
        raise _PublicationOwnershipUncertain("release publication ownership is uncertain")
    return candidates[0] if candidates else None


def _delete_owned_asset(client, repository: str, release_id: int, tag: str, asset_id: int) -> None:
    delete_error = None
    try:
        client.delete_asset(repository, asset_id)
    except Exception as exc:  # noqa: BLE001
        delete_error = exc
    try:
        remaining_ids = {record["id"] for record in _rebound_release_asset_records(client, repository, release_id, tag)}
    except Exception as exc:
        raise RuntimeError("exact asset cleanup could not be verified") from (delete_error or exc)
    if asset_id in remaining_ids:
        raise RuntimeError("exact asset cleanup failed") from delete_error


def upload_bound_assets(
    client,
    repository: str,
    tag: str,
    expected_commit: str,
    asset_paths: Sequence[Path],
) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise RuntimeError("expected release commit must be a lowercase 40-hex SHA")
    if not repository or not tag or not asset_paths:
        raise RuntimeError("repository, tag, and at least one release asset are required")

    captured = [_capture_asset(Path(path)) for path in asset_paths]
    names = [name for name, _data in captured]
    if len(names) != len(set(names)):
        raise RuntimeError("release asset names must be unique")

    release = client.get_release_by_tag(repository, tag)
    release_id = _release_id(release)
    if release.get("tag_name") != tag:
        raise RuntimeError("release identity changed")
    existing_names = set(_release_asset_names(release))
    collisions = sorted(existing_names.intersection(names))
    if collisions:
        raise RuntimeError("release asset already exists: %s" % ", ".join(collisions))

    uploaded_ids: List[int] = []
    try:
        for name, data in captured:
            current = _assert_release_binding(client, repository, release_id, tag, expected_commit)
            before_assets = _release_asset_records(current)
            if name in [record["name"] for record in before_assets]:
                raise RuntimeError("release asset already exists: %s" % name)
            label = "dcc-mcp-txn-%s" % uuid.uuid4().hex
            digest = "sha256:%s" % hashlib.sha256(data).hexdigest()
            expected_upload_url = current.get("upload_url")
            if not isinstance(expected_upload_url, str) or "{" not in expected_upload_url:
                raise RuntimeError("GitHub returned an invalid release upload URL")
            try:
                uploaded = client.upload_asset(
                    repository,
                    release_id,
                    name,
                    data,
                    label=label,
                    expected_tag=tag,
                    expected_upload_url=expected_upload_url,
                    expected_assets=before_assets,
                )
            except Exception:
                reconciled_id = _reconcile_upload(
                    client,
                    repository,
                    release_id,
                    tag,
                    before_assets,
                    name,
                    label,
                    digest,
                    len(data),
                )
                if reconciled_id is not None:
                    uploaded_ids.append(reconciled_id)
                raise
            uploaded_id = uploaded.get("id") if isinstance(uploaded, dict) else None
            reconciled_id = _reconcile_upload(
                client,
                repository,
                release_id,
                tag,
                before_assets,
                name,
                label,
                digest,
                len(data),
            )
            if reconciled_id is None:
                raise _PublicationOwnershipUncertain("release publication ownership is uncertain")
            if (
                not isinstance(uploaded_id, int)
                or uploaded_id <= 0
                or uploaded.get("name") != name
                or uploaded.get("label") != label
                or uploaded.get("digest") != digest
                or uploaded.get("size") != len(data)
                or uploaded.get("state") != "uploaded"
                or uploaded_id != reconciled_id
            ):
                uploaded_ids.append(reconciled_id)
                raise RuntimeError("GitHub returned an invalid uploaded asset identity")
            if reconciled_id in uploaded_ids:
                raise _PublicationOwnershipUncertain("release publication ownership is uncertain")
            uploaded_ids.append(reconciled_id)
            _assert_release_binding(client, repository, release_id, tag, expected_commit)
        _assert_release_binding(client, repository, release_id, tag, expected_commit)
    except Exception as exc:
        cleanup_errors = []
        for asset_id in reversed(uploaded_ids):
            try:
                _delete_owned_asset(client, repository, release_id, tag, asset_id)
            except Exception as cleanup_exc:  # noqa: BLE001
                cleanup_errors.append(str(cleanup_exc))
        if cleanup_errors:
            raise RuntimeError("release publication failed and exact asset cleanup failed") from exc
        raise


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required")
        self._token = token
        self._api_url = api_url.rstrip("/")

    def _request_json(self, method: str, url: str, *, data: bytes = None, content_type: str = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer %s" % self._token,
            "User-Agent": "dcc-mcp-3dsmax-release-uploader",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError("GitHub API request failed with status %s" % exc.code) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("GitHub API request failed") from exc
        if not payload:
            return {}
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("GitHub API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("GitHub API returned an invalid object")
        return value

    def _api(self, method: str, path: str, *, data: bytes = None, content_type: str = None):
        return self._request_json(method, self._api_url + path, data=data, content_type=content_type)

    def resolve_tag_commit(self, repository: str, tag: str) -> str:
        quoted_tag = urllib.parse.quote(tag, safe="")
        value = self._api("GET", "/repos/%s/git/ref/tags/%s" % (repository, quoted_tag))
        target = value.get("object")
        for _index in range(8):
            if not isinstance(target, dict):
                break
            object_type = target.get("type")
            sha = target.get("sha")
            if object_type == "commit" and isinstance(sha, str):
                return sha
            if object_type != "tag" or not isinstance(sha, str):
                break
            target = self._api("GET", "/repos/%s/git/tags/%s" % (repository, sha)).get("object")
        raise RuntimeError("release tag does not resolve to one commit")

    def get_release_by_tag(self, repository: str, tag: str):
        return self._api("GET", "/repos/%s/releases/tags/%s" % (repository, urllib.parse.quote(tag, safe="")))

    def get_release(self, repository: str, release_id: int):
        return self._api("GET", "/repos/%s/releases/%s" % (repository, release_id))

    def upload_asset(
        self,
        repository: str,
        release_id: int,
        name: str,
        data: bytes,
        *,
        label: str,
        expected_tag: str,
        expected_upload_url: str,
        expected_assets: Sequence[Dict[str, object]],
    ):
        release = self.get_release(repository, release_id)
        if release.get("id") != release_id or release.get("tag_name") != expected_tag:
            raise RuntimeError("release identity changed before asset upload")
        current_assets = _release_asset_records(release)
        if {_asset_record_identity(item) for item in current_assets} != {
            _asset_record_identity(item) for item in expected_assets
        }:
            raise RuntimeError("release asset inventory changed before asset upload")
        upload_url = release.get("upload_url")
        if not isinstance(upload_url, str) or "{" not in upload_url or upload_url != expected_upload_url:
            raise RuntimeError("GitHub returned an invalid release upload URL")
        endpoint = upload_url.split("{", 1)[0] + "?" + urllib.parse.urlencode({"name": name, "label": label})
        return self._request_json("POST", endpoint, data=data, content_type="application/octet-stream")

    def delete_asset(self, repository: str, asset_id: int) -> None:
        self._api("DELETE", "/repos/%s/releases/assets/%s" % (repository, asset_id))


def _resolve_assets(patterns: Iterable[str]) -> List[Path]:
    assets = []
    for pattern in patterns:
        matches = [Path(value) for value in sorted(glob.glob(pattern)) if Path(value).is_file()]
        if not matches:
            raise RuntimeError("release asset pattern matched no files: %s" % pattern)
        assets.extend(matches)
    unique = []
    seen = set()
    for path in assets:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--asset-glob", action="append", required=True)
    args = parser.parse_args(argv)
    client = GitHubClient(
        os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_API_URL", "https://api.github.com")
    )
    upload_bound_assets(client, args.repository, args.tag, args.commit, _resolve_assets(args.asset_glob))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
