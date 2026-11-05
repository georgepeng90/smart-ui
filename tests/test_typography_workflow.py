"""文字规范的持久化、页面依赖及失败写入验证。"""

from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from smartui_mcp.projects import ProjectError, style_sha256
from test_compositions import text_node
from test_review_workflow import project


@pytest.fixture
def typed_project(project, text_node):
    store, context = project
    root = Path(context["project_root"])
    font = root / "Assets/Fonts/body.ttf"
    font.parent.mkdir(parents=True)
    shutil.copyfile(text_node["font_path"], font)
    style = {"name": "正文", "usage": "用于持续阅读的说明文字", "size_id": "regular",
             "font_path": str(font), "line_height": 30, "letter_spacing": 0,
             "fill": {"color": "#FFFFFF", "opacity": 1}, "stroke": None, "shadow": None,
             "evidence": [{"reference_id": "guide", "region": [0, 0, 6, 5], "coordinate_size": [12, 10],
                           "observation": "文字与背景具有清楚的明暗区别", "decision": "用浅色正文在深色面板校准"}]}
    typography = {"description": "测试用角色规范", "design_size": {"width": 256, "height": 128},
                  "size_scale": {"regular": 20, "large": 24},
                  "styles": {"body": style, "title": {**deepcopy(style), "name": "标题", "size_id": "large"}}}
    return store, store.update_typography(str(root), context["revision"], typography)


def save_page(store, context, layout_id, role="body"):
    return store.save_layout(context["project_root"], context["revision"], layout_id, "文字页面", 256, 128,
                             "#123456", [{"id": "label", "kind": "text", "text": "A A", "text_style_id": role,
                                          "x": 8, "y": 8, "width": 160, "height": 80,
                                          "align": "center", "wrap": True, "overflow": "error"}])


def build_page(store, context, layout_id, role="body"):
    saved = save_page(store, context, layout_id, role)
    return store.build_layout(saved["project_root"], saved["revision"], layout_id)


def persistent_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in (root / "smart-ui").rglob("*") if path.is_file()}


def test_typography_persists_separately_without_changing_component_review(typed_project):
    store, context = typed_project
    root = Path(context["project_root"])
    index = json.loads((root / "smart-ui/project.json").read_bytes())
    assert index["typography"] == "smart-ui/typography.json"
    stored = json.loads((root / index["typography"]).read_bytes())
    assert stored["styles"]["body"]["font_path"] == "Assets/Fonts/body.ttf"
    assert not context["data"]["components"]["sample"]["needs_review"]
    component = deepcopy(context["data"]["components"]["sample"])
    source_style = style_sha256(context["data"]["style"])
    changed = deepcopy(context["data"]["typography"])
    changed["styles"]["body"].update(fill={"color": "#33CCFF", "opacity": 0.8},
                                   stroke={"color": "#220044", "opacity": 0.7, "width": 2},
                                   shadow={"color": "#001122", "opacity": 0.6, "offset_x": -2, "offset_y": 3, "blur": 2})
    updated = store.update_typography(str(root), context["revision"], changed)
    assert updated["data"]["components"]["sample"] == component
    assert style_sha256(updated["data"]["style"]) == source_style
    document = (root / "smart-ui/docs/typography.md").read_text(encoding="utf-8")
    assert all(color in document for color in ("#33CCFF", "#220044", "#001122"))
    assert store.context(str(root)) == updated


def test_only_pages_using_changed_visual_values_become_stale(typed_project):
    store, context = typed_project
    root = context["project_root"]
    first = build_page(store, context, "first")
    second = build_page(store, first, "second")
    other = build_page(store, second, "other", "title")
    initial = store.context(root)
    typography = deepcopy(initial["data"]["typography"])
    typography["description"] = "修改说明不改变预览"
    typography["styles"]["body"]["usage"] = "多行说明，同一视觉样式"
    typography["styles"]["unused"] = deepcopy(typography["styles"]["body"])
    same = store.update_typography(root, initial["revision"], typography)
    assert all(status["matches_typography"] for status in same["data"]["page_status"].values())
    typography["size_scale"]["regular"] = 22
    updated = store.update_typography(root, same["revision"], typography)
    statuses = updated["data"]["page_status"]
    assert not statuses["first"]["matches_typography"]
    assert not statuses["second"]["matches_typography"]
    assert statuses["other"]["matches_typography"]
    assert all(status["matches_layout"] and status["matches_images"] for status in statuses.values())
    assert updated["data"]["typography_usage"]["body"] == [
        {"layout_id": "first", "node_id": "label"}, {"layout_id": "second", "node_id": "label"}]
    assert updated["data"]["typography_usage"]["unused"] == []
    assert store.get_page_build(root, "first")["page_build"] == first["page_build"]
    rebuilt = store.build_layout(root, updated["revision"], "first")
    assert rebuilt["matches_typography"]
    assert rebuilt["page_build"]["nodes"][0]["font_size"] == 22
    assert not store.get_page_build(root, "second")["matches_typography"]


def test_referenced_role_deletion_and_revision_conflicts_never_publish(typed_project):
    store, context = typed_project
    saved = save_page(store, context, "screen")
    root = Path(context["project_root"])
    before = persistent_bytes(root)
    typography = deepcopy(context["data"]["typography"])
    del typography["styles"]["body"]
    with pytest.raises(ProjectError, match="screen.*label"):
        store.update_typography(str(root), saved["revision"], typography)
    assert persistent_bytes(root) == before
    with pytest.raises(ProjectError, match="revision"):
        store.update_typography(str(root), context["revision"], context["data"]["typography"])
    assert persistent_bytes(root) == before


