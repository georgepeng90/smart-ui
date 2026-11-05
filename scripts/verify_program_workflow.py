"""在正常安装的 PhotoCraft 中验证创建、局部更新、正式导出和页面构建。"""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smartui_mcp.client import PhotoCraftClient
from smartui_mcp.pipeline import draw_component, plan_component
from smartui_mcp.projects import ProjectStore


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(output_root):
    if not output_root.is_absolute():
        raise ValueError("--output-root 必须是绝对路径")
    if output_root.exists():
        raise FileExistsError(f"验证只创建新项目，拒绝覆盖：{output_root}")
    client = PhotoCraftClient()
    backend = client.status()
    output_root.mkdir(parents=True, exist_ok=False)
    report_path = output_root / ".smartui" / "report.json"
    report = {"status": "running", "backend": backend,
              "scope": "真实原生制作及通用资产页面链路；不代替用户美术确认"}
    try:
        store = ProjectStore()
        root = str(output_root)
        ctx = store.create(root, "native-workflow-verification", "PhotoCraft 通用工作流验证")
        example_path = ROOT / "examples/drawing-programs/semantic_palette.py"
        parameters = runpy.run_path(str(example_path))["example_parameters"]()
        ctx = store.update_style(root, ctx["revision"], "通用配色及状态验证", [], parameters, [])
        ctx = store.save_program(root, ctx["revision"], "semantic-palette", "通用状态程序",
                                 "验证原生形状、共享颜色和局部更新", example_path.read_text(encoding="utf-8"))
        spec = {"program_id": "semantic-palette", "theme": "lagoon", "state": "available", "width": 200, "height": 64}
        ctx = store.define_component(root, ctx["revision"], "action", "操作背景", spec)
        report["plan"] = plan_component(root, "action", store=store)
        first = draw_component(root, ctx["revision"], "action", client=client, store=store)
        report["created"] = first
        original_png = hashlib.sha256(Path(first["preview"]).read_bytes()).hexdigest()
        ctx = store.context(root)
        parameters["themes"]["lagoon"]["action"] = "#408B70"
        ctx = store.update_style(root, ctx["revision"], "通用配色及状态验证", [], parameters, [])
        edited = draw_component(root, ctx["revision"], "action", layers=["surface"], client=client, store=store)
        report["edited"] = edited
        require(edited["verification"]["unchanged_other_nodes"], "局部更新影响了未选节点")
        require(edited["verification"]["target_content_changed"], "目标节点未产生实际变化")
        require(hashlib.sha256(Path(edited["preview"]).read_bytes()).hexdigest() != original_png, "更新未改变预览")
        require(all(edited["verification"][key] for key in
                    ("reopened", "stable_node_ids", "stable_pixels", "stable_properties")), "保存重开检查失败")
        ctx = store.context(root)
        exported = store.export_assets(root, ctx["revision"], "action", [{
            "asset_id": "action-background", "name": "操作背景", "output_path": "smart-ui/exports/ui/action.png",
            "state": "available", "purpose": "验证正式交付",
            "region": {"x": 0, "y": 0, "width": 200, "height": 64}, "geometry": {"mode": "contain", "content_rect": {"x": 0, "y": 0, "width": 200, "height": 64}}}])
        report["export"] = exported["export"]
        saved = store.save_layout(root, exported["revision"], "verification", "原生资产页面验证", 400, 160, "#182438", [{
            "id": "action-instance", "kind": "image", "asset_id": "action-background",
            "x": 100, "y": 48, "width": 200, "height": 64}])
        built = store.build_layout(root, saved["revision"], "verification")
        report["page_build"] = built["page_build"]
        require(built["matches_layout"] and built["matches_bindings"], "页面构建未使用当前布局和资产")
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True, help="尚不存在的业务工程绝对路径")
    run(parser.parse_args().output_root)
