"""Behavioral release-asset publication boundary tests."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_uploader():
    path = ROOT / "packaging" / "upload_release_assets.py"
    assert path.is_file(), "release assets must use the repository-owned bound uploader"
    spec = importlib.util.spec_from_file_location("upload_release_assets", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Client:
    def __init__(self, commits, *, existing_assets=()):
        self.commits = list(commits)
        self.uploads = []
        self.deletes = []
        self.release = {
            "id": 4242,
            "tag_name": "v0.2.2",
            "upload_url": "https://uploads.example/release/assets{?name,label}",
            "assets": [{"id": index + 1, "name": name} for index, name in enumerate(existing_assets)],
        }

    def resolve_tag_commit(self, repository, tag):
        assert repository == "dcc-mcp/dcc-mcp-3dsmax"
        assert tag == "v0.2.2"
        if len(self.commits) > 1:
            return self.commits.pop(0)
        return self.commits[0]

    def get_release_by_tag(self, repository, tag):
        return dict(self.release)

    def get_release(self, repository, release_id):
        assert release_id == 4242
        return dict(self.release)

    def upload_asset(self, repository, release_id, name, data, *, label=None, **_kwargs):
        self.uploads.append((repository, release_id, name, data))
        record = {
            "id": 9001,
            "name": name,
            "label": label,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "state": "uploaded",
        }
        self.release["assets"].append(record)
        return dict(record)

    def delete_asset(self, repository, asset_id):
        self.deletes.append((repository, asset_id))
        self.release["assets"] = [asset for asset in self.release["assets"] if asset["id"] != asset_id]


class _UncertainResponseClient(_Client):
    def __init__(self, response_mode, *, duplicate_remote_asset=False):
        super().__init__(["a" * 40])
        self.response_mode = response_mode
        self.duplicate_remote_asset = duplicate_remote_asset

    def upload_asset(self, repository, release_id, name, data, *, label=None, **_kwargs):
        self.uploads.append((repository, release_id, name, data))
        record = {
            "id": 9001,
            "name": name,
            "label": label,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "state": "uploaded",
        }
        self.release["assets"].append(record)
        if self.duplicate_remote_asset:
            self.release["assets"].append(dict(record, id=9002))
        if self.response_mode == "lost":
            raise RuntimeError("upload response was lost after acceptance")
        return {} if self.response_mode == "malformed" else dict(record)

    def delete_asset(self, repository, asset_id):
        super().delete_asset(repository, asset_id)
        self.release["assets"] = [asset for asset in self.release["assets"] if asset["id"] != asset_id]


class _PlausibleForeignResponseClient(_Client):
    def __init__(self):
        super().__init__(["a" * 40])
        self.upload_calls = 0

    def upload_asset(self, repository, release_id, name, data, *, label=None, **_kwargs):
        self.uploads.append((repository, release_id, name, data))
        self.upload_calls += 1
        if self.upload_calls > 1:
            raise RuntimeError("second upload failed before acceptance")
        self.release["assets"].extend(
            [
                {"id": 7001, "name": "foreign.bin"},
                {
                    "id": 9001,
                    "name": name,
                    "label": label,
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "state": "uploaded",
                },
            ]
        )
        return {"id": 7001, "name": name}


class _AcceptedCleanupLostResponseClient(_UncertainResponseClient):
    def __init__(self):
        super().__init__("lost")

    def delete_asset(self, repository, asset_id):
        super().delete_asset(repository, asset_id)
        raise RuntimeError("delete response was lost after acceptance")


class _SplitInventoryClient:
    def __init__(self):
        self.tag_reads = 0
        self.id_assets = []
        self.tag_assets = [{"id": 7001, "name": "foreign.bin"}]
        self.uploads = []
        self.deletes = []

    def resolve_tag_commit(self, _repository, _tag):
        return "a" * 40

    def get_release_by_tag(self, _repository, _tag):
        self.tag_reads += 1
        assets = [] if self.tag_reads == 1 else list(self.tag_assets)
        return {"id": 4242, "tag_name": "v0.2.2", "assets": assets}

    def get_release(self, _repository, _release_id):
        return {"id": 4242, "tag_name": "v0.2.2", "assets": list(self.id_assets)}

    def upload_asset(self, _repository, _release_id, name, data, *, label=None, **_kwargs):
        self.uploads.append((name, data))
        owned = {
            "id": 9001,
            "name": name,
            "label": label,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "state": "uploaded",
        }
        self.id_assets.append(owned)
        self.tag_assets.append(owned)
        return dict(owned)

    def delete_asset(self, _repository, asset_id):
        self.deletes.append(asset_id)


class _ForeignSameNameFailureClient(_Client):
    """A concurrent actor creates the requested name while our POST fails."""

    def upload_asset(self, repository, release_id, name, data, **_kwargs):
        self.uploads.append((repository, release_id, name, data))
        self.release["assets"].append({"id": 7777, "name": name, "size": len(data), "state": "uploaded"})
        raise RuntimeError("upload failed before acceptance")


def test_upload_rejects_tag_drift_before_any_foreign_write(tmp_path):
    uploader = _load_uploader()
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"owned bytes")
    client = _Client(["b" * 40])

    with pytest.raises(RuntimeError, match="release tag identity changed"):
        uploader.upload_bound_assets(client, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [asset])

    assert client.uploads == []
    assert client.deletes == []


def test_upload_rolls_back_exact_asset_when_tag_moves_during_publication(tmp_path):
    uploader = _load_uploader()
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"owned bytes")
    client = _Client(["a" * 40, "b" * 40])

    with pytest.raises(RuntimeError, match="release tag identity changed"):
        uploader.upload_bound_assets(client, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [asset])

    assert client.uploads == [("dcc-mcp/dcc-mcp-3dsmax", 4242, "artifact.whl", b"owned bytes")]
    assert client.deletes == [("dcc-mcp/dcc-mcp-3dsmax", 9001)]


def test_upload_uses_exact_release_id_owned_bytes_and_no_clobber(tmp_path):
    uploader = _load_uploader()
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"owned bytes")
    client = _Client(["a" * 40])

    uploader.upload_bound_assets(client, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [asset])

    assert client.uploads == [("dcc-mcp/dcc-mcp-3dsmax", 4242, "artifact.whl", b"owned bytes")]
    assert client.deletes == []

    clobber = _Client(["a" * 40], existing_assets=["artifact.whl"])
    with pytest.raises(RuntimeError, match="already exists"):
        uploader.upload_bound_assets(clobber, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [asset])
    assert clobber.uploads == []


@pytest.mark.parametrize("response_mode", ["lost", "malformed"])
def test_upload_reconciles_and_retires_one_accepted_asset_after_uncertain_response(tmp_path, response_mode):
    uploader = _load_uploader()
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"owned bytes")
    client = _UncertainResponseClient(response_mode)

    with pytest.raises(RuntimeError):
        uploader.upload_bound_assets(client, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [asset])

    assert client.deletes == [("dcc-mcp/dcc-mcp-3dsmax", 9001)]
    assert client.release["assets"] == []


def test_upload_does_not_guess_when_uncertain_response_creates_ambiguous_assets(tmp_path):
    uploader = _load_uploader()
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"owned bytes")
    client = _UncertainResponseClient("lost", duplicate_remote_asset=True)

    with pytest.raises(RuntimeError, match="ownership.*uncertain"):
        uploader.upload_bound_assets(client, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [asset])

    assert client.deletes == []
    assert [item["id"] for item in client.release["assets"]] == [9001, 9002]


def test_upload_rebinds_a_plausible_response_to_the_exact_new_inventory_asset(tmp_path):
    uploader = _load_uploader()
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"owned bytes")
    later = tmp_path / "later.tar.gz"
    later.write_bytes(b"later bytes")
    client = _PlausibleForeignResponseClient()

    with pytest.raises(RuntimeError):
        uploader.upload_bound_assets(
            client,
            "dcc-mcp/dcc-mcp-3dsmax",
            "v0.2.2",
            "a" * 40,
            [asset, later],
        )

    assert client.deletes == [("dcc-mcp/dcc-mcp-3dsmax", 9001)]
    assert client.release["assets"] == [{"id": 7001, "name": "foreign.bin"}]


def test_cleanup_reconciles_an_accepted_delete_after_its_response_is_lost(tmp_path):
    uploader = _load_uploader()
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"owned bytes")
    client = _AcceptedCleanupLostResponseClient()

    with pytest.raises(RuntimeError, match="upload response was lost"):
        uploader.upload_bound_assets(client, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [asset])

    assert client.deletes == [("dcc-mcp/dcc-mcp-3dsmax", 9001)]
    assert client.release["assets"] == []


def test_upload_rejects_split_id_tag_prestate_before_any_write(tmp_path):
    uploader = _load_uploader()
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"exact bytes")
    client = _SplitInventoryClient()

    with pytest.raises(RuntimeError, match="ownership|inventory|identity"):
        uploader.upload_bound_assets(
            client,
            "dcc-mcp/dcc-mcp-3dsmax",
            "v0.2.2",
            "a" * 40,
            [artifact],
        )

    assert client.uploads == []
    assert all(asset["id"] != 9001 for asset in client.id_assets + client.tag_assets)


def test_upload_never_deletes_a_foreign_same_name_asset_after_failed_post(tmp_path):
    uploader = _load_uploader()
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"owned bytes")
    client = _ForeignSameNameFailureClient(["a" * 40])

    with pytest.raises(RuntimeError):
        uploader.upload_bound_assets(client, "dcc-mcp/dcc-mcp-3dsmax", "v0.2.2", "a" * 40, [artifact])

    assert client.deletes == []
    assert [asset["id"] for asset in client.release["assets"]] == [7777]


def test_upload_rejects_a_replaced_release_upload_url_before_post(tmp_path):
    uploader = _load_uploader()
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"owned bytes")
    client = uploader.GitHubClient("token")
    client.posts = []
    client.get_release = lambda _repository, _release_id: {
        "id": 4242,
        "tag_name": "v0.2.2",
        "upload_url": "https://uploads.example/foreign/assets{?name,label}",
    }
    client._request_json = lambda method, url, **_kwargs: client.posts.append((method, url)) or {}

    with pytest.raises(RuntimeError, match="upload URL|release identity|ownership"):
        client.upload_asset(
            "dcc-mcp/dcc-mcp-3dsmax",
            4242,
            artifact.name,
            artifact.read_bytes(),
            label="dcc-mcp-txn-test",
            expected_tag="v0.2.2",
            expected_upload_url="https://uploads.example/owned/assets{?name,label}",
            expected_assets=[],
        )

    assert client.posts == []


def test_upload_binds_label_and_expected_inventory_into_the_post_url():
    uploader = _load_uploader()
    client = uploader.GitHubClient("token")
    posts = []
    client.get_release = lambda _repository, _release_id: {
        "id": 4242,
        "tag_name": "v0.2.2",
        "upload_url": "https://uploads.example/owned/assets{?name,label}",
        "assets": [],
    }
    client._request_json = lambda method, url, **_kwargs: (
        posts.append((method, url))
        or {
            "id": 9001,
            "name": "artifact.whl",
        }
    )

    client.upload_asset(
        "dcc-mcp/dcc-mcp-3dsmax",
        4242,
        "artifact.whl",
        b"owned bytes",
        label="dcc-mcp-txn-test",
        expected_tag="v0.2.2",
        expected_upload_url="https://uploads.example/owned/assets{?name,label}",
        expected_assets=[],
    )

    assert posts == [
        (
            "POST",
            "https://uploads.example/owned/assets?name=artifact.whl&label=dcc-mcp-txn-test",
        )
    ]
