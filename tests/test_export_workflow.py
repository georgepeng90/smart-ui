"""验证当前正式导出、项目锁和 MCP 的整合行为。"""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from mcp import StdioServerParameters
from PIL import Image

from smartui_mcp.pipeline import plan_component
from smartui_mcp.projects import ProjectError
from smartui_mcp.references import register_reference
from test_review_workflow import project, project_session, record_sample_run


def export_item():
    return {"asset_id": "button", "name": "按钮底板", "output_path": "smart-ui/exports/ui/button.png",
            "region": {"x": 0, "y": 0, "width": 8, "height": 6}, "state": "normal", "purpose": "按钮背景",
            "geometry": {"mode": "nine_slice", "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4},
                         "borders": {"left": 2, "top": 2, "right": 2, "bottom": 2}}}


def test_layout_basis_detects_matching_double_size_claims_and_changed_base_node(project, tmp_path):
    store, context = project
    root = context["project_root"]
    context = store.update_ui_settings(root, context["revision"], {"reference_size": {"width": 200, "height": 100}})
    reference = tmp_path / "high-resolution.png"
    Image.new("RGBA", (160, 80)).save(reference)
    context = store.add_reference(root, context["revision"], register_reference(root, "large-guide", str(reference)))
    context = store.update_style(root, context["revision"], "按高分辨率参考制作按钮", [], {}, ["large-guide"])
    icon = store.register_image(root, context["revision"], "placeholder", "设计占位图", str(reference),
                                {"kind": "imported", "description": "布局草稿的占位图"})
    base = {"id": "base", "kind": "image", "asset_id": "placeholder", "x": 0, "y": 0, "width": 80, "height": 40}
    layout = store.save_layout(root, icon["revision"], "menu", "按钮设计", 200, 100, "#FFFFFF", [base])
    basis = {"layout_id": "menu", "node_id": "base"}
    spec = {"program_id": "drawing", "width": 160, "height": 80, "design": {"basis": basis, "width": 160, "height": 80}}
    with pytest.raises(ProjectError, match="80×40.*160×80"):
        store.define_component(root, layout["revision"], "sample", "按钮", spec)
    context = store.define_component(root, layout["revision"], "sample", "按钮", {
        **spec, "width": 80, "height": 40, "design": {"basis": basis, "width": 80, "height": 40}})
    run = record_sample_run(store, context, "at-design-size", (30, 60, 80, 255))
    item = export_item()
    item["geometry"] = {"mode": "horizontal", "content_rect": {"x": 0, "y": 0, "width": 80, "height": 40},
                        "borders": {"left": 4, "right": 4, "top": 0, "bottom": 0}}
    item["region"] = item["geometry"]["content_rect"].copy()
    item["slice_sample_size"] = {"width": 12, "height": 40}
    result = store.export_assets(root, run["revision"], "sample", [item])
    asset = result["export"]["items"][0]["record"]
    assert asset["width"] == 12 and asset["provenance"]["design"]["width"] == 80
    assert asset["geometry"]["borders"] == item["geometry"]["borders"]

    instance = {**base, "id": "wide-instance", "y": 40, "width": 160, "asset_id": "button"}
    saved = store.save_layout(root, result["revision"], "menu", "按钮设计", 200, 100, "#FFFFFF", [base, instance])
    assert store.page_dependencies(root)["assets"]["button"]["source_issue"] is None
    changed = store.save_layout(root, saved["revision"], "menu", "按钮设计", 200, 100, "#FFFFFF",
                                [{**base, "width": 90}, instance])
    assert "90×40" in store.page_dependencies(root)["assets"]["button"]["source_issue"]["action"]
    with pytest.raises(ValueError, match="90×40.*80×40"):
        plan_component(root, "sample", store=store)
    with pytest.raises(ValueError, match="90×40.*80×40"):
        store.export_assets(root, changed["revision"], "sample", [item])


def test_changed_project_resolution_and_old_artwork_require_explicit_design(project):
    store, context = project
    root = context["project_root"]
    spec = context["data"]["components"]["sample"]["spec"]
    result = store.export_assets(root, context["revision"], "sample", [export_item()])
    changed = store.update_ui_settings(root, result["revision"], {"reference_size": {"width": 128, "height": 96}})
    issue = store.page_dependencies(root)["assets"]["button"]["source_issue"]
    assert issue["reason"] == "production_design_unverified" and "reference_size" in issue["action"]
    with pytest.raises(ProjectError, match="design.reference_size"):
        store.define_component(root, changed["revision"], "sample", "按钮", spec)
    draft = store.define_component(root, changed["revision"], "sample", "旧规格",
                                   {key: value for key, value in spec.items() if key != "design"})
    with pytest.raises(ValueError, match="缺少独立的 1×"):
        plan_component(root, "sample", store=store)
    with pytest.raises(ValueError, match="缺少独立的 1×"):
        store.export_assets(root, draft["revision"], "sample", [export_item()])
    updated_design = {key: value for key, value in spec["design"].items() if key != "reference_size"}
    updated = store.define_component(root, draft["revision"], "sample", "按钮", {**spec, "design": updated_design})
    with pytest.raises(ValueError, match="请按已登记的 1× 规格重新绘制"):
        store.export_assets(root, updated["revision"], "sample", [export_item()])


