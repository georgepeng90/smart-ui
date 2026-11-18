"""在正常安装的 PhotoCraft 中验证创建、局部更新、正式导出和页面构建。"""

import argparse
import json
from pathlib import Path
import runpy
import shutil
import sys
from tempfile import TemporaryDirectory

from PIL import Image, ImageChops, ImageColor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smartui_mcp.client import PhotoCraftClient
from smartui_mcp.photocraft import read_document
from smartui_mcp.pipeline import draw_component, plan_component
from smartui_mcp.projects import ProjectStore


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_preview(path):
    with Image.open(path) as image:
        return image.convert("RGBA")


def verify_outline_edit(client, initial_document, initial_preview, edited, layer_ids, original_outline, theme):
    surface_id = layer_ids["surface"]
    marker_id = layer_ids["marker"]
    before = {node["id"]: node for node in initial_document["nodes"]}
    after = {node["id"]: node for node in read_document(edited["document"])["nodes"]}
    original_surface = before[surface_id]["manifest"]
    edited_surface = after[surface_id]["manifest"]
    original_shape, edited_shape = original_surface["content"], edited_surface["content"]
    require(after[surface_id]["kind"] == "shape" and
            {key: value for key, value in original_shape.items() if key not in ("cache", "stroke")} ==
            {key: value for key, value in edited_shape.items() if key not in ("cache", "stroke")},
            "改描边色后原生形状的填充或几何发生变化")
    require(original_surface["effects"] == edited_surface["effects"] and
            before[marker_id]["sha256"] == after[marker_id]["sha256"], "改描边色影响了投影样式或独立状态标记")
    original_stroke, edited_stroke = original_shape["stroke"], edited_shape["stroke"]
    require(original_stroke["paint"] != edited_stroke["paint"] and
            {key: value for key, value in original_stroke.items() if key != "paint"} ==
            {key: value for key, value in edited_stroke.items() if key != "paint"},
            "形状描边未独立改色，或宽度、对齐等属性发生变化")

    edited_preview = read_preview(edited["preview"])
    center_x = edited_preview.width // 2
    edge, interior, marker = (center_x, 5), (center_x, 32), (edited_preview.width - 17, 15)
    shadow = (center_x, edited_preview.height - 3)
    require(initial_preview.getpixel(edge) == ImageColor.getcolor(original_outline, "RGBA") and
            edited_preview.getpixel(edge) == ImageColor.getcolor(theme["outline"], "RGBA"),
            "保存重开后的描边像素未呈现预期颜色")
    require(initial_preview.getpixel(interior) == edited_preview.getpixel(interior) == ImageColor.getcolor(theme["action"], "RGBA") and
            initial_preview.getpixel(marker) == edited_preview.getpixel(marker) == ImageColor.getcolor(theme["selection"], "RGBA") and
            initial_preview.getpixel(shadow) == edited_preview.getpixel(shadow) and
            initial_preview.getchannel("A").tobytes() == edited_preview.getchannel("A").tobytes(),
            "调色影响了底面、状态标记、投影或透明轮廓")
    recolored_region = ImageChops.difference(initial_preview, edited_preview).getbbox(alpha_only=False)
    require(recolored_region == (4, 4, edited_preview.width - 4, edited_preview.height - 4),
            "改色像素的边界与底板形状不一致")

    with TemporaryDirectory(prefix="smartui-verify-outline-") as directory:
        scratch = Path(directory)
        shutil.copyfile(edited["document"], scratch / "edited.pcraft")
        with client.session(scratch) as session:
            session.call("doc.open", {"path": "edited.pcraft"})
            session.call("engine.execute", {"command": "layer.setEffectsVisible",
                                              "params": {"layer": surface_id, "index": 0, "visible": False}})
            session.call("doc.save", {"path": "hidden.pcraft"})
            session.call("doc.close")
            session.call("doc.open", {"path": "hidden.pcraft"})
            session.call("doc.render", {"path": "hidden.png", "maxSide": 0})
        hidden_preview = read_preview(scratch / "hidden.png")
        hidden_surface = next(node["manifest"] for node in read_document(scratch / "hidden.pcraft")["nodes"]
                              if node["id"] == surface_id)
    hidden_item, = hidden_surface["effects"]["items"]
    require(not hidden_item["DropShadow"]["common"]["enabled"] and hidden_surface["content"] == edited_shape and
            all(hidden_preview.getpixel(point) == edited_preview.getpixel(point) for point in (edge, interior, marker)),
            "关闭投影并重开后，形状、填充、描边或状态标记发生变化")
    shadow_alpha = edited_preview.getpixel(shadow)[3]
    require(shadow_alpha > 0 and hidden_preview.getpixel(shadow)[3] == 0, "关闭投影并重开后，投影像素仍然可见")
    return {"original_color": original_outline, "updated_color": theme["outline"],
            "stroke_width": edited_stroke["width"], "stroke_alignment": edited_stroke["align"],
            "changed_pixel_bounds": recolored_region, "shadow_sample": shadow,
            "shadow_alpha_before_hiding": shadow_alpha, "shadow_alpha_after_hiding": 0}


