"""Offline tests for the scene query and graph tools.

``pymxs`` is faked with plain Python objects, so the hierarchy walk, the
instance grouping, the dependency read, and all six query modes run without a
3ds Max host.

The tests that matter most here are the ones about honesty: a host that cannot
answer must refuse, and a host that answers partially must say what it skipped.
"Empty result" and "could not look" are different answers, and an agent acts on
them very differently.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dcc_mcp_3dsmax import _scene_query  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1] / "src" / "dcc_mcp_3dsmax" / "skills" / "3dsmax-scene"


def _load_action(script_name: str):
    path = SKILL_DIR / script_name
    spec = importlib.util.spec_from_file_location(path.stem + "_query_test_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Fake pymxs runtime ─────────────────────────────────────────────────


class _Point3(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class _BaseObject(object):
    """Stands in for the object a node derives from.

    Instances share one instance of this class, which is what makes the
    base-object-handle strategy observable.
    """

    def __init__(self, handle):
        self.handle = handle
        self.name = "obj{}".format(handle)


class _Node(object):
    def __init__(self, name, handle, base=None, parent=None, hidden=False):
        self.name = name
        self.handle = handle
        self.baseObject = base if base is not None else _BaseObject(handle + 1000)
        self.parent = parent
        self.isHidden = hidden
        self.radius = 5.0
        self.wirecolor = _Point3(0.2, 0.2, 0.2)

    def radius_getter(self):
        """A method, used to prove methods are not reported as properties."""
        return self.radius


class _InstanceMgr(object):
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = 0

    def GetInstances(self, node):
        self.calls += 1
        return [item for item in self.runtime.objects if item.baseObject is node.baseObject]


class _Refs(object):
    def __init__(self, runtime, *, failures=()):
        self.runtime = runtime
        self.failures = set(failures)
        self.calls = []

    def dependents(self, node):
        self.calls.append("dependents")
        if "dependents" in self.failures:
            raise RuntimeError("the host refused refs.dependents")
        return [self.runtime.shared_material]

    def dependentnodes(self, node):
        self.calls.append("dependentnodes")
        if "dependentnodes" in self.failures:
            raise RuntimeError("the host refused refs.dependentnodes")
        return [item for item in self.runtime.objects if item is not node]

    def dependsOn(self, node):
        self.calls.append("dependsOn")
        if "dependsOn" in self.failures:
            raise RuntimeError("the host refused refs.dependsOn")
        return [node.baseObject]


class _Runtime(object):
    def __init__(self, *, with_instance_mgr=False, refs_failures=(), with_refs=True, ghost=None):
        shared = _BaseObject(900)
        self.hero = _Node("hero_mesh", 1, base=shared)
        self.hero_instance = _Node("hero_mesh_inst", 2, base=shared)
        self.child = _Node("hero_child", 3, parent=self.hero)
        self.grandchild = _Node("hero_grandchild", 4, parent=self.child)
        self.lone = _Node("lone_mesh", 5)
        self.hidden_leaf = _Node("hidden_leaf", 6, parent=self.hero, hidden=True)
        self.objects = [self.hero, self.hero_instance, self.child, self.grandchild, self.lone, self.hidden_leaf]
        self.selection = [self.hero]
        self.maxFileName = "unit_test.max"
        self.shared_material = types.SimpleNamespace(name="shared_mat", handle=999)
        # A wrapper getNodeByName may return instead of the enumerated node:
        # real hosts hand out more than one wrapper for the same node, and the
        # resolve_node_object fallback can produce one the enumeration never saw.
        self.ghost = ghost
        if with_instance_mgr:
            self.InstanceMgr = _InstanceMgr(self)
        if with_refs:
            self.refs = _Refs(self, failures=refs_failures)

    def getNodeByName(self, name):
        if self.ghost is not None and name == self.ghost.name:
            return self.ghost
        for node in self.objects:
            if node.name == name:
                return node
        return None


def _install_runtime(monkeypatch, **kwargs):
    runtime = _Runtime(**kwargs)
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=runtime))
    return runtime


def _hierarchy(**kwargs):
    return _load_action("action_get_hierarchy.py").main(**kwargs)


def _instances(**kwargs):
    return _load_action("action_get_instances.py").main(**kwargs)


def _dependencies(**kwargs):
    return _load_action("action_get_dependencies.py").main(**kwargs)


def _query(**kwargs):
    return _load_action("action_query_scene.py").main(**kwargs)


def _names(tree):
    return [(entry["node_name"], entry["depth"], _names(entry["children"])) for entry in tree]


# ── get_hierarchy ──────────────────────────────────────────────────────


def test_hierarchy_walks_the_whole_scene(monkeypatch):
    _install_runtime(monkeypatch)

    result = _hierarchy()

    assert result["success"] is True
    assert result["data"]["node_count"] == 6
    assert result["data"]["root_count"] == 3
    assert [entry["node_name"] for entry in result["data"]["tree"]] == ["hero_mesh", "hero_mesh_inst", "lone_mesh"]
    assert _names(result["data"]["tree"])[0] == ("hero_mesh", 0, [("hero_child", 1, [("hero_grandchild", 2, [])]), ("hidden_leaf", 1, [])])


def test_hierarchy_can_start_at_one_subtree(monkeypatch):
    _install_runtime(monkeypatch)

    result = _hierarchy(node_name="hero_child")

    assert result["success"] is True
    assert result["data"]["node_count"] == 2
    assert result["data"]["root_count"] == 1
    assert _names(result["data"]["tree"]) == [("hero_child", 0, [("hero_grandchild", 1, [])])]


def test_hierarchy_a_missing_root_is_an_error_not_an_empty_tree(monkeypatch):
    _install_runtime(monkeypatch)

    result = _hierarchy(node_name="ghost_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "node_not_found"


def test_hierarchy_reports_the_depth_cap_instead_of_dropping_children(monkeypatch):
    _install_runtime(monkeypatch)

    result = _hierarchy(node_name="hero_mesh", max_depth=0)

    assert result["success"] is True
    assert result["data"]["truncated"] is True
    assert result["data"]["node_count"] == 1
    assert result["data"]["tree"][0]["unlisted_children"] == 2


def test_hierarchy_reports_the_node_limit(monkeypatch):
    _install_runtime(monkeypatch)

    result = _hierarchy(node_name="hero_mesh", limit=2)

    assert result["success"] is True
    assert result["data"]["node_count"] == 2
    assert result["data"]["truncated"] is True
    assert any("node limit" in item for item in result["data"]["warnings"])


def test_hierarchy_hidden_nodes_take_their_subtree_with_them(monkeypatch):
    _install_runtime(monkeypatch)

    result = _hierarchy(include_hidden=False)

    assert result["success"] is True
    assert "hidden_leaf" not in str(_names(result["data"]["tree"]))
    assert any("hidden node(s)" in item for item in result["data"]["warnings"])


def test_hierarchy_a_cycle_is_reported_not_dropped(monkeypatch):
    """A cycle has no parentless member, so it must not silently vanish."""
    runtime = _install_runtime(monkeypatch)
    runtime.hero.parent = runtime.grandchild
    runtime.grandchild.parent = runtime.hero
    runtime.objects = [runtime.hero, runtime.grandchild]

    result = _hierarchy()

    assert result["success"] is True
    # Every node is still in the tree: the cycle was broken, not skipped.
    assert result["data"]["node_count"] == 2
    assert sorted(entry["node_name"] for entry in result["data"]["cycle_members"]) == [
        "hero_grandchild",
        "hero_mesh",
    ]
    assert len(result["data"]["cycle_roots"]) == 1
    assert any("cycle" in item for item in result["data"]["warnings"])


def test_hierarchy_a_parent_outside_the_scene_is_named(monkeypatch):
    """A group head is not in rt.objects, so its children look parentless."""
    runtime = _install_runtime(monkeypatch)
    runtime.child.parent = _Node("group_head", 77)

    result = _hierarchy()

    assert result["success"] is True
    assert result["data"]["parent_outside_scene"][0]["node_name"] == "hero_child"
    assert any("not in the scene node list" in item for item in result["data"]["warnings"])


def test_hierarchy_a_host_without_objects_is_refused(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=types.SimpleNamespace()))

    result = _hierarchy()

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "scene_unavailable"


def test_hierarchy_rejects_a_non_integer_limit(monkeypatch):
    _install_runtime(monkeypatch)

    result = _hierarchy(limit="all")

    assert result["success"] is False
    assert "limit must be an integer" in result["message"]


# ── get_instances ──────────────────────────────────────────────────────


def test_instances_group_nodes_that_share_an_object(monkeypatch):
    _install_runtime(monkeypatch)

    result = _instances()

    assert result["success"] is True
    assert result["data"]["group_count"] == 1
    group = result["data"]["groups"][0]
    assert group["instance_count"] == 2
    assert sorted(entry["node_name"] for entry in group["nodes"]) == ["hero_mesh", "hero_mesh_inst"]


def test_instances_prefers_instance_mgr_and_says_so(monkeypatch):
    runtime = _install_runtime(monkeypatch, with_instance_mgr=True)

    result = _instances()

    assert result["success"] is True
    assert result["data"]["strategy"] == "instance_mgr"
    assert runtime.InstanceMgr.calls == len(runtime.objects)
    assert result["data"]["warnings"] == []


def test_instances_falls_back_to_the_base_object_handle_and_says_so(monkeypatch):
    _install_runtime(monkeypatch)

    result = _instances()

    assert result["data"]["strategy"] == "base_object_handle"
    assert any("GetInstances is unavailable" in item for item in result["data"]["warnings"])


def test_instances_one_node_target_answers_its_own_set(monkeypatch):
    _install_runtime(monkeypatch)

    result = _instances(node_name="hero_mesh")

    assert result["success"] is True
    assert result["data"]["is_instanced"] is True
    assert result["data"]["instance_count"] == 2


def test_instances_a_unique_node_is_reported_as_not_instanced(monkeypatch):
    _install_runtime(monkeypatch)

    result = _instances(node_name="lone_mesh")

    assert result["data"]["is_instanced"] is False
    assert result["data"]["instance_count"] == 1


def test_instances_a_missing_node_is_an_error_not_no_instances(monkeypatch):
    _install_runtime(monkeypatch)

    result = _instances(node_name="ghost_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "node_not_found"


def test_instances_a_host_that_cannot_answer_is_refused(monkeypatch):
    """An empty group list would read as 'this scene has no instances'."""

    class _HandlelessNode(_Node):
        def __init__(self, name, handle):
            _Node.__init__(self, name, handle)
            self.baseObject = types.SimpleNamespace(name="no_handle_here")

    runtime = _install_runtime(monkeypatch)
    for node in list(runtime.objects):
        node.__class__ = _HandlelessNode
        node.baseObject = types.SimpleNamespace(name="no_handle_here")

    result = _instances()

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "instance_query_unavailable"
    assert "no base object handle" in result["message"]


def test_instances_unresolved_nodes_are_reported_with_a_reason(monkeypatch):
    runtime = _install_runtime(monkeypatch)
    runtime.lone.baseObject = types.SimpleNamespace(name="no_handle_here")

    result = _instances()

    assert result["success"] is True
    assert result["data"]["unresolved_count"] == 1
    assert result["data"]["unresolved"][0]["node_name"] == "lone_mesh"
    assert result["data"]["unresolved"][0]["reason"]
    assert any("could not be classified" in item for item in result["data"]["warnings"])


def test_instances_include_unique_reports_singletons(monkeypatch):
    _install_runtime(monkeypatch)

    result = _instances(include_unique=True)

    assert result["data"]["include_unique"] is True
    assert sorted(group["instance_count"] for group in result["data"]["groups"]) == [1, 1, 1, 1, 2]


# ── get_dependencies ───────────────────────────────────────────────────


def test_dependencies_reads_all_three_refs_sections(monkeypatch):
    _install_runtime(monkeypatch)

    result = _dependencies(node_name="hero_mesh")

    assert result["success"] is True
    assert result["data"]["strategy"] == "refs"
    assert result["data"]["direct_dependents"]["available"] is True
    assert result["data"]["direct_dependents"]["items"][0]["node_name"] == "shared_mat"
    assert result["data"]["dependent_nodes"]["count"] == 5
    # hero_mesh and hero_mesh_inst share one object, so dependsOn returns it.
    assert result["data"]["depends_on"]["items"][0]["object_id"] == 900
    assert result["data"]["warnings"] == []


def test_dependencies_resolves_a_handle_target(monkeypatch):
    _install_runtime(monkeypatch)

    result = _dependencies(handle=1)

    assert result["success"] is True
    assert result["data"]["node"]["node_name"] == "hero_mesh"


def test_dependencies_requires_a_target(monkeypatch):
    _install_runtime(monkeypatch)

    result = _dependencies()

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "missing_target"


def test_dependencies_a_host_without_refs_is_refused(monkeypatch):
    _install_runtime(monkeypatch, with_refs=False)

    result = _dependencies(node_name="hero_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "refs_unavailable"


def test_dependencies_a_refs_interface_with_no_known_methods_is_refused(monkeypatch):
    runtime = _install_runtime(monkeypatch)
    runtime.refs = types.SimpleNamespace()

    result = _dependencies(node_name="hero_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "refs_unavailable"


def test_dependencies_all_refs_calls_failing_is_refused(monkeypatch):
    _install_runtime(monkeypatch, refs_failures=("dependents", "dependentnodes", "dependsOn"))

    result = _dependencies(node_name="hero_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "refs_unavailable"


def test_dependencies_a_partial_answer_says_which_sections_failed(monkeypatch):
    _install_runtime(monkeypatch, refs_failures=("dependents",))

    result = _dependencies(node_name="hero_mesh")

    assert result["success"] is True
    assert result["data"]["direct_dependents"]["available"] is False
    assert "the host refused refs.dependents" in result["data"]["direct_dependents"]["error"]
    assert result["data"]["dependent_nodes"]["available"] is True
    assert any("refs.dependents failed" in item for item in result["data"]["warnings"])


def test_dependencies_a_missing_method_is_named_per_section(monkeypatch):
    runtime = _install_runtime(monkeypatch)
    runtime.refs = types.SimpleNamespace(
        dependents=runtime.refs.dependents,
        dependentnodes=runtime.refs.dependentnodes,
    )

    result = _dependencies(node_name="hero_mesh")

    assert result["success"] is True
    assert result["data"]["depends_on"]["available"] is False
    assert "does not expose refs.dependsOn" in result["data"]["depends_on"]["error"]


def test_dependencies_a_missing_node_is_an_error_not_an_empty_graph(monkeypatch):
    _install_runtime(monkeypatch)

    result = _dependencies(node_name="ghost_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "node_not_found"


# ── query_scene: overview ──────────────────────────────────────────────


def test_overview_answers_with_counts_and_no_node_list(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="overview")

    assert result["success"] is True
    assert result["data"]["nodes"] == []
    summary = result["data"]["summary"]
    assert summary["node_count"] == 6
    assert summary["selection_count"] == 1
    assert summary["class_count"] == 1
    assert summary["classes"][0] == {"class_name": "_BaseObject", "count": 6}
    assert summary["scene_name"] == "unit_test.max"


def test_overview_counts_hidden_nodes(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="overview")

    assert result["data"]["summary"]["hidden_count"] == 1
    assert result["data"]["summary"]["visible_count"] == 5


def test_a_host_without_a_selection_says_so_in_overview(monkeypatch):
    runtime = _install_runtime(monkeypatch)
    del runtime.selection

    result = _query(mode="overview")

    assert result["success"] is True
    assert result["data"]["summary"]["selection_count"] == 0
    assert any("selection" in item for item in result["data"]["warnings"])


# ── query_scene: filter ────────────────────────────────────────────────


def test_filter_matches_a_name_substring_case_insensitively(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="filter", name_filter="HERO")

    assert result["success"] is True
    assert result["data"]["count"] == 4
    assert all("hero" in entry["node_name"] for entry in result["data"]["nodes"])


def test_filter_reports_truncation(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="filter", limit=2)

    assert result["data"]["count"] == 2
    assert result["data"]["total_matched"] == 6
    assert result["data"]["truncated"] is True


def test_filter_can_exclude_hidden_nodes(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="filter", name_filter="hidden", include_hidden=False)

    assert result["data"]["count"] == 0


# ── query_scene: class ─────────────────────────────────────────────────


def test_class_mode_matches_case_insensitively(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="class", class_name="_NODE")

    assert result["data"]["count"] == 6


def test_class_mode_supports_contains_matching(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="class", class_name="node", class_match="contains")

    assert result["data"]["count"] == 6


def test_class_mode_requires_a_class_name(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="class")

    assert result["success"] is False
    assert "class_name is required" in result["message"]


def test_class_mode_an_unmatched_class_answers_with_an_empty_list(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="class", class_name="VRayLight")

    assert result["success"] is True
    assert result["data"]["nodes"] == []
    assert result["data"]["count"] == 0


# ── query_scene: property ──────────────────────────────────────────────


def test_property_mode_reads_one_property_per_node(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="property", property_name="radius")

    assert result["success"] is True
    assert result["data"]["count"] == 6
    assert result["data"]["nodes"][0]["value"] == 5.0


def test_property_mode_filters_by_value(monkeypatch):
    runtime = _install_runtime(monkeypatch)
    runtime.lone.radius = 42.0

    result = _query(mode="property", property_name="radius", property_value=42.0)

    assert result["data"]["count"] == 1
    assert result["data"]["nodes"][0]["node_name"] == "lone_mesh"


def test_property_mode_a_node_that_could_not_be_read_is_skipped_not_dropped(monkeypatch):
    """Silently dropping an unreadable node is the failure mode to avoid."""

    runtime = _install_runtime(monkeypatch)

    def _refuse(self):
        raise RuntimeError("the host refused this read")

    # A property, not a plain attribute: reading it raises instead of returning
    # None, which is what the host does when it refuses a read.
    runtime.lone.__class__ = type("_RefusingNode", (_Node,), {"radius": property(_refuse)})

    result = _query(mode="property", property_name="radius")

    assert result["success"] is True
    assert result["data"]["skipped_count"] == 1
    assert result["data"]["skipped"][0]["node_name"] == "lone_mesh"
    assert "the host refused this read" in result["data"]["skipped"][0]["reason"]
    assert any("could not be read" in item for item in result["data"]["warnings"])


def test_property_mode_a_method_is_not_a_property(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="property", property_name="radius_getter")

    assert result["data"]["skipped_count"] == 6
    assert "is a method, not a property" in result["data"]["skipped"][0]["reason"]


def test_property_mode_refuses_a_private_property(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="property", property_name="_secret")

    assert result["success"] is False
    assert "private property" in result["message"]


def test_property_mode_requires_a_property_name(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="property")

    assert result["success"] is False
    assert "property is required" in result["message"]


# ── query_scene: selection ─────────────────────────────────────────────


def test_selection_mode_returns_the_selected_nodes(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="selection")

    assert result["data"]["count"] == 1
    assert result["data"]["nodes"][0]["node_name"] == "hero_mesh"


def test_selection_mode_a_host_without_a_selection_is_an_error(monkeypatch):
    runtime = _install_runtime(monkeypatch)
    del runtime.selection

    result = _query(mode="selection")

    assert result["success"] is False
    assert "selection" in result["message"]


# ── query_scene: delta ─────────────────────────────────────────────────


def test_delta_requires_a_baseline(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="delta")

    assert result["success"] is False
    assert "baseline" in result["message"]


def test_delta_a_snapshot_round_trips_through_include_snapshot(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    before = _query(mode="filter", include_snapshot=True)
    assert before["data"]["snapshot_count"] == 6
    assert before["data"]["snapshot_truncated"] is False

    runtime.objects = runtime.objects[:4]
    result = _query(mode="delta", baseline=before["data"]["snapshot"])

    assert result["success"] is True
    assert result["data"]["removed_count"] == 2
    assert sorted(entry["node_name"] for entry in result["data"]["removed"]) == ["hidden_leaf", "lone_mesh"]
    assert result["data"]["added_count"] == 0


def test_delta_matches_by_object_id_so_a_rename_is_not_a_remove_plus_add(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    before = _query(mode="filter", include_snapshot=True)
    runtime.hero.name = "hero_mesh_renamed"

    result = _query(mode="delta", baseline=before["data"]["snapshot"])

    assert result["data"]["renamed_count"] == 1
    assert result["data"]["renamed"][0] == {"before": "hero_mesh", "after": "hero_mesh_renamed", "object_id": 1}
    assert result["data"]["removed_count"] == 0
    assert result["data"]["added_count"] == 0


def test_delta_reports_added_nodes(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    before = _query(mode="filter", include_snapshot=True)
    runtime.objects.append(_Node("brand_new", 42))

    result = _query(mode="delta", baseline=before["data"]["snapshot"])

    assert result["data"]["added_count"] == 1
    assert result["data"]["added"][0]["node_name"] == "brand_new"


def test_delta_compares_a_property_when_the_baseline_carries_values(monkeypatch):
    runtime = _install_runtime(monkeypatch)

    before = _query(mode="property", property_name="radius")
    runtime.hero.radius = 99.0

    result = _query(mode="delta", baseline=before["data"]["nodes"], property_name="radius")

    assert result["data"]["changed_count"] == 1
    assert result["data"]["changed"][0]["node_name"] == "hero_mesh"
    assert result["data"]["changed"][0]["before"] == 5.0
    assert result["data"]["changed"][0]["after"] == 99.0
    assert result["data"]["comparable_count"] == 6


def test_delta_a_baseline_without_values_says_it_could_not_compare(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="delta", baseline=["hero_mesh", "lone_mesh"], property_name="radius")

    assert result["success"] is True
    assert result["data"]["comparable_count"] == 0
    assert any("no value for radius" in item for item in result["data"]["warnings"])


def test_delta_accepts_a_list_of_names(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="delta", baseline=["hero_mesh", "ghost_mesh"])

    assert result["data"]["baseline_count"] == 2
    assert result["data"]["removed_count"] == 1
    assert result["data"]["removed"][0]["node_name"] == "ghost_mesh"


def test_delta_a_truncated_snapshot_is_flagged_before_it_is_used(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="filter", include_snapshot=True, limit=2)

    assert result["data"]["snapshot_truncated"] is True
    assert any("delta baseline" in item for item in result["data"]["warnings"])


def test_delta_rejects_a_baseline_entry_without_a_name(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="delta", baseline=[{"object_id": 1}])

    assert result["success"] is False
    assert "missing node_name" in result["message"]


# ── query_scene: shared contract ───────────────────────────────────────


def test_an_unknown_mode_is_rejected(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="everything")

    assert result["success"] is False
    assert "mode must be one of" in result["message"]


def test_ignored_arguments_are_named_in_warnings(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="filter", class_name="_Node", property_value=3)

    assert result["success"] is True
    assert any("class_name is ignored" in item for item in result["data"]["warnings"])
    assert any("property_value is ignored" in item for item in result["data"]["warnings"])


def test_a_host_without_objects_is_refused_by_every_mode(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymxs", types.SimpleNamespace(runtime=types.SimpleNamespace()))

    result = _query(mode="overview")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "invalid_request"
    assert "objects collection" in result["message"]


# ── tools.yaml contract ────────────────────────────────────────────────

NEW_TOOLS = {
    "get_hierarchy": "action_get_hierarchy.py",
    "get_instances": "action_get_instances.py",
    "get_dependencies": "action_get_dependencies.py",
    "query_scene": "action_query_scene.py",
}


def _tools():
    return yaml.safe_load((SKILL_DIR / "tools.yaml").read_text(encoding="utf-8"))["tools"]


def test_new_tools_declare_read_only_main_thread_metadata():
    tools = {tool["name"]: tool for tool in _tools()}

    for name, source_file in NEW_TOOLS.items():
        tool = tools[name]
        assert tool["source_file"] == source_file, name
        assert (SKILL_DIR / source_file).is_file(), name
        assert tool["affinity"] == "main", name
        assert tool["enforce_thread_affinity"] is True, name
        assert tool["read_only"] is True, name
        assert tool["destructive"] is False, name
        assert tool["idempotent"] is True, name
        assert tool["annotations"]["read_only_hint"] is True, name
        assert tool["annotations"]["destructive_hint"] is False, name
        assert tool["annotations"]["idempotent_hint"] is True, name
        assert tool["side_effects"]["modifies"] is False, name
        assert tool["side_effects"]["deletes"] is False, name
        assert tool["produces"], name


def test_query_scene_schema_lists_every_mode():
    tools = {tool["name"]: tool for tool in _tools()}
    modes = tools["query_scene"]["input_schema"]["properties"]["mode"]["enum"]

    assert modes == list(_scene_query.QUERY_MODES)


# ── regressions: delta scope, baseline unwrapping, wrapper identity ────
#
# These cover the three combinations the first review found: a delta narrowed
# by the shared filters, a result object passed back as its own baseline, and a
# target node that resolve_node_object answered with a wrapper the scene
# enumeration never produced. All three produced a confident answer about
# something the tool had not looked at.


def test_delta_ignores_the_shared_filters_so_in_scene_nodes_are_not_removed(monkeypatch):
    """A filtered delta must not report unfiltered-but-present nodes as removed."""
    _install_runtime(monkeypatch)

    snapshot = _query(mode="filter", include_snapshot=True)["data"]["snapshot"]

    hidden = _query(mode="delta", baseline=snapshot, include_hidden=False)
    assert hidden["data"]["removed_count"] == 0
    assert hidden["data"]["added_count"] == 0
    assert any("were not applied to the comparison" in item for item in hidden["data"]["warnings"])

    filtered = _query(mode="delta", baseline=snapshot, name_filter="hero")
    assert filtered["data"]["removed_count"] == 0
    assert filtered["data"]["added_count"] == 0


def test_delta_still_sees_real_removals(monkeypatch):
    """The scope fix must not blunt delta: a genuinely removed node is reported."""
    runtime = _install_runtime(monkeypatch)

    snapshot = _query(mode="filter", include_snapshot=True)["data"]["snapshot"]
    runtime.objects = [node for node in runtime.objects if node.name != "lone_mesh"]

    result = _query(mode="delta", baseline=snapshot)

    assert result["data"]["removed_count"] == 1
    assert result["data"]["removed"][0]["node_name"] == "lone_mesh"


def test_delta_snapshot_matches_what_it_compared(monkeypatch):
    """A delta snapshot round-trips: feeding it back reports no change."""
    _install_runtime(monkeypatch)

    first = _query(mode="delta", baseline=[], include_snapshot=True)
    assert first["data"]["snapshot_count"] == 6

    second = _query(mode="delta", baseline=first["data"]["snapshot"])

    assert second["data"]["added_count"] == 0
    assert second["data"]["removed_count"] == 0


def test_delta_prefers_a_non_empty_snapshot_over_an_empty_nodes_list(monkeypatch):
    """overview answers with counts, so its nodes list is empty and is not a baseline."""
    _install_runtime(monkeypatch)

    overview = _query(mode="overview", include_snapshot=True)["data"]
    assert overview["nodes"] == []
    assert overview["snapshot_count"] == 6

    result = _query(mode="delta", baseline=overview)

    assert result["data"]["baseline_count"] == 6
    assert result["data"]["added_count"] == 0


def test_delta_warns_when_the_baseline_resolves_to_nothing(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="delta", baseline={"nodes": [], "count": 0})

    assert result["success"] is True
    assert result["data"]["baseline_count"] == 0
    assert any("resolved to 0 entries" in item for item in result["data"]["warnings"])


def test_hierarchy_a_different_wrapper_for_the_same_handle_still_finds_the_subtree(monkeypatch):
    """pymxs may hand back another wrapper for a node that is in the scene."""
    ghost = _Node("hero_mesh", 1, base=_BaseObject(900))
    runtime = _install_runtime(monkeypatch, ghost=ghost)
    assert runtime.getNodeByName("hero_mesh") is ghost

    result = _hierarchy(node_name="hero_mesh")

    assert result["success"] is True
    # The subtree belongs to the enumerated node, not to the wrapper we were handed.
    assert result["data"]["node_count"] == 4
    assert "hero_child" in [entry["node_name"] for entry in result["data"]["tree"][0]["children"]]


def test_hierarchy_refuses_a_wrapper_the_enumeration_does_not_contain(monkeypatch):
    """A one-node tree that looks complete is worse than a refusal."""
    ghost = _Node("hero_mesh", 1, base=_BaseObject(900))
    runtime = _install_runtime(monkeypatch, ghost=ghost)
    runtime.objects = [node for node in runtime.objects if node.name != "hero_mesh"]

    result = _hierarchy(node_name="hero_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "node_not_in_scene"
    assert result["data"]["tree"] == []


def test_instances_a_different_wrapper_for_the_same_handle_still_resolves(monkeypatch):
    ghost = _Node("hero_mesh", 1, base=_BaseObject(900))
    runtime = _install_runtime(monkeypatch, ghost=ghost)
    assert runtime.getNodeByName("hero_mesh") is ghost

    result = _instances(node_name="hero_mesh")

    assert result["success"] is True
    assert result["data"]["is_instanced"] is True
    assert result["data"]["instance_count"] == 2


def test_instances_refuses_a_wrapper_the_enumeration_does_not_contain(monkeypatch):
    """Claiming 'shares its object with 0 node(s)' asserts a fact never looked up."""
    ghost = _Node("hero_mesh", 1, base=_BaseObject(900))
    runtime = _install_runtime(monkeypatch, ghost=ghost)
    runtime.objects = [node for node in runtime.objects if node.name != "hero_mesh"]

    result = _instances(node_name="hero_mesh")

    assert result["success"] is False
    assert result["data"]["failure_reason"] == "node_not_in_scene"


def test_dependencies_accepts_a_different_wrapper_for_the_same_handle(monkeypatch):
    """The handle match is shared, so all three tools agree on one object."""
    ghost = _Node("hero_mesh", 1, base=_BaseObject(900))
    _install_runtime(monkeypatch, ghost=ghost)

    result = _dependencies(node_name="hero_mesh")

    assert result["success"] is True
    assert result["data"]["node"]["node_name"] == "hero_mesh"


# ── regressions: bounded skipped, unreadable bucket, ignored arguments ─


def test_property_mode_bounds_the_skipped_list_like_the_matches(monkeypatch):
    """An unreadable node is a finding, not a licence to return thousands."""
    _install_runtime(monkeypatch)

    result = _query(mode="property", property_name="nothing_has_this", limit=2)

    assert len(result["data"]["skipped"]) == 2
    assert result["data"]["skipped_count"] == 6
    assert result["data"]["skipped_omitted"] == 4
    assert result["data"]["truncated"] is True


def test_delta_does_not_count_read_failures_as_changes(monkeypatch):
    runtime = _install_runtime(monkeypatch)
    snapshot = _query(mode="property", property_name="radius")["data"]["nodes"]

    def _refuse(self):
        raise RuntimeError("the host refused this read")

    runtime.lone.__class__ = type("_RefusingNode", (_Node,), {"radius": property(_refuse)})

    result = _query(mode="delta", baseline=snapshot, property_name="radius")

    assert result["data"]["changed_count"] == 0
    assert result["data"]["unreadable_count"] == 1
    assert result["data"]["unreadable"][0]["node_name"] == "lone_mesh"
    assert result["data"]["unreadable"][0]["before"] == 5.0
    assert any("could not be read" in item for item in result["data"]["warnings"])


def test_property_value_is_reported_as_ignored_in_delta(monkeypatch):
    _install_runtime(monkeypatch)

    result = _query(mode="delta", baseline=["hero_mesh"], property_value=3)

    assert any("property_value is ignored" in item for item in result["data"]["warnings"])


def test_hierarchy_names_each_truncation_cause_separately(monkeypatch):
    """One sentence naming three causes leaves the caller unable to pick a knob."""
    _install_runtime(monkeypatch)

    depth = _hierarchy(node_name="hero_mesh", max_depth=0)
    assert any("depth cap" in item for item in depth["data"]["warnings"])
    assert not any("node limit" in item for item in depth["data"]["warnings"])

    limit = _hierarchy(limit=2)
    assert any("node limit" in item for item in limit["data"]["warnings"])
    assert not any("depth cap" in item for item in limit["data"]["warnings"])
