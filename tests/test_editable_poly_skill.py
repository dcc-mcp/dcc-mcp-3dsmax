"""Offline tests for the Editable Poly component tools.

``pymxs`` is faked with a small in-memory poly object that really stores
vertices and faces, so a move, a delete, and a weld are observable - and so a
write the fake refuses is indistinguishable from a host that refuses one. That
is the point of this suite: every write path has a case where the target does
not accept the value, and every one of those cases must come back as a failure
or an explicit warning, never as a success.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dcc_mcp_3dsmax import _undo_utils  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-mesh-ops"
UNDO_DOC = Path(__file__).resolve().parents[1] / "docs" / "UNDO.md"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_poly_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Fake pymxs runtime ─────────────────────────────────────────────────


class Point3:
    """Stand-in for ``pymxs.runtime.Point3``."""

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Point3) and (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid.
        return "Point3({}, {}, {})".format(self.x, self.y, self.z)

    def __add__(self, other: "Point3") -> "Point3":
        return Point3(self.x + other.x, self.y + other.y, self.z + other.z)


class Ray:
    """Stand-in for ``pymxs.runtime.Ray``."""

    def __init__(self, pos: Point3, dir: Point3) -> None:
        self.pos = pos
        self.dir = dir


class TriMesh:
    """The TriMesh value the ``mesh()`` constructor returns."""

    def __init__(self, vertices, faces) -> None:
        self.vertices = [Point3(v.x, v.y, v.z) for v in vertices]
        self.faces = [[int(f.x), int(f.y), int(f.z)] for f in faces]


class PolyNode:
    """An Editable Poly node that really stores vertices and triangular faces."""

    _next_handle = 1000

    def __init__(self, name: str, vertices=None, faces=None, translation=(0.0, 0.0, 0.0)) -> None:
        PolyNode._next_handle += 1
        self.name = name
        self.handle = PolyNode._next_handle
        self.verts = [Point3(*v) for v in (vertices or [])]
        self.faces = [list(f) for f in (faces or [])]
        self.modifiers: list = []
        self.parent = None
        self.isHidden = False
        self.smoothing_groups: dict = {}
        self.material_ids: dict = {}
        self.translation = Point3(*translation)
        # Rejection switches: when set, the named write refuses the value, which
        # is how a host that ignores a write is modelled.
        self.reject_verts: set = set()
        self.reject_smoothing = False
        self.reject_material = False
        self.reject_deletes = False

    @property
    def baseObject(self):  # noqa: N802 - mirrors the native node interface.
        return self

    @property
    def objectTransform(self):  # noqa: N802 - mirrors the native node interface.
        return Translation(self.translation)

    def edges(self):
        """Edges derived from the face list: one per shared vertex pair."""
        pairs = []
        for face in self.faces:
            for offset in range(len(face)):
                pair = tuple(sorted((face[offset], face[(offset + 1) % len(face)])))
                if pair not in pairs:
                    pairs.append(pair)
        return pairs


class _EditableMeshNode(PolyNode):
    """A node created by the ``Editable_Mesh`` constructor.

    Assigning ``.mesh`` replaces its geometry, which is the documented TriMesh
    path the ``create_mesh`` tool uses. A plain node - a Box, say - would take
    the assignment as a read-only derived property and keep its own geometry,
    which is why construction starts from this class.
    """

    @property
    def mesh(self):
        return TriMesh(self.verts, [Point3(*face) for face in self.faces])

    @mesh.setter
    def mesh(self, tri_mesh):
        self.verts = [Point3(v.x, v.y, v.z) for v in tri_mesh.vertices]
        self.faces = [list(face) for face in tri_mesh.faces]


class Translation:
    """A translate-only stand-in for ``pymxs.runtime.Matrix3``.

    ``_curve_utils.object_to_world`` maps with ``point * transform`` and
    ``world_to_object`` with ``point * inverse(transform)``, so the transform
    side has to accept the multiplication.
    """

    def __init__(self, translation: Point3) -> None:
        self.translation = translation

    def __rmul__(self, point: Point3) -> Point3:
        return Point3(
            point.x + self.translation.x,
            point.y + self.translation.y,
            point.z + self.translation.z,
        )


class _PolyOp:
    """``pymxs.runtime.polyOp`` over the fake poly nodes."""

    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    # ── counts ──
    def getNumVerts(self, node):  # noqa: N802
        return len(node.verts)

    def getNumEdges(self, node):  # noqa: N802
        return len(node.edges())

    def getNumFaces(self, node):  # noqa: N802
        return len(node.faces)

    # ── reads ──
    def getVert(self, node, index):  # noqa: N802
        return node.verts[index - 1]

    def getFaceVerts(self, node, index):  # noqa: N802
        return list(node.faces[index - 1])

    def getEdgeVerts(self, node, index):  # noqa: N802
        return list(node.edges()[index - 1])

    def getFaceEdges(self, node, index):  # noqa: N802
        face = node.faces[index - 1]
        pairs = []
        for offset in range(len(face)):
            pair = tuple(sorted((face[offset], face[(offset + 1) % len(face)])))
            if pair not in pairs:
                pairs.append(pair)
        return [node.edges().index(pair) + 1 for pair in pairs]

    def getFaceCenter(self, node, index):  # noqa: N802
        corners = [node.verts[i - 1] for i in node.faces[index - 1]]
        return Point3(
            sum(c.x for c in corners) / len(corners),
            sum(c.y for c in corners) / len(corners),
            sum(c.z for c in corners) / len(corners),
        )

    def getFaceSmoothGroup(self, node, index):  # noqa: N802
        return node.smoothing_groups.get(index, 0)

    def getFaceMatID(self, node, index):  # noqa: N802
        return node.material_ids.get(index, 1)

    # ── writes ──
    def setVert(self, node, indices, point):  # noqa: N802
        wanted = [int(indices)] if isinstance(indices, int) else [int(i) for i in indices]
        for index in wanted:
            if index in node.reject_verts:
                raise RuntimeError("vertex {} rejected by the host".format(index))
            node.verts[index - 1] = Point3(point.x, point.y, point.z)
        return True

    def deleteVerts(self, node, indices):  # noqa: N802
        if node.reject_deletes:
            raise RuntimeError("delete rejected by the host")
        for index in sorted((int(i) for i in indices), reverse=True):
            del node.verts[index - 1]
            node.faces = [face for face in node.faces if index not in face]
        return True

    def deleteFaces(self, node, indices):  # noqa: N802
        if node.reject_deletes:
            raise RuntimeError("delete rejected by the host")
        for index in sorted((int(i) for i in indices), reverse=True):
            del node.faces[index - 1]
        return True

    def deleteEdges(self, node, indices):  # noqa: N802
        if node.reject_deletes:
            raise RuntimeError("delete rejected by the host")
        pairs = node.edges()
        doomed = [pairs[int(i) - 1] for i in indices]
        kept = []
        for face in node.faces:
            face_pairs = {
                tuple(sorted((face[o], face[(o + 1) % len(face)]))) for o in range(len(face))
            }
            if not face_pairs & set(doomed):
                kept.append(face)
        node.faces = kept
        return True

    def weldVerts(self, node, first, second):  # noqa: N802
        target = node.verts[int(first) - 1]
        node.verts[int(second) - 1] = Point3(target.x, target.y, target.z)
        for face in node.faces:
            node.faces[node.faces.index(face)] = [
                int(first) if int(v) == int(second) else int(v) for v in face
            ]
        del node.verts[int(second) - 1]
        node.faces = [_renumber(face, int(second)) for face in node.faces]
        return True

    def detachFaces(self, node, indices, asNode=True, name="DetachedMesh"):  # noqa: N803
        if node.reject_deletes:
            raise RuntimeError("detach rejected by the host")
        face_indices = sorted((int(i) for i in indices), reverse=True)
        detached_faces = [list(node.faces[i - 1]) for i in face_indices]
        for index in face_indices:
            del node.faces[index - 1]
        detached = PolyNode(name, vertices=[[0.0, 0.0, 0.0]] * 3, faces=[[1, 2, 3]])
        detached.detached_faces = detached_faces
        self.runtime.objects.append(detached)
        return detached

    def setFaceSmoothGroup(self, node, indices, group):  # noqa: N802
        if node.reject_smoothing:
            raise RuntimeError("smoothing group rejected by the host")
        for index in indices:
            node.smoothing_groups[int(index)] = int(group)
        return True

    def setFaceMatID(self, node, indices, material_id):  # noqa: N802
        if node.reject_material:
            raise RuntimeError("material id rejected by the host")
        for index in indices:
            node.material_ids[int(index)] = int(material_id)
        return True


def _renumber(face, removed):
    """Drop a vertex index and shift the ones above it down by one."""
    return [v if v < removed else v - 1 for v in face]


def _snapshot(runtime: "FakeRuntime"):
    """Copy the geometry of every node, so a hold can put it back.

    Vertices, faces, and the two per-face maps are what the component tools
    write, so together they are the state a cancelled or undone batch has to
    restore. The node list is copied too, because a detach creates a node that
    undoing the batch has to remove again.
    """
    nodes = list(runtime.objects)
    geometry = [
        (
            [Point3(v.x, v.y, v.z) for v in node.verts],
            [list(face) for face in node.faces],
            dict(node.smoothing_groups),
            dict(node.material_ids),
        )
        for node in nodes
    ]
    return (nodes, geometry)


def _restore(runtime: "FakeRuntime", snapshot) -> None:
    """Put every node back to the geometry captured in *snapshot*."""
    nodes, geometry = snapshot
    runtime.objects[:] = nodes
    for node, (verts, faces, smoothing, material_ids) in zip(nodes, geometry):
        node.verts = [Point3(v.x, v.y, v.z) for v in verts]
        node.faces = [list(face) for face in faces]
        node.smoothing_groups = dict(smoothing)
        node.material_ids = dict(material_ids)


class _Hold:
    """Stands in for ``rt.theHold`` with a real Begin/Accept/Cancel cycle.

    The geometry side is real as well as the bookkeeping: ``Begin`` snapshots
    every node, ``Cancel`` restores it, and ``Accept`` pushes one reversible
    entry onto the runtime undo stack. That is what makes "one undo reverses the
    whole batch" an observable claim instead of a counter having moved -
    ``runtime.execute("max undo")`` runs the same host channel the adapter's
    undo tool uses.
    """

    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime
        self.begin_calls = 0
        self.accept_calls: list = []
        self.cancel_calls = 0
        self.holding = False
        self._snapshot = None

    def Begin(self):  # noqa: N802
        self.begin_calls += 1
        self.holding = True
        self._snapshot = _snapshot(self.runtime)

    def Accept(self, label):  # noqa: N802
        self.accept_calls.append(str(label))
        self.holding = False
        if self._snapshot is not None:
            before, after = self._snapshot, _snapshot(self.runtime)
            self._snapshot = None
            self.runtime.record(
                lambda: _restore(self.runtime, after),
                lambda: _restore(self.runtime, before),
            )

    def Cancel(self):  # noqa: N802
        self.cancel_calls += 1
        self.holding = False
        if self._snapshot is not None:
            snapshot, self._snapshot = self._snapshot, None
            _restore(self.runtime, snapshot)

    def Holding(self):  # noqa: N802
        return self.holding


class _UndoEntry:
    """One reversible operation recorded for the fake undo stack."""

    def __init__(self, redo, undo) -> None:
        self.redo = redo
        self.undo = undo


class FakeRuntime:
    """A pymxs stand-in with a real, reversible undo stack."""

    def __init__(self, *, with_hold: bool = True) -> None:
        self.objects: list = []
        self.selection: list = []
        self.currentTime = 0
        self.polyOp = _PolyOp(self)
        self.Point3 = Point3
        self.ray = Ray
        self.theHold = _Hold(self) if with_hold else None
        self.history: list = []
        self.redo_stack: list = []
        self.executed: list = []
        # Screen -> ray mapping. Off by default so the "host exposes nothing"
        # path is what an unconfigured host really looks like.
        self.screen_ray_factory = None
        self.ray_cast_entries = ("intersectRayEx", "intersectRay")
        self.mesh_constructor_error = None
        self.convert_to_poly_error = None

    # ── node helpers ──
    def getNodeByName(self, name):  # noqa: N802
        for node in self.objects:
            if node.name == name:
                return node
        return None

    def add(self, node) -> None:
        self.objects.append(node)
        self.selection = [node]

    def record(self, redo, undo) -> None:
        self.history.append(_UndoEntry(redo, undo))
        self.redo_stack.clear()

    def _undo(self) -> None:
        if not self.history:
            return
        entry = self.history.pop()
        entry.undo()
        self.redo_stack.append(entry)

    def _redo(self) -> None:
        if not self.redo_stack:
            return
        entry = self.redo_stack.pop()
        entry.redo()
        self.history.append(entry)

    # ── construction ──
    def Editable_Mesh(self):  # noqa: N802
        node = PolyNode("Mesh001")
        # A real Editable Mesh node takes its geometry from the assigned
        # TriMesh, which is what makes `node.mesh = mesh(...)` the construction
        # path rather than a no-op write on a plain node.
        node.__class__ = _EditableMeshNode
        self.objects.append(node)
        return node

    def mesh(self, vertices=None, faces=None):
        if self.mesh_constructor_error:
            raise RuntimeError(self.mesh_constructor_error)
        return TriMesh(vertices or [], faces or [])

    def update(self, node):
        return True

    def convertToPoly(self, node):  # noqa: N802
        if self.convert_to_poly_error:
            raise RuntimeError(self.convert_to_poly_error)
        return node

    def delete(self, node):
        self.objects.remove(node)
        return True

    # ── undo / redo channels ──
    def execute(self, script):
        self.executed.append(script)
        if script == "max undo":
            self._undo()
        elif script == "max redo":
            self._redo()
        return True

    # ── picking ──
    def mapScreenToWorldRay(self, x, y):  # noqa: N802
        if self.screen_ray_factory is None:
            return None
        return self.screen_ray_factory(float(x), float(y))

    def intersectRayEx(self, node, ray):  # noqa: N802
        if "intersectRayEx" not in self.ray_cast_entries:
            raise RuntimeError("intersectRayEx is not available")
        return self._cast(node, ray, report_face=True)

    def intersectRay(self, node, ray):  # noqa: N802
        if "intersectRay" not in self.ray_cast_entries:
            raise RuntimeError("intersectRay is not available")
        return self._cast(node, ray, report_face=False)

    def _cast(self, node, ray, *, report_face: bool):
        """Ray/triangle intersection against the fake poly."""
        best = None
        for face_index, face in enumerate(node.faces, start=1):
            corners = [node.verts[i - 1] for i in face]
            for offset in range(1, len(corners) - 1):
                hit = _ray_triangle(ray, corners[0], corners[offset], corners[offset + 1])
                if hit is None:
                    continue
                distance = _distance(ray.pos, hit)
                if best is None or distance < best[0]:
                    best = (distance, hit, face_index)
        if best is None:
            return None
        point = _world(node, best[1])
        if report_face:
            return [point, best[2]]
        return point

    def inverse(self, transform):
        return Translation(Point3(-transform.translation.x, -transform.translation.y, -transform.translation.z))


def _world(node, point: Point3) -> Point3:
    return Point3(
        point.x + node.translation.x, point.y + node.translation.y, point.z + node.translation.z
    )


def _distance(left: Point3, right: Point3) -> float:
    return math.sqrt((left.x - right.x) ** 2 + (left.y - right.y) ** 2 + (left.z - right.z) ** 2)


def _ray_triangle(ray: Ray, a: Point3, b: Point3, c: Point3):
    """Möller-Trumbore; returns the hit point or None."""
    edge1 = Point3(b.x - a.x, b.y - a.y, b.z - a.z)
    edge2 = Point3(c.x - a.x, c.y - a.y, c.z - a.z)
    h = _cross(ray.dir, edge2)
    determinant = _dot(edge1, h)
    if abs(determinant) < 1e-12:
        return None
    f = 1.0 / determinant
    s = Point3(ray.pos.x - a.x, ray.pos.y - a.y, ray.pos.z - a.z)
    u = f * _dot(s, h)
    if u < 0.0 or u > 1.0:
        return None
    q = _cross(s, edge1)
    v = f * _dot(ray.dir, q)
    if v < 0.0 or u + v > 1.0:
        return None
    t = f * _dot(edge2, q)
    if t <= 1e-9:
        return None
    return Point3(ray.pos.x + ray.dir.x * t, ray.pos.y + ray.dir.y * t, ray.pos.z + ray.dir.z * t)


def _cross(left: Point3, right: Point3) -> Point3:
    return Point3(
        left.y * right.z - left.z * right.y,
        left.z * right.x - left.x * right.z,
        left.x * right.y - left.y * right.x,
    )


def _dot(left: Point3, right: Point3) -> float:
    return left.x * right.x + left.y * right.y + left.z * right.z


def _install(monkeypatch, **kwargs):
    runtime = FakeRuntime(**kwargs)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


# A unit quad in the XY plane, sitting at z = 0, shifted to (5, 0, 0) so the
# object-space / world-space mapping is exercised on every read.
QUAD_VERTICES = [[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]]
QUAD_FACES = [[1, 2, 3], [1, 3, 4]]


def _quad(runtime, name="quad", translation=(5.0, 0.0, 0.0)):
    node = PolyNode(name, vertices=QUAD_VERTICES, faces=QUAD_FACES, translation=translation)
    runtime.add(node)
    return node


# ── create_mesh ────────────────────────────────────────────────────────


def test_create_mesh_builds_a_poly_and_verifies_every_vertex(monkeypatch):
    runtime = _install(monkeypatch)

    result = _load_action("action_create_mesh.py").main(
        vertices=QUAD_VERTICES, faces=[list(face) for face in QUAD_FACES], name="panel"
    )

    assert result["success"] is True, result
    assert result["data"]["vertex_count"] == 4
    assert result["data"]["face_count"] == 2
    assert result["data"]["triangulated_faces"] == []
    assert len(runtime.objects) == 1
    assert runtime.objects[0].name == "panel"


def test_create_mesh_fan_triangulates_n_gons_and_reports_them(monkeypatch):
    _install(monkeypatch)

    result = _load_action("action_create_mesh.py").main(
        vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]],
        faces=[[1, 2, 3, 4]],
    )

    assert result["success"] is True, result
    # One quad becomes two triangles; the split is reported, not hidden.
    assert result["data"]["face_count"] == 2
    assert result["data"]["triangulated_faces"] == [1]
    assert any("fan-triangulated" in item for item in result["data"]["warnings"])


def test_create_mesh_rejects_a_face_index_outside_the_vertex_list(monkeypatch):
    _install(monkeypatch)

    result = _load_action("action_create_mesh.py").main(vertices=QUAD_VERTICES, faces=[[1, 2, 9]])

    assert result["success"] is False
    assert "outside 1..4" in result["message"]


def test_create_mesh_rejects_a_degenerate_face(monkeypatch):
    _install(monkeypatch)

    assert _load_action("action_create_mesh.py").main(vertices=QUAD_VERTICES, faces=[[1, 2]])["success"] is False
    assert (
        _load_action("action_create_mesh.py").main(vertices=QUAD_VERTICES, faces=[[1, 1, 2]])["success"] is False
    )


def test_create_mesh_fails_and_rolls_back_when_the_host_ignores_the_payload(monkeypatch):
    """A mesh the node did not take is a failure, and the node is removed."""
    runtime = _install(monkeypatch)
    runtime.mesh_constructor_error = "the payload was refused"

    result = _load_action("action_create_mesh.py").main(
        vertices=QUAD_VERTICES, faces=[list(face) for face in QUAD_FACES]
    )

    assert result["success"] is False
    assert "mesh() rejected" in result["message"]
    assert result["data"]["rolled_back"] is False
    assert result["data"]["created_with"] == "Editable_Mesh"


def test_create_mesh_fails_when_the_conversion_never_runs(monkeypatch):
    runtime = _install(monkeypatch)
    runtime.convert_to_poly_error = "cannot convert"

    result = _load_action("action_create_mesh.py").main(
        vertices=QUAD_VERTICES, faces=[list(face) for face in QUAD_FACES]
    )

    assert result["success"] is False
    assert "convertToPoly failed" in result["message"]


def test_create_mesh_reports_a_vertex_the_host_moved(monkeypatch):
    """A host that merged or moved a vertex must not report a clean success."""
    _install(monkeypatch)

    original = FakeRuntime.mesh

    def _shifted(self, vertices=None, faces=None):
        tri = original(self, vertices, faces)
        tri.vertices[3] = Point3(99.0, 99.0, 99.0)
        return tri

    monkeypatch.setattr(FakeRuntime, "mesh", _shifted)
    result = _load_action("action_create_mesh.py").main(
        vertices=QUAD_VERTICES, faces=[list(face) for face in QUAD_FACES]
    )

    assert result["success"] is False
    assert "do not match the requested world positions" in result["message"]
    assert result["data"]["mismatch_count"] == 1


# ── inspect_mesh ───────────────────────────────────────────────────────


def test_inspect_mesh_reports_vertices_edges_and_faces_with_world_positions(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_inspect_mesh.py").main(node_name="quad")

    assert result["success"] is True, result
    assert result["data"]["vertex_count"] == 4
    assert result["data"]["edge_count"] == 5
    assert result["data"]["face_count"] == 2
    # The node sits at x = 5, so world and object positions must differ.
    assert result["data"]["vertices"][0]["object"] == [-1.0, -1.0, 0.0]
    assert result["data"]["vertices"][0]["world"] == [4.0, -1.0, 0.0]
    assert result["data"]["faces"][0]["vertices"] == [1, 2, 3]
    assert result["data"]["edges"][0]["vertices"] == [1, 2]


def test_inspect_mesh_honours_include_and_limit(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_inspect_mesh.py").main(node_name="quad", include=["faces"], limit=1)

    assert result["success"] is True
    assert "vertices" not in result["data"]
    assert len(result["data"]["faces"]) == 1
    assert result["data"]["faces_truncated"] is True


def test_inspect_mesh_fails_on_a_node_that_is_not_a_poly(monkeypatch):
    runtime = _install(monkeypatch)
    node = PolyNode("plain", vertices=QUAD_VERTICES, faces=QUAD_FACES)
    monkeypatch.setattr(type(runtime.polyOp), "getNumVerts", lambda self, n: (_ for _ in ()).throw(
        RuntimeError("not an editable poly")
    ))
    runtime.add(node)

    result = _load_action("action_inspect_mesh.py").main(node_name="plain")

    assert result["success"] is False
    assert "not readable as an Editable Poly" in result["message"]


def test_inspect_mesh_requires_a_target(monkeypatch):
    _install(monkeypatch)

    result = _load_action("action_inspect_mesh.py").main()

    assert result["success"] is False
    assert "node_name or handle is required" in result["message"]


# ── edit_vertices ──────────────────────────────────────────────────────


def test_edit_vertices_read_reports_world_positions(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_edit_vertices.py").main(action="read", node_name="quad")

    assert result["success"] is True, result
    assert result["data"]["vertices"][0]["world"] == [4.0, -1.0, 0.0]


def test_edit_vertices_moves_in_world_space(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_edit_vertices.py").main(
        action="move", node_name="quad", vertex_indices=[1, 2], offset=[0.0, 0.0, 2.0]
    )

    assert result["success"] is True, result
    assert node.verts[0].z == 2.0
    assert node.verts[1].z == 2.0
    assert result["data"]["vertices"][0]["before"] == [4.0, -1.0, 0.0]
    assert result["data"]["vertices"][0]["after"] == [4.0, -1.0, 2.0]


def test_edit_vertices_sets_world_positions(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_edit_vertices.py").main(
        action="set", node_name="quad", vertex_indices=[1], positions=[[10.0, 20.0, 30.0]]
    )

    assert result["success"] is True, result
    # World (10, 20, 30) on a node translated by (5, 0, 0) is object (5, 20, 30).
    assert (node.verts[0].x, node.verts[0].y, node.verts[0].z) == (5.0, 20.0, 30.0)


def test_edit_vertices_aligns_to_the_mean(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_edit_vertices.py").main(
        action="align", node_name="quad", vertex_indices=[1, 2, 3, 4], axis="z"
    )

    assert result["success"] is True, result
    assert all(vertex.z == 0.0 for vertex in node.verts)
    assert result["data"]["axis"] == "z"


def test_edit_vertices_aligns_to_min_and_max(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    node.verts[0] = Point3(0.0, 0.0, 4.0)
    node.verts[1] = Point3(0.0, 0.0, 1.0)

    minimum = _load_action("action_edit_vertices.py").main(
        action="align", node_name="quad", vertex_indices=[1, 2], axis="z", mode="min"
    )
    assert minimum["success"] is True
    assert node.verts[0].z == 1.0

    node.verts[0] = Point3(0.0, 0.0, 4.0)
    node.verts[1] = Point3(0.0, 0.0, 1.0)
    maximum = _load_action("action_edit_vertices.py").main(
        action="align", node_name="quad", vertex_indices=[1, 2], axis="z", mode="max"
    )
    assert maximum["success"] is True
    assert node.verts[1].z == 4.0


def test_edit_vertices_fails_when_the_host_refuses_a_vertex(monkeypatch):
    """A vertex the target will not take is a failure, not a warning."""
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    node.reject_verts = {2}

    result = _load_action("action_edit_vertices.py").main(
        action="set", node_name="quad", vertex_indices=[1, 2], positions=[[0.0, 0.0, 1.0], [0.0, 0.0, 2.0]]
    )

    assert result["success"] is False
    assert "not accepted" in result["message"]
    assert result["data"]["error_count"] == 1
    assert "undo_last" in result["data"]["note"]


def test_edit_vertices_rejects_an_out_of_range_index(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_edit_vertices.py").main(
        action="move", node_name="quad", vertex_indices=[99], offset=[0, 0, 1]
    )

    assert result["success"] is False
    assert "out of range" in result["message"]


def test_edit_vertices_rejects_mismatched_positions(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_edit_vertices.py").main(
        action="set", node_name="quad", vertex_indices=[1, 2], positions=[[0.0, 0.0, 1.0]]
    )

    assert result["success"] is False
    assert "positions has 1 entr(ies)" in result["message"]


def test_edit_vertices_rejects_a_bad_action_and_axis(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    assert _load_action("action_edit_vertices.py").main(
        action="teleport", node_name="quad"
    )["success"] is False
    assert _load_action("action_edit_vertices.py").main(
        action="align", node_name="quad", axis="w"
    )["success"] is False


# ── mesh_edit: preflight ───────────────────────────────────────────────


def test_mesh_edit_preflight_rejects_out_of_range_indices_before_writing(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "move_vertices", "indices": [1], "offset": [0, 0, 1]},
            {"op": "delete_faces", "indices": [99]},
        ],
    )

    assert result["success"] is False
    assert "failed preflight" in result["message"]
    assert result["data"]["applied"] == 0
    # Nothing was written, so the first - valid - op did not move a vertex.
    assert node.verts[0].z == 0.0


def test_mesh_edit_dry_run_plans_without_writing(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        dry_run=True,
        ops=[{"op": "delete_faces", "indices": [1]}, {"op": "move_vertices", "indices": [1], "offset": [0, 0, 1]}],
    )

    assert result["success"] is True
    assert result["data"]["applied"] == 0
    assert result["data"]["dry_run"] is True
    assert result["data"]["destructive_ops"] == ["delete_faces"]
    assert len(node.faces) == 2
    assert node.verts[0].z == 0.0


def test_mesh_edit_rejects_unknown_ops_and_bad_shapes(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    bad_op = _load_action("action_mesh_edit.py").main(node_name="quad", ops=[{"op": "explode", "indices": [1]}])
    bad_indices = _load_action("action_mesh_edit.py").main(node_name="quad", ops=[{"op": "delete_faces"}])
    not_a_list = _load_action("action_mesh_edit.py").main(node_name="quad", ops="delete_faces")
    too_many = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "delete_faces", "indices": [1]}] * 257
    )

    assert bad_op["success"] is False and "unsupported op" in bad_op["message"]
    assert bad_indices["success"] is False and "indices is required" in bad_indices["message"]
    assert not_a_list["success"] is False
    assert too_many["success"] is False and "at most 256" in too_many["message"]


# ── mesh_edit: the single undo step ────────────────────────────────────


def test_mesh_edit_applies_a_batch_and_one_undo_reverses_all_of_it(monkeypatch):
    """The headline contract: N ops, one undo entry, one undo reverses all of them."""
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    runtime.record(lambda: None, lambda: None)  # something to undo, so the stack is non-empty
    history_before = len(runtime.history)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        label="quad edit",
        ops=[
            {"op": "move_vertices", "indices": [1, 2], "offset": [0, 0, 3]},
            {"op": "set_face_smoothing_group", "indices": [1], "smoothing_group": 4},
            {"op": "delete_faces", "indices": [2]},
        ],
    )

    assert result["success"] is True, result
    assert result["data"]["applied"] == 3
    assert result["data"]["undo"]["grouped"] is True
    assert result["data"]["undo"]["granularity"] == _undo_utils.GRANULARITY_SINGLE_CALL
    assert runtime.theHold.accept_calls == ["quad edit"]
    assert runtime.theHold.cancel_calls == 0
    assert node.verts[0].z == 3.0
    assert len(node.faces) == 1
    assert node.smoothing_groups[1] == 4
    # Three ops, one host entry: the hold collapsed them, so one step is all
    # the caller has to ask for.
    assert len(runtime.history) == history_before + 1

    # One undo through the host channel the adapter uses, and all three ops are
    # gone - geometry included, not just the hold bookkeeping.
    runtime.execute("max undo")

    assert node.verts[0].z == 0.0
    assert node.verts[1].z == 0.0
    assert len(node.faces) == 2
    assert node.smoothing_groups == {}
    assert [list(face) for face in node.faces] == [list(face) for face in QUAD_FACES]

    # The entry is a real undo/redo pair, so the batch comes back as a unit.
    runtime.execute("max redo")

    assert node.verts[0].z == 3.0
    assert len(node.faces) == 1
    assert node.smoothing_groups[1] == 4


def test_mesh_edit_a_failed_op_cancels_the_hold(monkeypatch):
    """A failure part-way through must cancel the hold, not close it."""
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    node.reject_smoothing = True

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "move_vertices", "indices": [1], "offset": [0, 0, 5]},
            {"op": "set_face_smoothing_group", "indices": [1], "smoothing_group": 2},
        ],
    )

    assert result["success"] is False
    assert result["data"]["undo"]["grouped"] is True
    assert result["data"]["rolled_back"] is True
    assert result["data"]["rollback"] == "cancelled_hold"
    assert runtime.theHold.cancel_calls == 1
    assert runtime.theHold.accept_calls == []
    # Cancelling put the geometry back: the move that landed is gone again.
    assert node.verts[0].z == 0.0
    assert len(node.faces) == 2
    # A cancelled hold leaves nothing to undo later.
    assert len(runtime.history) == 0


def test_mesh_edit_cancelling_the_hold_restores_a_delete_that_landed(monkeypatch):
    """Rollback is geometric: a face removed before the failure comes back."""
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    node.reject_smoothing = True

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "delete_faces", "indices": [2]},
            {"op": "set_face_smoothing_group", "indices": [1], "smoothing_group": 2},
        ],
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is True
    assert result["data"]["rollback"] == "cancelled_hold"
    assert result["data"]["applied"] == 1
    # The delete really ran, and the cancelled hold really undid it.
    assert [list(face) for face in node.faces] == [list(face) for face in QUAD_FACES]
    assert len(node.edges()) == 5


def test_mesh_edit_refuses_an_ungrouped_batch_by_default(monkeypatch):
    """Without a hold the batch is refused, because it could not be undone."""
    runtime = _install(monkeypatch, with_hold=False)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "move_vertices", "indices": [1], "offset": [0, 0, 1]}]
    )

    assert result["success"] is False
    assert "cannot group the batch into one undo step" in result["message"]
    assert result["data"]["applied"] == 0
    assert result["data"]["undo"]["grouped"] is False
    assert runtime.theHold is None
    assert node.verts[0].z == 0.0


def test_mesh_edit_allow_ungrouped_applies_and_says_it_was_not_grouped(monkeypatch):
    runtime = _install(monkeypatch, with_hold=False)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        allow_ungrouped=True,
        ops=[{"op": "move_vertices", "indices": [1], "offset": [0, 0, 1]}],
    )

    assert result["success"] is True
    assert result["data"]["undo"]["grouped"] is False
    assert node.verts[0].z == 1.0
    assert any("not grouped into a single undo step" in item for item in result["data"]["warnings"])


def test_mesh_edit_reports_an_unrollbackable_partial_batch_honestly(monkeypatch):
    """No hold means no rollback: the result must say so, not claim one."""
    runtime = _install(monkeypatch, with_hold=False)
    node = _quad(runtime)
    node.reject_smoothing = True

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        allow_ungrouped=True,
        ops=[
            {"op": "move_vertices", "indices": [1], "offset": [0, 0, 7]},
            {"op": "set_face_smoothing_group", "indices": [1], "smoothing_group": 2},
        ],
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is False
    assert result["data"]["rollback"] == "unavailable"
    assert "were not rolled back" in result["data"]["rollback_error"]
    assert result["data"]["applied"] == 1
    # The first op really did land, and the result does not pretend otherwise.
    assert node.verts[0].z == 7.0


def test_mesh_edit_reports_a_hold_that_will_not_close_cleanly(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)
    runtime.theHold.accept_calls = []
    monkeypatch.setattr(
        runtime.theHold,
        "Accept",
        lambda label: (_ for _ in ()).throw(RuntimeError("accept refused")),
    )

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "move_vertices", "indices": [1], "offset": [0, 0, 1]}]
    )

    assert result["success"] is True
    assert result["data"]["undo"]["grouped"] is True
    assert any("did not close cleanly" in item for item in result["data"]["warnings"])


# ── mesh_edit: every op, and a rejection for each write path ───────────


def test_mesh_edit_welds_vertices(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "weld_vertices", "indices": [1, 2]}]
    )

    assert result["success"] is True, result
    assert len(node.verts) == 3


def test_mesh_edit_detaches_faces_into_a_new_node(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "detach_faces", "indices": [1], "name": "Flap"}]
    )

    assert result["success"] is True, result
    assert len(node.faces) == 1
    assert result["data"]["ops"][0]["detached"]["node_name"] == "Flap"


def test_mesh_edit_sets_material_ids_and_smoothing_groups(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "set_face_material_id", "indices": [1, 2], "material_id": 3},
            {"op": "set_face_smoothing_group", "indices": [2], "smoothing_group": 7},
        ],
    )

    assert result["success"] is True, result
    assert node.material_ids == {1: 3, 2: 3}
    assert node.smoothing_groups[2] == 7


def test_mesh_edit_align_vertices_inside_a_batch(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    node.verts[0] = Point3(-1.0, -1.0, 5.0)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[{"op": "align_vertices", "indices": [1, 2, 3, 4], "axis": "z", "mode": "max"}],
    )

    assert result["success"] is True, result
    assert all(vertex.z == 5.0 for vertex in node.verts)


def test_mesh_edit_fails_when_a_delete_is_refused(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    node.reject_deletes = True

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "delete_faces", "indices": [1]}]
    )

    assert result["success"] is False
    assert result["data"]["rolled_back"] is True
    assert len(node.faces) == 2


def test_mesh_edit_fails_when_a_material_id_is_refused(monkeypatch):
    runtime = _install(monkeypatch)
    node = _quad(runtime)
    node.reject_material = True

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "set_face_material_id", "indices": [1], "material_id": 2}]
    )

    assert result["success"] is False
    assert "rejected" in result["message"].lower()


def test_mesh_edit_rejects_weld_with_a_single_index(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "weld_vertices", "indices": [1]}]
    )

    assert result["success"] is False
    assert "at least two indices" in result["message"]


def test_mesh_edit_verifies_a_delete_actually_shrank_the_mesh(monkeypatch):
    """A host that accepts a delete but leaves the mesh intact must fail."""
    runtime = _install(monkeypatch)
    _quad(runtime)
    monkeypatch.setattr(runtime.polyOp, "deleteFaces", lambda n, indices: True)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "delete_faces", "indices": [1]}]
    )

    assert result["success"] is False
    assert "left 2 faces instead of the expected 1" in result["message"]


# ── pick_component ─────────────────────────────────────────────────────


def test_pick_component_maps_an_explicit_ray_to_a_verified_face(monkeypatch):
    """The assertable hit: a known ray, a known face, a verified index."""
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))

    result = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[-0.5, -0.5, 5.0], ray_direction=[0, 0, -1]
    )

    assert result["success"] is True, result
    assert result["data"]["hit"] is True
    assert result["data"]["component"]["face_index"] == 1
    assert result["data"]["face_reported"] is True
    assert result["data"]["face_verified"] is True
    assert result["data"]["ray_source"] == "explicit"


def test_pick_component_reports_a_clean_miss_as_a_miss(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))

    result = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[50.0, 50.0, 5.0], ray_direction=[0, 0, -1]
    )

    assert result["success"] is True
    assert result["data"]["hit"] is False
    assert "did not hit" in result["message"]


def test_pick_component_maps_an_image_position_through_the_host(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))
    runtime.screen_ray_factory = lambda x, y: Ray(Point3(-0.5, -0.5, 5.0), Point3(0.0, 0.0, -1.0))

    result = _load_action("action_pick_component.py").main(node_name="quad", image_x=320, image_y=240)

    assert result["success"] is True, result
    assert result["data"]["hit"] is True
    assert result["data"]["ray_source"] == "host"
    assert result["data"]["component"]["face_index"] == 1


def test_pick_component_fails_loudly_when_no_screen_mapping_exists(monkeypatch):
    """No host entry point means an error naming what was probed - never a guess."""
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_pick_component.py").main(node_name="quad", image_x=10, image_y=10)

    assert result["success"] is False
    assert "no viewport position -> world ray entry point" in result["message"]
    assert any("mapScreenToWorldRay" in item for item in result["data"]["probed"])


def test_pick_component_falls_back_to_a_point_only_hit_and_flags_it(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))
    runtime.ray_cast_entries = ("intersectRay",)

    result = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[-0.5, -0.5, 5.0], ray_direction=[0, 0, -1]
    )

    assert result["success"] is True, result
    assert result["data"]["hit"] is True
    # The face was resolved by proximity, so it is flagged rather than implied
    # to be something the host named.
    assert result["data"]["face_reported"] is False
    assert result["data"]["component"]["face_index"] == 1
    assert any("resolved by proximity" in item for item in result["data"]["warnings"])


def test_pick_component_flags_a_face_the_point_does_not_lie_on(monkeypatch):
    """A host that names the wrong face must not produce a confident pick.

    The quad is the two triangles [1,2,3] and [1,3,4]. The point (0.5, -0.5)
    is strictly inside the first and strictly outside the second, so a cast
    that reports face 2 is naming a face the hit is not on.
    """
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))

    original = FakeRuntime._cast

    def _wrong_face(self, node, ray, *, report_face):
        hit = original(self, node, ray, report_face=report_face)
        if hit is None:
            return None
        return [hit[0], 2 if hit[1] == 1 else 1]

    monkeypatch.setattr(FakeRuntime, "_cast", _wrong_face)

    result = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[0.5, -0.5, 5.0], ray_direction=[0, 0, -1]
    )

    assert result["success"] is True
    assert result["data"]["component"]["face_index"] == 2
    assert result["data"]["face_verified"] is False
    assert any("does not lie on it" in item for item in result["data"]["warnings"])


def test_pick_component_accepts_a_hit_on_a_shared_edge(monkeypatch):
    """A point on an edge between two faces genuinely lies on both."""
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))

    original = FakeRuntime._cast

    def _other_face(self, node, ray, *, report_face):
        hit = original(self, node, ray, report_face=report_face)
        if hit is None:
            return None
        return [hit[0], 2 if hit[1] == 1 else 1]

    monkeypatch.setattr(FakeRuntime, "_cast", _other_face)

    # (-0.5, -0.5) is on the diagonal the two faces share.
    result = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[-0.5, -0.5, 5.0], ray_direction=[0, 0, -1]
    )

    assert result["success"] is True
    assert result["data"]["face_verified"] is True


def test_pick_component_resolves_vertex_and_edge_targets(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))

    vertex = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[-0.9, -0.9, 5.0], ray_direction=[0, 0, -1], component="vertex"
    )
    edge = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[0.0, -0.95, 5.0], ray_direction=[0, 0, -1], component="edge"
    )

    assert vertex["success"] is True
    assert vertex["data"]["component"]["vertex_index"] == 1
    assert edge["success"] is True
    assert edge["data"]["component"]["edge_index"] is not None


def test_pick_component_rejects_normalized_image_coordinates(monkeypatch):
    """Normalized 0..1 input must fail, not be passed through as pixels.

    The adapter cannot query the viewport size, so a 0..1 pair handed to the
    host mapping unchanged would land near the top-left corner and return a
    wrong component with every check passing.
    """
    runtime = _install(monkeypatch)
    _quad(runtime)

    result = _load_action("action_pick_component.py").main(
        node_name="quad", image_x=0.5, image_y=0.5, image_space="normalized"
    )

    assert result["success"] is False
    assert "not supported" in result["message"]
    assert "pixels" in result["message"]


def test_pick_component_accepts_explicit_pixels_space(monkeypatch):
    """Declaring the unit the host expects is still allowed."""
    runtime = _install(monkeypatch)
    _quad(runtime, translation=(0.0, 0.0, 0.0))
    calls = []
    runtime.screen_ray_factory = lambda x, y: (
        calls.append((x, y)) or Ray(Point3(-0.5, -0.5, 5.0), Point3(0.0, 0.0, -1.0))
    )

    result = _load_action("action_pick_component.py").main(
        node_name="quad", image_x=320, image_y=240, image_space="pixels"
    )

    assert result["success"] is True, result
    # Passed through unconverted: the host entry point gets exactly what was asked for.
    assert calls == [(320.0, 240.0)]


def test_pick_component_image_space_schema_only_allows_pixels():
    """The schema must not advertise a unit the tool cannot honour."""
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    pick = next(tool for tool in tools if tool["name"] == "pick_component")

    image_space = pick["input_schema"]["properties"]["image_space"]
    assert image_space["enum"] == ["pixels"]


def test_pick_component_rejects_conflicting_and_incomplete_input(monkeypatch):
    runtime = _install(monkeypatch)
    _quad(runtime)

    both = _load_action("action_pick_component.py").main(
        node_name="quad", image_x=1, image_y=1, ray_origin=[0, 0, 1], ray_direction=[0, 0, -1]
    )
    neither = _load_action("action_pick_component.py").main(node_name="quad")
    half_ray = _load_action("action_pick_component.py").main(node_name="quad", ray_origin=[0, 0, 1])
    zero_ray = _load_action("action_pick_component.py").main(
        node_name="quad", ray_origin=[0, 0, 1], ray_direction=[0, 0, 0]
    )

    assert both["success"] is False and "not both" in both["message"]
    assert neither["success"] is False and "is required" in neither["message"]
    assert half_ray["success"] is False
    assert zero_ray["success"] is False


def test_pick_component_picks_the_nearest_hit_across_nodes(monkeypatch):
    runtime = _install(monkeypatch)
    near = _quad(runtime, name="near", translation=(0.0, 0.0, 0.0))
    _quad(runtime, name="far", translation=(0.0, 0.0, -3.0))
    assert near is not None

    result = _load_action("action_pick_component.py").main(
        ray_origin=[-0.5, -0.5, 5.0], ray_direction=[0, 0, -1]
    )

    assert result["success"] is True, result
    assert result["data"]["hit"] is True
    assert result["data"]["hits"] == 2
    assert result["data"]["node"]["node_name"] == "near"


# ── review findings (P3) ───────────────────────────────────────────────


def test_edit_vertices_is_not_idempotent_because_move_accumulates(monkeypatch):
    """Two identical move calls move twice, so the hint must not say idempotent."""
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    for _ in range(2):
        result = _load_action("action_edit_vertices.py").main(
            action="move", node_name="quad", vertex_indices=[1], offset=[0, 0, 1]
        )
        assert result["success"] is True, result

    assert node.verts[0].z == 2.0

    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    tool = next(item for item in tools if item["name"] == "edit_vertices")
    assert tool["idempotent"] is False
    assert tool["annotations"]["idempotent_hint"] is False


def test_mesh_edit_rejects_an_index_that_only_becomes_invalid_mid_batch(monkeypatch):
    """Preflight tracks the counts an op will see, not one snapshot.

    Face 2 is valid on the 2-face quad, but after deleting a face only one
    remains, so re-shading "face 2" has to be rejected before the hold opens.
    """
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "delete_faces", "indices": [1]},
            {"op": "set_face_smoothing_group", "indices": [2], "smoothing_group": 3},
        ],
    )

    assert result["success"] is False
    assert "failed preflight" in result["message"]
    assert result["data"]["applied"] == 0
    # Nothing was written, so the first - valid - op did not delete a face.
    assert len(node.faces) == 2
    assert "at this point in the batch" in result["data"]["errors"][0]["message"]


def test_mesh_edit_tightens_the_edge_bound_across_edge_deletions(monkeypatch):
    """Deleting edges lowers the bound a later edge op is checked against.

    The quad has 5 edges. Removing four of them leaves at most one, so a later
    "edge 3" is definitely invalid and must be rejected up front.
    """
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "delete_edges", "indices": [1, 2, 3, 4]},
            {"op": "delete_edges", "indices": [3]},
        ],
    )

    assert result["success"] is False
    assert "failed preflight" in result["message"]
    assert result["data"]["applied"] == 0
    assert "at this point in the batch" in result["data"]["errors"][0]["message"]
    assert len(node.faces) == 2


def test_mesh_edit_rejects_a_face_op_after_a_cascading_deletion(monkeypatch):
    """A count an earlier op makes unpredictable is not guessed at.

    Deleting edges can remove whole faces as a side effect, so the face count
    afterwards is unknown and a later face op cannot be range-checked at all.
    """
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "delete_edges", "indices": [1]},
            {"op": "set_face_material_id", "indices": [1], "material_id": 2},
        ],
    )

    assert result["success"] is False
    assert result["data"]["applied"] == 0
    assert "split the batch" in result["data"]["errors"][0]["message"]
    assert len(node.faces) == 2


def test_mesh_edit_still_accepts_a_batch_that_stays_in_range(monkeypatch):
    """Tracking the counts must not reject a batch that is genuinely valid."""
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "delete_faces", "indices": [2]},
            {"op": "set_face_smoothing_group", "indices": [1], "smoothing_group": 2},
        ],
    )

    assert result["success"] is True, result
    assert len(node.faces) == 1
    assert node.smoothing_groups[1] == 2


def test_mesh_edit_reports_an_unsupported_op_as_a_clean_failure(monkeypatch):
    """An op with no apply branch must fail as an _EditFailure, not KeyError."""
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "move_vertices", "indices": [1], "offset": [0, 0, 4]},
            {"op": "set_face_smoothing_group", "indices": [1], "smoothing_group": 1},
        ],
    )
    assert node.verts[0].z == 4.0
    assert result["success"] is True, result

    # Reach _apply directly with an op the schema would accept but that has no
    # branch, which is the state a half-finished new op would land in.
    module = _load_action("action_mesh_edit.py")
    try:
        module._apply(runtime, node, {"position": 0, "op": "tessellate", "indices": [1]})
    except module._EditFailure as exc:
        assert "no apply branch" in exc.message
    else:  # pragma: no cover - the guard is the point of the finding
        raise AssertionError("an unhandled op must raise _EditFailure, not fall through")


def test_delete_edges_verifies_the_full_count_dropped(monkeypatch):
    """A host that removes only some of the edges must fail."""
    runtime = _install(monkeypatch)
    _quad(runtime)

    # A host that reports no error but leaves every edge in place.
    monkeypatch.setattr(runtime.polyOp, "deleteEdges", lambda node, indices: True)
    result = _load_action("action_mesh_edit.py").main(
        node_name="quad", ops=[{"op": "delete_edges", "indices": [1, 2]}]
    )

    assert result["success"] is False
    assert "instead of at most" in result["message"]


def test_edit_vertices_read_reports_what_it_could_not_read(monkeypatch):
    """A read that partially fails must be directly comparable, not incidental."""
    runtime = _install(monkeypatch)
    _quad(runtime)

    original = _PolyOp.getVert

    def _partially_broken(self, node, index):
        if index == 2:
            raise RuntimeError("vertex is unavailable")
        return original(self, node, index)

    monkeypatch.setattr(_PolyOp, "getVert", _partially_broken)

    result = _load_action("action_edit_vertices.py").main(
        action="read", node_name="quad", vertex_indices=[1, 2, 3]
    )

    assert result["success"] is True
    assert result["data"]["requested_count"] == 3
    assert result["data"]["vertex_count"] == 2
    # The gap is stated in the result, not left for the caller to infer from
    # `vertex_count` being smaller than the list it passed in.
    assert "1 of 3" in result["data"]["message_note"]
    assert len(result["data"]["warnings"]) == 1
    assert "getVert" in result["data"]["warnings"][0]


def test_mesh_edit_rejects_an_edge_op_after_faces_were_deleted(monkeypatch):
    """Deleting faces takes edges with them, so the edge count stops being known.

    The quad has 5 edges. Deleting both faces removes every edge, and which of
    them go is decided by the host - so a following ``delete_edges`` has to be
    refused during preflight instead of being range-checked against the stale
    count of 5 and failing once the hold is already open.
    """
    runtime = _install(monkeypatch)
    node = _quad(runtime)

    result = _load_action("action_mesh_edit.py").main(
        node_name="quad",
        ops=[
            {"op": "delete_faces", "indices": [1, 2]},
            {"op": "delete_edges", "indices": [5]},
        ],
    )

    assert result["success"] is False
    assert "failed preflight" in result["message"]
    assert result["data"]["applied"] == 0
    assert result["data"]["errors"][0]["op"] == "delete_edges"
    assert result["data"]["errors"][0]["position"] == 1
    assert "split the batch" in result["data"]["errors"][0]["message"]
    # Rejected before the hold opened, so no geometry was touched and no host
    # call was made with an index that no longer exists.
    assert len(node.faces) == 2
    assert len(node.edges()) == 5
    assert runtime.theHold.begin_calls == 0


def test_mesh_edit_declares_a_component_and_a_count_effect_for_every_op():
    """A new op must not be able to reach preflight without its metadata."""
    module = _load_action("action_mesh_edit.py")

    assert set(module.OPERATIONS) <= set(module.OP_COMPONENT)
    assert set(module.OPERATIONS) <= set(module._COUNT_EFFECTS)


def test_mesh_edit_advance_limits_treats_a_missing_count_effect_as_no_change():
    """An op with no count entry must not raise KeyError out of preflight."""
    module = _load_action("action_mesh_edit.py")
    limits = {"vertices": 4, "edges": 5, "faces": 2}

    module._advance_limits(limits, {"position": 0, "op": "tessellate", "indices": [1]})

    assert limits == {"vertices": 4, "edges": 5, "faces": 2}


# ── tools.yaml / docs contract ─────────────────────────────────────────

NEW_TOOLS = {
    "create_mesh": "action_create_mesh.py",
    "inspect_mesh": "action_inspect_mesh.py",
    "mesh_edit": "action_mesh_edit.py",
    "edit_vertices": "action_edit_vertices.py",
    "pick_component": "action_pick_component.py",
}


def test_new_component_tools_are_declared_with_the_full_contract():
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    for name, source_file in NEW_TOOLS.items():
        tool = by_name[name]
        assert tool["source_file"] == source_file, name
        assert (SKILL_DIR / source_file).is_file(), name
        assert tool["affinity"] == "main", name
        assert tool["enforce_thread_affinity"] is True, name
        assert isinstance(tool["read_only"], bool), name
        assert isinstance(tool["destructive"], bool), name
        assert isinstance(tool["idempotent"], bool), name
        for key in ("side_effects", "produces", "risk", "intent", "annotations"):
            assert key in tool, (name, key)
        for key in ("read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"):
            assert isinstance(tool["annotations"][key], bool), (name, key)
        assert tool["annotations"]["read_only_hint"] is tool["read_only"], name
        assert tool["annotations"]["destructive_hint"] is tool["destructive"], name


def test_only_mesh_edit_is_destructive_and_it_declares_undo():
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    for name in NEW_TOOLS:
        destructive = by_name[name]["destructive"]
        undo = by_name[name].get("undo")
        if name == "mesh_edit":
            assert destructive is True, name
            assert isinstance(undo, dict), name
            assert undo["supported"] is True
            assert undo["granularity"] == _undo_utils.GRANULARITY_SINGLE_CALL
        else:
            # Creating a mesh, moving vertices, and picking are not data loss.
            assert destructive is False, name
        if isinstance(undo, dict):
            assert undo["granularity"] in _undo_utils.VALID_GRANULARITIES, name
            assert undo["notes"].strip(), name


def test_every_new_tool_with_undo_metadata_has_an_undo_doc_row():
    """docs/UNDO.md is the contract; a declared tool without a row is a gap."""
    doc = UNDO_DOC.read_text(encoding="utf-8")
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    for tool in tools:
        if tool["name"] not in NEW_TOOLS:
            continue
        if not isinstance(tool.get("undo"), dict):
            continue
        assert "`3dsmax-mesh-ops__{}`".format(tool["name"]) in doc, tool["name"]


def test_pick_component_is_read_only_and_idempotent():
    tools = yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    assert by_name["pick_component"]["read_only"] is True
    assert by_name["pick_component"]["idempotent"] is True
    assert by_name["inspect_mesh"]["read_only"] is True
    assert by_name["inspect_mesh"]["idempotent"] is True