def test_snapshot_survives_missing_source_font_and_project_move(typed_project, tmp_path):
    store, context = typed_project
    root = Path(context["project_root"])
    built = build_page(store, context, "screen")
    moved = tmp_path / "新位置"
    shutil.copytree(root, moved)
    font = moved / "Assets/Fonts/body.ttf"
    restored = store.context(str(moved))
    assert restored["revision"] == built["revision"]
    assert Path(restored["data"]["typography"]["styles"]["body"]["font_path"]) == font
    font.unlink()
    snapshot = store.get_page_build(str(moved), "screen")
    assert snapshot["matches_typography"]
    assert Path(snapshot["page_build"]["files"]["overview"]["path"]).is_file()
    before = persistent_bytes(moved)
    with pytest.raises(ValueError, match="字体|font_path"):
        store.build_layout(str(moved), snapshot["revision"], "screen")
    assert persistent_bytes(moved) == before


def test_changed_font_requires_explicit_norm_update(typed_project):
    store, context = typed_project
    built = build_page(store, context, "screen")
    root = Path(context["project_root"])
    current = store.context(str(root))
    typography = deepcopy(current["data"]["typography"])
    font = Path(typography["styles"]["body"]["font_path"])
    font.write_bytes(font.read_bytes() + b"new font identity")
    before = persistent_bytes(root)
    with pytest.raises(ValueError, match="SHA-256"):
        store.update_typography(str(root), current["revision"], typography)
    with pytest.raises(ValueError, match="SHA-256"):
        store.build_layout(str(root), current["revision"], "screen")
    assert persistent_bytes(root) == before
    for style in typography["styles"].values():
        del style["font_sha256"]
    updated = store.update_typography(str(root), current["revision"], typography)
    assert not store.get_page_build(str(root), "screen")["matches_typography"]
    assert store.get_page_build(str(root), "screen")["page_build"] == built["page_build"]
    assert store.build_layout(str(root), updated["revision"], "screen")["matches_typography"]


def test_typography_document_manual_edit_is_not_overwritten(typed_project):
    store, context = typed_project
    root = Path(context["project_root"])
    (root / "smart-ui/docs/typography.md").write_text("用户手工资料", encoding="utf-8")
    before = persistent_bytes(root)
    with pytest.raises(ProjectError, match="受管文档"):
        store.update_typography(str(root), context["revision"], context["data"]["typography"])
    assert persistent_bytes(root) == before


def test_norm_file_must_match_the_published_index(typed_project):
    store, context = typed_project
    root = Path(context["project_root"])
    path = root / "smart-ui/typography.json"
    changed = json.loads(path.read_bytes())
    changed["styles"]["body"]["fill"]["color"] = "#112233"
    path.write_text(json.dumps(changed), encoding="utf-8")
    before = persistent_bytes(root)
    with pytest.raises(ProjectError, match="文字规范摘要不一致"):
        store.context(str(root))
    with pytest.raises(ProjectError, match="文字规范摘要不一致"):
        store.update_typography(str(root), context["revision"], context["data"]["typography"])
    assert persistent_bytes(root) == before


@pytest.mark.parametrize("field", ["text_style_id", "text_style_sha256"])
def test_invalid_build_metadata_reports_the_node_before_deriving_status(typed_project, field):
    store, context = typed_project
    built = build_page(store, context, "screen")
    root = Path(context["project_root"])
    index = root / "smart-ui/project.json"
    state = json.loads(index.read_bytes())
    del state["page_builds"]["screen"]["nodes"][0][field]
    index.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ProjectError, match="文字|样式"):
        store.get_page_build(str(root), built["page_build"]["id"])


def test_mcp_updates_three_paints_and_builds_role_references(typed_project):
    store, context = typed_project
    root = context["project_root"]

    async def run():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(Path(root) / "missing-cli.exe")})
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(tool_name, **arguments):
                    result = await session.call_tool(tool_name, {"project_root": root, **arguments})
                    assert not result.isError, result.content
                    return json.loads(result.content[0].text)

                norm = deepcopy(context["data"]["typography"])
                paints = {"fill": {"color": "#33CCFF", "opacity": 0.8},
                          "stroke": {"width": 2, "color": "#220044", "opacity": 0.7},
                          "shadow": {"offset_x": -2, "offset_y": 3, "blur": 2,
                                     "color": "#001122", "opacity": 0.6}}
                norm["styles"]["body"].update(paints)
                updated = await call("project_update_typography", expected_revision=context["revision"], typography=norm)
                saved = await call("project_save_layout", expected_revision=updated["revision"],
                    layout_id="screen", name="文字效果", width=256, height=128, background="#123456",
                    nodes=[{"id": "label", "kind": "text", "text_style_id": "body", "text": "A A",
                            "x": 8, "y": 8, "width": 160, "height": 80, "align": "center",
                            "wrap": True, "overflow": "error"}])
                built = await call("project_build_layout", expected_revision=saved["revision"], layout_id="screen")
                snapshot = await call("project_get_page_build", build_id="screen")
                assert snapshot["matches_typography"]
                assert {key: snapshot["page_build"]["nodes"][0][key] for key in paints} == paints
                current = await call("project_context")
                assert current["revision"] == built["revision"]
                assert current["typography_usage"]["body"] == [{"layout_id": "screen", "node_id": "label"}]
                assert current["page_status"]["screen"]["matches_bindings"]

    asyncio.run(run())
