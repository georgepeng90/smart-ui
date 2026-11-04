"""可更新布局的版本、资源绑定、字体证据与工程迁移契约。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from smartui_mcp.page_layouts import resolve_definition, validate_definition
from smartui_mcp.projects import ProjectError
from test_compositions import text_node
from test_export_workflow import export_item
from test_review_workflow import project, record_sample_run


@pytest.fixture
def exported_project(project):
    store, context = project
    exported = store.export_assets(context["project_root"], context["revision"], "sample", "first", [export_item()])
    return store, store.context(exported["project_root"])


@pytest.fixture
def label(text_node):
    return {**text_node, "width": 48, "height": 30, "line_height": 24,
            "align": "left", "wrap": True, "overflow": "error"}


def image_node(identifier="button-instance", binding=None, **changes):
    return {"id": identifier, "kind": "image", "asset_id": "button", "x": 2, "y": 2,
            "width": 18, "height": 12, "binding": binding or {"kind": "fixed", "version": 1}, **changes}


def save(store, context, nodes, **changes):
    arguments = {"layout_id": "screen", "name": "测试页面", "width": 64, "height": 48,
                 "background": "#123456", "nodes": nodes, **changes}
    return store.save_layout(context["project_root"], context["revision"], **arguments)


def project_bytes(context):
    return (Path(context["project_root"]) / ".smartui/project.json").read_bytes()


def test_fixed_and_selected_bindings_resolve_independently_without_mutating_definition(exported_project):
    store, context = exported_project
    root = context["project_root"]
    selected = store.select_image(root, context["revision"], "button", 1)
    fixed = image_node("fixed")
    following = image_node("following", {"kind": "selected"}, x=25)
    saved = save(store, selected, [fixed, following])
    original = deepcopy(saved["layout"]["definition"])
    second_run = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    exported = store.export_assets(root, second_run["revision"], "sample", "second", [export_item()])
    store.select_image(root, exported["revision"], "button", 2)
    current = store.context(root)
    state_before = deepcopy(current["data"])
    resolved = resolve_definition(current["data"], original)
    assert [node["version"] for node in resolved["nodes"]] == [1, 2]
    assert resolved["nodes"][0]["sha256"] != resolved["nodes"][1]["sha256"]
    assert original == saved["layout"]["definition"]
    assert current["data"] == state_before
    assert store.get_layout(root, "screen")["layout"]["definition"] == original


def test_selected_binding_requires_explicit_selection_and_fixed_version_must_exist(exported_project):
    store, context = exported_project
    before = project_bytes(context)
    for binding, message in [({"kind": "selected"}, "尚未选用"), ({"kind": "fixed", "version": 2}, "不存在的资产版本")]:
        with pytest.raises(ValueError, match=message):
            save(store, context, [image_node(binding=binding)])
    assert project_bytes(context) == before


def test_font_digest_roundtrip_allows_editing_and_resaving_the_returned_definition(exported_project, label):
    store, context = exported_project
    saved = save(store, context, [image_node(), label])
    fetched = store.get_layout(context["project_root"], "screen")
    definition = fetched["layout"]["definition"]
    font = definition["nodes"][1]
    digest = hashlib.sha256(Path(label["font_path"]).read_bytes()).hexdigest()
    assert font["font_sha256"] == digest
    assert definition == saved["layout"]["definition"]
    font["text"] = "A\nA"
    updated = store.save_layout(context["project_root"], fetched["revision"], "screen", "测试页面", **definition)
    assert updated["layout"]["version"] == 2
    assert updated["layout"]["definition"]["nodes"][1]["font_sha256"] == digest
    assert updated["layout"]["definition"]["nodes"][1]["text"] == "A\nA"
    assert store.get_layout(context["project_root"], "screen", 1)["layout"]["definition"] == saved["layout"]["definition"]


def test_moving_shared_instances_creates_only_a_layout_version_and_preserves_history(exported_project):
    store, context = exported_project
    nodes = [image_node("first"), image_node("second", x=25)]
    saved = save(store, context, nodes)
    old_record = {key: deepcopy(saved["layout"][key]) for key in
                  ("version", "definition", "definition_sha256", "created_at")}
    assets = deepcopy(store.context(context["project_root"])["data"]["images"])
    changed = deepcopy(nodes)
    changed[0]["x"] = 4
    changed[1]["y"] = 20
    updated = save(store, saved, changed)
    assert updated["layout"]["version"] == 2
    current = store.context(context["project_root"])
    assert current["data"]["images"] == assets
    assert len(current["data"]["images"]["button"]["versions"]) == 1
    before_read = project_bytes(current)
    historical = store.get_layout(context["project_root"], "screen", 1)
    assert {key: historical["layout"][key] for key in old_record} == old_record
    assert project_bytes(current) == before_read
    assert historical["revision"] == current["revision"]
    assert saved["layout"]["definition"]["nodes"] == nodes


def test_identical_save_reuses_layout_version_and_project_revision(exported_project, label):
    store, context = exported_project
    saved = save(store, context, [image_node(), label])
    before = project_bytes(saved)
    definition = store.get_layout(context["project_root"], "screen")["layout"]["definition"]
    again = store.save_layout(context["project_root"], saved["revision"], "screen", "测试页面", **definition)
    assert again == saved
    assert project_bytes(again) == before
    assert len(store.context(context["project_root"])["data"]["layouts"]["screen"]["versions"]) == 1


def test_layout_rename_preserves_the_name_saved_in_each_historical_version(exported_project):
    store, context = exported_project
    first = save(store, context, [image_node()], name="背包页面")
    second = save(store, first, [image_node()], name="物品详情页面")
    assert second["layout"]["version"] == 2
    assert second["layout"]["name"] == "物品详情页面"
    assert second["layout"]["definition_sha256"] == first["layout"]["definition_sha256"]
    historical = store.get_layout(context["project_root"], "screen", 1)["layout"]
    assert historical["name"] == "背包页面"
    assert historical["created_at"] == first["layout"]["created_at"]
    assert store.get_layout(context["project_root"], "screen")["layout"]["name"] == "物品详情页面"


def test_missing_historical_layout_name_reports_the_invalid_record(exported_project):
    store, context = exported_project
    save(store, context, [image_node()])
    path = Path(context["project_root"]) / ".smartui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["layouts"]["screen"]["versions"][0]["name"]
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProjectError, match=r"layouts\.screen\.versions\[1\]\.name.*非空字符串"):
        store.get_layout(context["project_root"], "screen", 1)


def test_layout_accepts_512_nodes_without_the_static_composition_limit(exported_project):
    store, context = exported_project
    nodes = [image_node(f"instance-{index}") for index in range(512)]
    saved = save(store, context, nodes)
    assert len(saved["layout"]["definition"]["nodes"]) == 512
    assert len(store.get_layout(context["project_root"], "screen")["layout"]["definition"]["nodes"]) == 512
    before = project_bytes(saved)
    with pytest.raises(ValueError, match="512"):
        save(store, saved, [*nodes, image_node("too-many")])
    assert project_bytes(saved) == before


@pytest.mark.parametrize("change,message", [
    ({"kind": "component"}, "kind"), ({"id": "../invalid"}, "id"),
    ({"unknown": True}, "字段"), ({"x": True}, "x"), ({"width": 0}, "width"),
    ({"x": 63}, "超出画布"), ({"binding": {"kind": "latest"}}, "binding.kind"),
    ({"binding": {"kind": "fixed"}}, "binding.*字段"),
    ({"binding": {"kind": "fixed", "version": True}}, "binding.version"),
    ({"binding": {"kind": "selected", "version": 1}}, "binding.*字段"),
])
def test_invalid_image_schema_never_saves_a_partial_layout(exported_project, change, message):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match=message):
        save(store, context, [image_node(), image_node("invalid", **change)])
    assert project_bytes(context) == before
    assert "screen" not in store.context(context["project_root"])["data"].get("layouts", {})


@pytest.mark.parametrize("change,message", [
    ({"overflow": "shrink"}, "overflow"), ({"wrap": 1}, "wrap"),
    ({"align": "justify"}, "align"), ({"line_height": 0}, "line_height"),
    ({"text": ""}, "text"), ({"text": "A\tA"}, "控制字符"),
    ({"unknown": 1}, "字段"), ({"color": "red"}, "color"),
])
def test_invalid_text_policy_is_rejected(exported_project, label, change, message):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match=message):
        save(store, context, [{**label, **change}])
    assert project_bytes(context) == before


@pytest.mark.parametrize("failure", ["missing", "relative", "wrong_extension", "invalid_font", "missing_character"])
def test_font_errors_identify_the_source_instead_of_substituting_a_font(exported_project, label, failure):
    store, context = exported_project
    path = Path(label["font_path"])
    if failure == "missing":
        path.unlink()
    elif failure == "relative":
        label["font_path"] = "fixture.ttf"
    elif failure == "wrong_extension":
        other = path.with_suffix(".png")
        shutil.copyfile(path, other)
        label["font_path"] = str(other)
    elif failure == "invalid_font":
        path.write_bytes(b"invalid font")
    else:
        label["text"] = "B"
    before = project_bytes(context)
    with pytest.raises((ValueError, FileNotFoundError)) as error:
        save(store, context, [label])
    if failure == "missing_character":
        assert "U+0042" in str(error.value)
    else:
        assert "fixture" in str(error.value)
    assert project_bytes(context) == before


def test_font_changed_since_saved_definition_cannot_be_accepted_silently(exported_project, label):
    store, context = exported_project
    saved = save(store, context, [label])
    definition = deepcopy(saved["layout"]["definition"])
    path = Path(label["font_path"])
    path.write_bytes(path.read_bytes() + b"changed font bytes")
    before = project_bytes(saved)
    with pytest.raises(ValueError, match="字体 SHA-256 不符"):
        store.save_layout(context["project_root"], saved["revision"], "screen", "测试页面", **definition)
    assert project_bytes(saved) == before


@pytest.mark.parametrize("width,height", [(1, 12), (18, 1), (2, 2)])
def test_nine_slice_minimum_dimensions_are_enforced_before_save(exported_project, width, height):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match="固定边之和"):
        save(store, context, [image_node(width=width, height=height)])
    assert project_bytes(context) == before


def test_missing_asset_and_duplicate_instance_ids_are_explicit_errors(exported_project):
    store, context = exported_project
    before = project_bytes(context)
    with pytest.raises(ValueError, match="不存在的资产"):
        save(store, context, [image_node(asset_id="missing")])
    with pytest.raises(ValueError, match="重复节点 ID"):
        save(store, context, [image_node(), image_node()])
    assert project_bytes(context) == before


def test_changed_asset_archive_is_rejected_by_resolution_and_save(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])
    state = store.context(context["project_root"])["data"]
    archive = Path(state["images"]["button"]["versions"][0]["path"])
    archive.write_bytes(b"manual asset edit")
    before = project_bytes(saved)
    with pytest.raises(ValueError, match="SHA-256"):
        resolve_definition(state, saved["layout"]["definition"])
    with pytest.raises(ValueError, match="SHA-256"):
        save(store, saved, [image_node(x=4)])
    assert project_bytes(saved) == before


def test_project_move_preserves_relative_font_path_and_layout_digest(exported_project, label, tmp_path):
    store, context = exported_project
    root = Path(context["project_root"])
    font = root / "Assets/Fonts/fixture.ttf"
    font.parent.mkdir(parents=True)
    shutil.copyfile(label["font_path"], font)
    label["font_path"] = str(font)
    saved = save(store, context, [image_node(), label])
    disk = json.loads(project_bytes(saved))
    stored = disk["layouts"]["screen"]["versions"][0]
    assert stored["definition"]["nodes"][1]["font_path"] == "Assets/Fonts/fixture.ttf"
    moved = tmp_path / "移动后的布局工程"
    shutil.move(root, moved)
    restored = store.get_layout(str(moved), "screen")
    assert Path(restored["layout"]["definition"]["nodes"][1]["font_path"]) == moved / "Assets/Fonts/fixture.ttf"
    assert restored["layout"]["definition_sha256"] == saved["layout"]["definition_sha256"]
    current = store.context(str(moved))
    resolved = resolve_definition(current["data"], restored["layout"]["definition"])
    assert Path(resolved["nodes"][0]["source"]).is_relative_to(moved)
    before = project_bytes(restored)
    repeat = store.save_layout(str(moved), restored["revision"], "screen", "测试页面", **restored["layout"]["definition"])
    assert repeat["layout"]["version"] == 1
    assert repeat["revision"] == restored["revision"]
    assert project_bytes(repeat) == before


def test_revision_conflict_never_writes_the_attempted_definition(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])
    before = project_bytes(saved)
    with pytest.raises(ProjectError, match="revision"):
        save(store, context, [image_node(x=8)])
    assert project_bytes(saved) == before
    assert store.get_layout(context["project_root"], "screen")["layout"] == saved["layout"]


def test_validation_preserves_the_input_objects(label):
    nodes = [image_node(), label]
    before = deepcopy(nodes)
    definition = validate_definition(64, 48, "#123456", nodes)
    assert nodes == before
    assert "font_sha256" in definition["nodes"][1]
    assert "font_sha256" not in label
