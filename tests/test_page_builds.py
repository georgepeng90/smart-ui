"""浏览器页面的固定输出、当前输入更新和文件完整性。"""

from copy import deepcopy
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from fontTools.ttLib import TTFont
from mcp import StdioServerParameters
from PIL import Image
import pytest

from smartui_mcp.projects import ProjectError
from test_compositions import text_node
from test_export_workflow import export_item
from test_page_layouts import (
    exported_project, image_node, label, project_bytes, project_font, save, typography_project, typography_spec,
)
from test_review_workflow import project, project_session, record_sample_run


def build(store, saved):
    return store.build_layout(saved["project_root"], saved["revision"], "screen")


def files(record):
    return {key: Path(value["path"]).read_bytes() for key, value in record["files"].items()}


def test_build_uses_real_browser_and_replaces_the_current_preview(typography_project, label):
    store, context = typography_project
    other_label = {**label, "id": "second-label", "y": 18}
    adaptation = {"first": {"anchor_min": [0, 1], "anchor_max": [0, 1], "pivot": [0, 1]}}
    saved = store.save_layout(
        context["project_root"], context["revision"], "screen", "Screen", 64, 48, "#000000",
        [image_node("first"), image_node("second", x=25), label, other_label], unity_adaptation=adaptation)
    before_assets = deepcopy(store.context(context["project_root"])["data"]["images"])
    first = build(store, saved)
    record = first["page_build"]
    assert record["id"] == record["layout_id"] == "screen"
    assert record["layout_sha256"] == saved["layout"]["sha256"]
    assert (record["width"], record["height"]) == (64, 48)
    assert first["matches_layout"] and first["matches_bindings"]
    assert record["renderer"]["engine"] == "chromium"
    assert record["renderer"]["version"]
    assert record["renderer"]["device_scale_factor"] == 1
    assert [font["status"] for font in record["renderer"]["fonts"]] == ["loaded"]
    assert len(record["renderer"]["nodes"]) == 4
    assert record["nodes"][0]["snapshot_key"] == record["nodes"][1]["snapshot_key"]
    assert record["nodes"][2]["snapshot_key"] == record["nodes"][3]["snapshot_key"]
    assert len({node["snapshot_key"] for node in record["nodes"]}) == 2
    for item in record["files"].values():
        path = Path(item["path"])
        assert path.is_relative_to(Path(context["project_root"]) / "smart-ui/previews/pages" / record["id"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    with Image.open(record["files"]["overview"]["path"]) as screenshot:
        assert screenshot.size == (64, 48)
    inputs = json.loads(Path(record["files"]["inputs"]["path"]).read_text(encoding="utf-8"))
    assert inputs["nodes"][0]["source"] == record["nodes"][0]["snapshot_key"]
    assert inputs["unity_adaptation"] == record["unity_adaptation"] == adaptation
    assert inputs["nodes"][2]["font_path"] == record["nodes"][2]["snapshot_key"]
    assert [node["text_style_id"] for node in inputs["nodes"][2:]] == ["button-label", "button-label"]
    assert inputs["nodes"][2]["text_style_sha256"] == inputs["nodes"][3]["text_style_sha256"]
    assert inputs["nodes"][2]["text_style_sha256"] == record["nodes"][2]["text_style_sha256"]
    assert inputs["nodes"][2]["font_size"] == 20
    assert inputs["nodes"][2]["fill"] == {"color": "#FF0000", "opacity": 1}
    assert inputs["nodes"][2]["stroke"] is None
    assert inputs["nodes"][2]["shadow"] is None
    old_files = files(record)
    second = build(store, first)
    assert second["page_build"]["id"] == record["id"]
    assert list((Path(context["project_root"]) / "smart-ui/previews/pages").iterdir()) == [
        Path(record["files"]["overview"]["path"]).parent]
    assert files(record) == old_files
    assert second["page_build"]["files"]["overview"]["sha256"] == record["files"]["overview"]["sha256"]
    assert second["page_build"]["files"]["html"]["sha256"] == record["files"]["html"]["sha256"]
    current = store.context(context["project_root"])
    assert current["data"]["images"] == before_assets
    assert current["data"]["layouts"]["screen"] == saved["layout"]
    assert set(current["data"]["page_builds"]) == {"screen"}
    annotated = store.build_layout(current["project_root"], current["revision"], "screen", annotate=True)["page_build"]
    assert annotated["layout_sha256"] == record["layout_sha256"]
    assert annotated["files"]["overview"]["sha256"] == record["files"]["overview"]["sha256"]
    assert annotated["files"]["annotated"]["sha256"] != record["files"]["overview"]["sha256"]
    verified = store.get_page_build(current["project_root"], "screen")
    assert verified["page_build"]["files"]["annotated"] == annotated["files"]["annotated"]


def test_rebuild_uses_the_new_asset_at_every_instance(exported_project):
    store, context = exported_project
    root = context["project_root"]
    saved = save(store, context, [image_node("first"), image_node("second", x=25)])
    first = build(store, saved)
    old = first["page_build"]
    later = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    exported = store.export_assets(root, later["revision"], "sample", [export_item()])
    stale = store.get_page_build(root, old["id"])
    assert stale["matches_layout"] and not stale["matches_bindings"]
    assert stale["page_build"] == old
    second = build(store, exported)
    current = second["page_build"]
    assert second["matches_layout"] and second["matches_bindings"]
    assert current["id"] == old["id"]
    digest = store.context(root)["data"]["images"]["button"]["sha256"]
    assert [node["sha256"] for node in current["nodes"]] == [digest, digest]
    assert [node["snapshot_key"] for node in current["nodes"]] == [node["snapshot_key"] for node in old["nodes"]]
    assert digest != old["nodes"][0]["sha256"]
    assert current["files"]["overview"]["sha256"] != old["files"]["overview"]["sha256"]


def test_preview_resource_names_follow_identity_through_layout_edits(typography_project, label, typography_spec):
    store, context = typography_project
    root = context["project_root"]
    image = Path(store.context(root)["data"]["images"]["button"]["path"])
    second_image = image_node("second-image", asset_id="other-button")
    current = store.register_image(root, context["revision"], second_image["asset_id"], "同内容的第二张图片",
                                   str(image), {"kind": "imported", "description": "验证同内容的独立图像身份"})
    style = typography_spec["styles"]["button-label"]
    font = Path(style["font_path"])
    second_font = font.with_name("other") / font.name
    second_font.parent.mkdir()
    shutil.copyfile(font, second_font)
    typography_spec["styles"].update({
        "shared-font": {**style, "line_height": 25},
        "other-font": {**style, "font_path": str(second_font)},
    })
    current = store.update_typography(root, current["revision"], typography_spec)
    button = image_node()
    shared_label = {**label, "id": "shared-label", "text_style_id": "shared-font"}
    second_label = {**label, "id": "second-label", "text_style_id": "other-font"}
    first = build(store, save(store, current, [button, label, shared_label]))

    def resources(result):
        return {node["id"]: node["snapshot_key"] for node in result["page_build"]["nodes"]}

    initial = resources(first)
    assert initial[label["id"]] == initial[shared_label["id"]]
    inserted = build(store, save(store, first, [second_image, second_label, label, shared_label, button]))
    keys = resources(inserted)
    assert {key: keys[key] for key in initial} == initial
    assert len(set(keys.values())) == 4
    inserted_record = inserted["page_build"]
    digests = {node["id"]: inserted_record["files"][node["snapshot_key"]]["sha256"]
               for node in inserted_record["nodes"]}
    assert digests[second_image["id"]] == digests[button["id"]]
    assert digests[second_label["id"]] == digests[label["id"]]
    first_family = first["page_build"]["renderer"]["fonts"][0]["family"]
    other_family, shared_family = (font["family"] for font in inserted_record["renderer"]["fonts"])
    assert shared_family == first_family and other_family != first_family

    with TTFont(font) as face:
        face["name"].setName("Updated test font", 1, 3, 1, 0x409)
        face.save(font)
    current = store.update_typography(root, inserted["revision"], typography_spec)
    rebuilt = build(store, save(store, current, [button, label, second_image]))
    surviving = resources(rebuilt)
    assert surviving == {key: keys[key] for key in surviving}
    record = rebuilt["page_build"]
    assert record["renderer"]["fonts"][0]["family"] == first_family
    assert record["files"][surviving[label["id"]]]["sha256"] != digests[label["id"]]
    assert not Path(inserted_record["files"][keys[second_label["id"]]]["path"]).exists()
    assert store.get_page_build(root, record["id"]) == rebuilt


def test_export_geometry_change_marks_the_preview_stale_even_when_pixels_match(exported_project):
    store, context = exported_project
    root = context["project_root"]
    first = build(store, save(store, context, [image_node()]))
    old = first["page_build"]["nodes"][0]
    item = export_item()
    item["geometry"] = {"mode": "stretch", "content_rect": item["geometry"]["content_rect"]}
    updated = store.export_assets(root, first["revision"], "sample", [item])
    assert updated["export"]["items"][0]["record"]["sha256"] == old["sha256"]
    stale = store.get_page_build(root, first["page_build"]["id"])
    assert stale["matches_layout"] and not stale["matches_bindings"]
    rebuilt = build(store, updated)
    assert rebuilt["matches_layout"] and rebuilt["matches_bindings"]
    assert rebuilt["page_build"]["nodes"][0]["geometry"] == item["geometry"]


def test_rebuild_replaces_changed_layout_and_removes_unused_input_files(typography_project, label):
    store, context = typography_project
    first = build(store, save(store, context, [image_node(x=2), label]))
    previous = first["page_build"]
    copied_font = Path(previous["nodes"][1]["font_path"])
    assert copied_font.is_file()
    saved = save(store, first, [image_node(x=20)])
    stale = store.get_page_build(context["project_root"], previous["id"])
    assert not stale["matches_layout"]
    rebuilt = build(store, saved)
    assert rebuilt["page_build"]["id"] == previous["id"]
    assert rebuilt["page_build"]["layout_sha256"] == saved["layout"]["sha256"]
    assert rebuilt["page_build"]["nodes"][0]["x"] == 20
    assert rebuilt["matches_layout"] and rebuilt["matches_bindings"]
    assert not copied_font.exists()


def test_current_preview_files_are_independent_of_the_original_image_and_font(typography_project, label, project_font):
    store, context = typography_project
    saved = save(store, context, [image_node(), label])
    built = build(store, saved)
    before = files(built["page_build"])
    state = store.context(context["project_root"])["data"]
    Path(state["images"]["button"]["path"]).unlink()
    project_font.unlink()
    raw = project_bytes(built)
    restored = store.get_page_build(context["project_root"], built["page_build"]["id"])
    assert restored["page_build"] == built["page_build"]
    assert files(restored["page_build"]) == before
    assert restored["matches_layout"] and restored["matches_bindings"]
    assert project_bytes(built) == raw


def test_every_preview_file_is_verified_before_returning_the_current_build(typography_project, label):
    store, context = typography_project
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


def test_current_build_survives_project_move_without_the_original_font(typography_project, label, project_font, tmp_path):
    store, context = typography_project
    built = build(store, save(store, context, [image_node(), label]))
    record = built["page_build"]
    original = files(record)
    project_font.unlink()
    moved = tmp_path / "移动后的页面快照工程"
    shutil.move(context["project_root"], moved)
    restored = store.get_page_build(str(moved), record["id"])
    assert files(restored["page_build"]) == original
    assert restored["page_build"]["layout_sha256"] == record["layout_sha256"]
    assert restored["matches_layout"] and restored["matches_bindings"]
    for item in restored["page_build"]["files"].values():
        assert Path(item["path"]).is_relative_to(moved)
    for node in restored["page_build"]["nodes"]:
        assert Path(node["source" if node["kind"] == "image" else "font_path"]).is_relative_to(moved)


def test_revision_conflict_leaves_no_build_or_state_change(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])
    root = Path(context["project_root"])
    raw = project_bytes(saved)
    with pytest.raises(ProjectError, match="revision"):
        build(store, context)
    assert project_bytes(saved) == raw
    assert not (root / "smart-ui/previews/pages").exists()


def test_overflow_failure_preserves_the_current_build_and_project_state(typography_project, label):
    store, context = typography_project
    first = build(store, save(store, context, [image_node()]))
    before = files(first["page_build"])
    label.update(text="A A A A A A A A", width=10, height=15, wrap=False, overflow="error")
    saved = save(store, first, [label])
    raw = project_bytes(saved)
    with pytest.raises(ValueError, match="超出文本框"):
        build(store, saved)
    assert project_bytes(saved) == raw
    assert files(first["page_build"]) == before


def test_missing_required_snapshot_file_record_is_a_schema_error(exported_project):
    store, context = exported_project
    built = build(store, save(store, context, [image_node()]))
    build_id = built["page_build"]["id"]
    path = Path(context["project_root"]) / "smart-ui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["page_builds"][build_id]["files"]["html"]
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((ValueError, ProjectError), match="快照文件集合不完整"):
        store.get_page_build(context["project_root"], build_id)


def test_snapshot_file_reference_must_match_the_manifest_key(exported_project):
    store, context = exported_project
    built = build(store, save(store, context, [image_node()]))
    build_id = built["page_build"]["id"]
    path = Path(context["project_root"]) / "smart-ui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    altered = state["page_builds"][build_id]
    node = altered["nodes"][0]
    filename = node["snapshot_key"]
    node["snapshot_key"] = "different-name.png"
    altered["files"][node["snapshot_key"]] = altered["files"].pop(filename)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((ValueError, ProjectError), match="节点与快照文件不一致"):
        store.get_page_build(context["project_root"], build_id)


@pytest.mark.parametrize("change", ["node_x", "node_text", "style_digest", "page_width"])
def test_preview_metadata_must_match_the_rendered_inputs(typography_project, label, change):
    store, context = typography_project
    built = build(store, save(store, context, [image_node(), label]))
    record = built["page_build"]
    original_files = files(record)
    path = Path(context["project_root"]) / "smart-ui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    altered = state["page_builds"][record["id"]]
    if change == "node_x":
        altered["nodes"][0]["x"] += 1
    elif change == "node_text":
        altered["nodes"][1]["text"] = "A A A"
    elif change == "style_digest":
        altered["nodes"][1]["text_style_sha256"] = "0" * 64
    else:
        altered["width"] += 1
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((ValueError, ProjectError)) as error:
        store.get_page_build(context["project_root"], record["id"])
    assert "inputs" in str(error.value) and "不一致" in str(error.value)
    assert files(record) == original_files


def test_build_and_get_over_real_stdio_return_the_current_png_bytes(exported_project):
    store, context = exported_project
    saved = save(store, context, [image_node()])

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"],
                                           env={**os.environ, "PYTHONUTF8": "1"})
        async with project_session(parameters) as session:
            listed = await session.list_tools()
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
