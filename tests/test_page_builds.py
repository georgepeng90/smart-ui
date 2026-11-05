"""浏览器页面构建的不可变快照、绑定更新及历史完整性。"""

from copy import deepcopy
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from mcp import StdioServerParameters
from PIL import Image
import pytest

from smartui_mcp.projects import ProjectError
from test_compositions import text_node
from test_export_workflow import export_item
from test_page_layouts import exported_project, image_node, label, project_bytes, save
from test_review_workflow import project, project_session, record_sample_run


def build(store, saved, version=None):
    return store.build_layout(saved["project_root"], saved["revision"], "screen", version)


def files(record):
    return {key: Path(value["path"]).read_bytes() for key, value in record["files"].items()}


def test_build_uses_real_browser_and_each_call_creates_an_independent_snapshot(exported_project, label):
    store, context = exported_project
    saved = save(store, context, [image_node("first"), image_node("second", x=25), label])
    before_assets = deepcopy(store.context(context["project_root"])["data"]["images"])
    first = build(store, saved)
    record = first["page_build"]
    assert record["layout_id"] == "screen" and record["layout_version"] == 1
    assert record["layout_definition_sha256"] == saved["layout"]["definition_sha256"]
    assert (record["width"], record["height"]) == (64, 48)
    assert first["matches_layout"] and first["matches_bindings"]
    assert record["renderer"]["engine"] == "chromium"
    assert record["renderer"]["version"]
    assert record["renderer"]["device_scale_factor"] == 1
    assert record["renderer"]["fonts"][0]["status"] == "loaded"
    assert len(record["renderer"]["nodes"]) == 3
    assert record["nodes"][0]["snapshot_key"] == record["nodes"][1]["snapshot_key"]
    assert len({node["snapshot_key"] for node in record["nodes"]}) == 2
    for item in record["files"].values():
        path = Path(item["path"])
        assert path.is_relative_to(Path(context["project_root"]) / ".smartui/page-builds" / record["id"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    with Image.open(record["files"]["overview"]["path"]) as screenshot:
        assert screenshot.size == (64, 48)
    inputs = json.loads(Path(record["files"]["inputs"]["path"]).read_text(encoding="utf-8"))
    assert inputs["nodes"][0]["source"] == record["nodes"][0]["snapshot_key"]
    assert inputs["nodes"][2]["font_path"] == record["nodes"][2]["snapshot_key"]
    old_files = files(record)
    second = build(store, first)
    assert second["page_build"]["id"] != record["id"]
    assert files(record) == old_files
    assert second["page_build"]["files"]["overview"]["sha256"] == record["files"]["overview"]["sha256"]
    assert second["page_build"]["files"]["html"]["sha256"] == record["files"]["html"]["sha256"]
    current = store.context(context["project_root"])
    assert current["data"]["images"] == before_assets
    assert current["data"]["layouts"]["screen"]["current_version"] == 1


def test_selected_binding_changes_only_the_next_build_while_old_snapshot_stays_intact(exported_project):
    store, context = exported_project
    root = context["project_root"]
    selected = store.select_image(root, context["revision"], "button", 1)
    saved = save(store, selected, [image_node("fixed"), image_node("following", {"kind": "selected"}, x=25)])
    first = build(store, saved)
    old = first["page_build"]
    old_files = files(old)
    later = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    exported = store.export_assets(root, later["revision"], "sample", "second", [export_item()])
    selected = store.select_image(root, exported["revision"], "button", 2)
    historical = store.get_page_build(root, old["id"])
    assert historical["matches_layout"] is True
    assert historical["matches_bindings"] is False
    assert historical["page_build"] == old
    second = build(store, selected)
    assert [node["version"] for node in second["page_build"]["nodes"]] == [1, 2]
    assert second["matches_layout"] and second["matches_bindings"]
    assert files(old) == old_files


def test_fixed_binding_stays_current_when_selection_changes(exported_project):
    store, context = exported_project
    root = context["project_root"]
    saved = save(store, context, [image_node()])
    first = build(store, saved)
    later = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    exported = store.export_assets(root, later["revision"], "sample", "second", [export_item()])
    selected = store.select_image(root, exported["revision"], "button", 2)
    historical = store.get_page_build(root, first["page_build"]["id"])
    assert historical["matches_layout"] and historical["matches_bindings"]
    second = build(store, selected)
    assert second["page_build"]["nodes"][0]["version"] == 1
    assert second["page_build"]["files"]["overview"]["sha256"] == first["page_build"]["files"]["overview"]["sha256"]


def test_explicit_historical_layout_version_builds_its_saved_definition(exported_project):
    store, context = exported_project
    first = save(store, context, [image_node(x=2)])
    second = save(store, first, [image_node(x=20)])
    built = build(store, second, 1)
    assert built["page_build"]["layout_version"] == 1
    assert built["page_build"]["layout_definition_sha256"] == first["layout"]["definition_sha256"]
    assert built["page_build"]["nodes"][0]["x"] == 2
    assert built["matches_layout"] is False
    assert built["matches_bindings"] is True
    current = build(store, built)
    assert current["page_build"]["layout_version"] == 2
    assert current["page_build"]["nodes"][0]["x"] == 20
    assert current["matches_layout"] is True


def test_history_reads_archived_inputs_after_original_image_and_font_are_deleted(exported_project, label):
    store, context = exported_project
    saved = save(store, context, [image_node(), label])
    built = build(store, saved)
    before = files(built["page_build"])
    state = store.context(context["project_root"])["data"]
    Path(state["images"]["button"]["versions"][0]["path"]).unlink()
    Path(label["font_path"]).unlink()
    raw = project_bytes(built)
    restored = store.get_page_build(context["project_root"], built["page_build"]["id"])
    assert restored["page_build"] == built["page_build"]
    assert files(restored["page_build"]) == before
    assert restored["matches_layout"] and restored["matches_bindings"]
    assert project_bytes(built) == raw


def test_every_archived_file_is_verified_before_returning_history(exported_project, label):
    store, context = exported_project
    built = build(store, save(store, context, [image_node(), label]))
    record = built["page_build"]
    raw = project_bytes(built)
    for item in record["files"].values():
        path = Path(item["path"])
        original = path.read_bytes()
        path.write_bytes(b"manual snapshot modification")
        with pytest.raises((ValueError, ProjectError), match="SHA-256"):
            store.get_page_build(context["project_root"], record["id"])
        path.write_bytes(original)
    assert project_bytes(built) == raw
    path = Path(record["files"]["overview"]["path"])
    path.unlink()
    with pytest.raises((FileNotFoundError, ValueError, ProjectError)) as error:
        store.get_page_build(context["project_root"], record["id"])
    assert path.name in str(error.value)


def test_build_snapshot_survives_project_move_without_the_original_external_font(exported_project, label, tmp_path):
    store, context = exported_project
    built = build(store, save(store, context, [image_node(), label]))
    record = built["page_build"]
    original = files(record)
    Path(label["font_path"]).unlink()
    moved = tmp_path / "移动后的页面快照工程"
    shutil.move(context["project_root"], moved)
    restored = store.get_page_build(str(moved), record["id"])
    assert files(restored["page_build"]) == original
    assert restored["page_build"]["layout_definition_sha256"] == record["layout_definition_sha256"]
    for item in restored["page_build"]["files"].values():
        assert Path(item["path"]).is_relative_to(moved)
    for node in restored["page_build"]["nodes"]:
        assert Path(node["source" if node["kind"] == "image" else "font_path"]).is_relative_to(moved)


def test_revision_conflict_and_invalid_version_leave_no_build_or_state_change(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])
    root = Path(context["project_root"])
    raw = project_bytes(saved)
    with pytest.raises(ProjectError, match="revision"):
        build(store, context)
    with pytest.raises(ProjectError, match="版本不存在"):
        build(store, saved, 2)
    assert project_bytes(saved) == raw
    assert not (root / ".smartui/page-builds").exists()


def test_overflow_failure_does_not_publish_a_build_or_change_project_state(exported_project, label):
    store, context = exported_project
    label.update(text="A A A A A A A A", width=10, height=15, wrap=False, overflow="error")
    saved = save(store, context, [label])
    raw = project_bytes(saved)
    with pytest.raises(ValueError, match="超出文本框"):
        build(store, saved)
    assert project_bytes(saved) == raw
    parent = Path(context["project_root"]) / ".smartui/page-builds"
    assert not parent.exists() or not list(parent.iterdir())


def test_missing_required_snapshot_file_record_is_a_schema_error(exported_project):
    store, context = exported_project
    built = build(store, save(store, context, [image_node()]))
    build_id = built["page_build"]["id"]
    path = Path(context["project_root"]) / ".smartui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["page_builds"][build_id]["files"]["html"]
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProjectError, match="快照文件集合不完整"):
        store.get_page_build(context["project_root"], build_id)