@pytest.mark.parametrize("custom_fields, detail", [
    ({"width": 8, "height": 6}, "basis"),
    ({"sprites": ["button"]}, "design.sprites"),
    ({"sprites": {"button": {"x": 0, "y": 0}}}, "basis"),
    ({"basis": {"layout_id": "menu"}, "width": 8, "height": 6}, "node_id"),
])
def test_reading_legacy_custom_design_reports_missing_evidence(project, custom_fields, detail):
    store, context = project
    root = context["project_root"]
    store.export_assets(root, context["revision"], "sample", [export_item()])
    # 旧版允许组件程序任意定义 design，直接重建这样的磁盘记录，绕过当前的写入检查。
    component_path = Path(root) / "smart-ui/components/sample.json"
    component = json.loads(component_path.read_bytes())
    custom = {"reference_size": {"width": 64, "height": 48}, **custom_fields}
    component["spec"]["design"] = custom
    component_path.write_text(json.dumps(component), encoding="utf-8")
    before = component_path.read_bytes()
    result = store.context(root)
    assert result["data"]["components"]["sample"]["needs_review"]
    assert store.page_dependencies(root)["components"]["sample"]["needs_review"]
    with pytest.raises(ValueError, match="规格待核对.*" + detail):
        plan_component(root, "sample", store=store)
    with pytest.raises(ProjectError, match="规格待核对.*" + detail):
        store.define_component(root, result["revision"], "sample", "样片", component["spec"])
    assert component_path.read_bytes() == before


def test_record_run_checks_actual_png_before_accepting_replacement(project, tmp_path):
    store, context = project
    root = context["project_root"]
    png = tmp_path / "double-size.png"
    Image.new("RGBA", (16, 12)).save(png)
    current = context["data"]["components"]["sample"]["latest_run"]
    with pytest.raises(ProjectError, match=r"1×.*\(8, 6\).*\(16, 12\)"):
        store.record_run(root, context["revision"], "sample", {**current, "preview": str(png), "run_id": "invalid"})
    assert store.context(root) == context