def verify_slice_pixels(original, sample, baseline_page, rebuilt_page, origin, background, theme):
    require(original.size == (200, 64) and sample.size == (64, 64), "正式导出图宽高与请求不符")
    regions = {
        "left_cap": ((0, 0, 24, 64), (0, 0, 24, 64)),
        "middle": ((24, 0, 40, 64), (24, 0, 40, 64)),
        "right_cap": ((176, 0, 200, 64), (40, 0, 64, 64)),
    }
    for region, (original_box, sample_box) in regions.items():
        require(original.crop(original_box).tobytes() == sample.crop(sample_box).tobytes(), f"收短改变了正式 PNG 区域的像素：{region}")

    require(baseline_page.size == rebuilt_page.size == (400, 160), "浏览器预览尺寸变化")
    difference = ImageChops.difference(baseline_page, rebuilt_page).getbbox(alpha_only=False)
    require(difference is None, f"收短 PNG 还原后的浏览器页面像素发生变化：{difference}")
    points = {"left_outline": (8, 8), "right_outline": (191, 8), "fill": (100, 32),
              "shadow": (100, 61), "marker": (183, 15), "transparent_corner": (0, 0)}
    pixels = {}
    for label, (x, y) in points.items():
        position = (origin[0] + x, origin[1] + y)
        pixels[label] = {"source_position": (x, y), "source_rgba": original.getpixel((x, y)),
                         "page_position": position, "page_rgba": rebuilt_page.getpixel(position)}
    for label, color in (("left_outline", theme["outline"]), ("right_outline", theme["outline"]),
                         ("fill", theme["action"]), ("marker", theme["selection"])):
        expected = ImageColor.getcolor(color, "RGBA")
        require(pixels[label]["source_rgba"] == pixels[label]["page_rgba"] == expected, f"浏览器样点与预期组件颜色不符：{label}")
    background_rgba = ImageColor.getcolor(background, "RGBA")
    require(pixels["transparent_corner"]["source_rgba"][3] == 0 and
            pixels["transparent_corner"]["page_rgba"] == background_rgba,
            "透明角没有保留，或浏览器未显示原背景")
    require(0 < pixels["shadow"]["source_rgba"][3] < 255 and pixels["shadow"]["page_rgba"] != background_rgba,
            "半透明投影未保留，或浏览器页面未显示投影")
    return {"identical_png_regions": regions, "identical_page_rgba": True, "samples": pixels}


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
                                 "验证原生形状、投影样式和局部更新", example_path.read_text(encoding="utf-8"))
        spec = {"program_id": "semantic-palette", "theme": "lagoon", "state": "selected", "width": 200, "height": 64}
        ctx = store.define_component(root, ctx["revision"], "action", "操作背景", spec)
        report["plan"] = plan_component(root, "action", store=store)
        first = draw_component(root, ctx["revision"], "action", client=client, store=store)
        report["created"] = first
        original_document = read_document(first["document"])
        original_preview = read_preview(first["preview"])
        ctx = store.context(root)
        layer_ids = ctx["data"]["components"]["action"]["latest_run"]["layer_ids"]
        theme = parameters["themes"]["lagoon"]
        original_outline = theme["outline"]
        theme["outline"] = "#A34E36"
        ctx = store.update_style(root, ctx["revision"], "通用配色及状态验证", [], parameters, [])
        edited = draw_component(root, ctx["revision"], "action", layers=["surface"], client=client, store=store)
        report["edited"] = edited
        require(edited["verification"]["unchanged_other_nodes"], "局部更新影响了未选节点")
        require(edited["verification"]["target_content_changed"], "目标节点未产生实际变化")
        require(all(edited["verification"][key] for key in
                    ("reopened", "stable_node_ids", "stable_pixels", "stable_properties")), "保存重开检查失败")
        report["outline_edit"] = verify_outline_edit(client, original_document, original_preview, edited, layer_ids,
                                                     original_outline, theme)
        ctx = store.context(root)
        export_request = {
            "asset_id": "action-background", "name": "操作背景", "output_path": "smart-ui/exports/ui/action.png",
            "state": "selected", "purpose": "验证正式交付",
            "region": {"x": 0, "y": 0, "width": 200, "height": 64},
            "geometry": {"mode": "horizontal", "content_rect": {"x": 0, "y": 0, "width": 200, "height": 64},
                         "borders": {"left": 24, "right": 24, "top": 0, "bottom": 0}},
        }
        exported = store.export_assets(root, ctx["revision"], "action", [export_request])
        origin, background = (100, 48), "#182438"
        saved = store.save_layout(root, exported["revision"], "verification", "原生资产页面验证", 400, 160, background, [{
            "id": "action-instance", "kind": "image", "asset_id": "action-background",
            "x": origin[0], "y": origin[1], "width": 200, "height": 64}])
        built = store.build_layout(root, saved["revision"], "verification")
        require(built["matches_layout"] and built["matches_bindings"], "页面构建未使用当前布局和资产")
        initial_build = built["page_build"]
        original_asset = exported["export"]["items"][0]["record"]
        original_overview = initial_build["files"]["overview"]
        exported_png = Path(original_asset["path"])
        evidence = report_path.parent / "slice-comparison"
        evidence.mkdir(parents=True)
        original_png = evidence / "original-200x64.png"
        baseline_page = evidence / "original-page.png"
        shutil.copyfile(exported_png, original_png)
        shutil.copyfile(original_overview["path"], baseline_page)
        report["slice_comparison"] = {
            "original_png": str(original_png), "original_png_sha256": original_asset["sha256"],
            "original_page": str(baseline_page), "original_page_sha256": original_overview["sha256"],
        }

        compact_request = export_request | {"slice_sample_size": {"width": 64, "height": 64}}
        compact = store.export_assets(root, built["revision"], "action", [compact_request])
        report["export"] = compact["export"]
        require(compact["export"]["manifest"]["path"] == exported["export"]["manifest"]["path"], "重导出未更新同一清单")
        stale = store.get_page_build(root, "verification")
        require(stale["matches_layout"] and not stale["matches_images"], "切片导出后旧页面未标记资产变化")
        rebuilt = store.build_layout(root, compact["revision"], "verification")
        final_build = report["page_build"] = rebuilt["page_build"]
        require(rebuilt["matches_layout"] and rebuilt["matches_bindings"] and
                final_build["layout_sha256"] == initial_build["layout_sha256"] and
                final_build["nodes"][0]["sha256"] != initial_build["nodes"][0]["sha256"],
                "重建页面未在同一布局中绑定新的切片 PNG")
        report["slice_comparison"].update(verify_slice_pixels(
            read_preview(original_png), read_preview(exported_png), read_preview(baseline_page),
            read_preview(final_build["files"]["overview"]["path"]), origin, background, theme))
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