@pytest.mark.parametrize("change", ["node_x", "node_text", "page_width"])
def test_snapshot_metadata_must_match_the_archived_inputs(exported_project, label, change):
    store, context = exported_project
    built = build(store, save(store, context, [image_node(), label]))
    record = built["page_build"]
    original_files = files(record)
    path = Path(context["project_root"]) / ".smartui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    altered = state["page_builds"][record["id"]]
    if change == "node_x":
        altered["nodes"][0]["x"] += 1
    elif change == "node_text":
        altered["nodes"][1]["text"] = "A A A"
    else:
        altered["width"] += 1
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((ValueError, ProjectError)) as error:
        store.get_page_build(context["project_root"], record["id"])
    assert "inputs" in str(error.value) and "不一致" in str(error.value)
    assert files(record) == original_files


def test_build_and_get_over_real_stdio_return_the_archived_png_bytes(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"],
                                           env={**os.environ, "PYTHONUTF8": "1"})
        async with project_session(parameters) as session:
            listed = await session.list_tools()
            assert len(listed.tools) == 30
            assert {"project_build_layout", "project_get_page_build"} <= {tool.name for tool in listed.tools}
            response = await session.call_tool("project_build_layout", {
                "project_root": context["project_root"], "expected_revision": saved["revision"], "layout_id": "screen",
            })
            assert not response.isError, response
            result = json.loads(response.content[0].text)
            image = response.content[1]
            assert image.type == "image" and image.mimeType == "image/png"
            overview = Path(result["page_build"]["files"]["overview"]["path"])
            expected = overview.read_bytes()
            assert base64.b64decode(image.data) == expected
            fetched = await session.call_tool("project_get_page_build", {
                "project_root": context["project_root"], "build_id": result["page_build"]["id"],
            })
            assert not fetched.isError, fetched
            assert json.loads(fetched.content[0].text) == result
            assert base64.b64decode(fetched.content[1].data) == expected

    asyncio.run(scenario())