def test_export_updates_current_asset_and_survives_project_move(project, tmp_path):
    store, context = project
    root = context["project_root"]
    first = store.export_assets(root, context["revision"], "sample", [export_item()])
    record = first["export"]
    path = Path(root) / export_item()["output_path"]
    before = path.read_bytes(), path.stat().st_mtime_ns
    again = store.export_assets(root, first["revision"], "sample", [export_item()])
    assert again["export"]["items"] == record["items"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    later = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    updated = store.export_assets(root, later["revision"], "sample", [export_item()])["export"]
    updated_bytes = path.read_bytes()
    assert updated_bytes != before[0]
    assert updated["id"] == record["id"]
    assert updated["manifest"]["path"] == record["manifest"]["path"]
    assert store.get_export(root, record["id"])["export"] == updated
    assert store.get_image(root, "button")["image"]["sha256"] == updated["items"][0]["record"]["sha256"]
    moved = tmp_path / "移动后的项目"
    shutil.move(root, moved)
    restored = store.get_export(str(moved), record["id"])["export"]
    assert Path(restored["manifest"]["path"]).is_relative_to(moved)
    assert Path(restored["items"][0]["record"]["path"]).read_bytes() == updated_bytes
    raw = json.loads((moved / "smart-ui/project.json").read_text(encoding="utf-8"))
    assert not Path(raw["exports"][record["id"]]["manifest"]["path"]).is_absolute()
    current = store.context(str(moved))
    repeat = store.export_assets(str(moved), current["revision"], "sample", [export_item()])
    assert repeat["export"]["items"] == restored["items"]


def test_reexport_can_switch_between_full_region_and_compact_sample(project):
    store, context = project
    root = context["project_root"]
    definition = export_item()
    explicit_full = definition | {"slice_sample_size": {"width": 8, "height": 6}}
    resized = definition | {"slice_sample_size": {"width": 5, "height": 5}}
    path = Path(root) / definition["output_path"]
    result = store.export_assets(root, context["revision"], "sample", [definition])
    original = result["export"]["items"][0]["record"]
    full_bytes, full_stamp = path.read_bytes(), path.stat().st_mtime_ns

    result = store.export_assets(root, result["revision"], "sample", [explicit_full])
    full = result["export"]["items"][0]["record"]
    assert full["sha256"] == original["sha256"] and full["export_signature"] != original["export_signature"]
    assert full["provenance"]["slice_sample_size"] == explicit_full["slice_sample_size"]
    assert path.stat().st_mtime_ns == full_stamp

    result = store.export_assets(root, result["revision"], "sample", [resized])
    sampled = result["export"]["items"][0]["record"]
    assert (sampled["width"], sampled["height"]) == (5, 5)
    assert sampled["id"] == original["id"] and sampled["path"] == original["path"]
    stamp = path.stat().st_mtime_ns
    result = store.export_assets(root, result["revision"], "sample", [resized])
    assert result["export"]["items"][0]["record"] == sampled
    assert path.stat().st_mtime_ns == stamp

    result = store.export_assets(root, result["revision"], "sample", [definition])
    assert result["export"]["items"][0]["record"] == original
    assert path.read_bytes() == full_bytes


def test_export_checks_revision_and_entire_batch_before_write(project):
    store, context = project
    root = Path(context["project_root"])
    definition = export_item()
    invalid = deepcopy(definition) | {"asset_id": "bad", "output_path": "smart-ui/exports/ui/bad.png"}
    invalid["region"]["width"] = 500
    with pytest.raises(ValueError, match="region"):
        store.export_assets(str(root), context["revision"], "sample", [definition, invalid])
    assert not (root / "smart-ui/exports").exists()
    assert "button" not in store.context(str(root))["data"]["images"]
    first = store.export_assets(str(root), context["revision"], "sample", [definition])
    before = (root / "smart-ui/project.json").read_bytes()
    with pytest.raises(ProjectError, match="revision"):
        store.export_assets(str(root), context["revision"], "sample", [definition])
    assert (root / "smart-ui/project.json").read_bytes() == before
    manifest = Path(first["export"]["manifest"]["path"])
    manifest.write_bytes(b"changed")
    with pytest.raises(ProjectError, match="SHA-256"):
        store.get_export(str(root), first["export"]["id"])


def test_git_checkout_preserves_program_and_export_manifest_bytes(project):
    store, context = project
    root = Path(context["project_root"])
    attributes = root / "smart-ui/.gitattributes"
    assert attributes.read_bytes().splitlines()[-1] == b"* -text"
    existing = b"# Existing project rules\r\n*.py text eol=crlf"
    attributes.write_bytes(existing)
    root_attributes = root / ".gitattributes"
    root_rules = b"* text eol=crlf\n"
    root_attributes.write_bytes(root_rules)

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout

    git("init", "-q")
    git("config", "core.autocrlf", "true")
    git("config", "core.safecrlf", "false")
    source = "# 混合换行\r\ndef build(parameters, spec, component_id):\n    return {}\n"
    saved = store.save_program(str(root), context["revision"], "mixed", "混合换行程序", "", source)
    exported = store.export_assets(str(root), saved["revision"], "sample", [export_item()])
    manifest = Path(exported["export"]["manifest"]["path"])
    program = root / "smart-ui/programs/mixed.py"
    assert attributes.read_bytes().startswith(existing + b"\n")
    assert root_attributes.read_bytes() == root_rules

    expected = {path: path.read_bytes() for path in (program, manifest)}
    git("add", "--", "smart-ui")
    for path, payload in expected.items():
        assert git("show", ":" + path.relative_to(root).as_posix()) == payload
        path.unlink()
    git("checkout", "--", "smart-ui")
    assert all(path.read_bytes() == payload for path, payload in expected.items())
    assert store.get_program(str(root), "mixed")["source"] == source
    assert store.get_export(str(root), exported["export"]["id"]) == exported


def test_export_tools_work_over_stdio(project):
    _, context = project

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"],
                                           env={**os.environ, "PYTHONUTF8": "1"})
        async with project_session(parameters) as session:
            definition = export_item() | {"slice_sample_size": {"width": 5, "height": 5}}
            result = await session.call_tool("project_export_assets", {
                "project_root": context["project_root"], "expected_revision": context["revision"],
                "component_id": "sample", "exports": [definition],
            })
            assert not result.isError, result
            exported = json.loads(result.content[0].text)
            record = exported["export"]["items"][0]["record"]
            assert (record["width"], record["height"]) == (5, 5)
            assert record["provenance"]["region"] == definition["region"]
            assert record["provenance"]["source_geometry"] == definition["geometry"]
            fetched = await session.call_tool("project_get_export", {
                "project_root": context["project_root"], "export_id": exported["export"]["id"],
            })
            assert not fetched.isError, fetched
            assert json.loads(fetched.content[0].text)["export"] == exported["export"]

    asyncio.run(scenario())
