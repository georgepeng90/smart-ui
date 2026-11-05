"""用合成素材验证正式导出、资产复用、文字样式和浏览器页面重建。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from PIL import Image, ImageDraw, ImageFilter


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from smartui_mcp.layout import ProjectLayout
from smartui_mcp.projects import ProjectStore, spec_sha256, style_sha256


SCOPE = ("本脚本用 Pillow 合成组件 PNG，并登记 status=drawn 的模拟运行。"
         "模拟 .pcraft 文件只是明确标记的占位文本，不能用 PhotoCraft 打开。"
         "本次仅验证通用工具的正式导出、资产复用、文字样式和浏览器页面重建，"
         "不验证 PhotoCraft 绘制、源文件重开或美术质量。")
PROGRAM = '''def build(parameters, spec, component_id):
    # 这是工具链验证用的空绘制计划；合成 PNG 由验证脚本生成，不调用 PhotoCraft。
    return {"schema_version": 1, "width": spec["width"], "height": spec["height"],
            "nodes": [{"key": "fixture", "name": "模拟来源", "kind": "pixel", "parent": "root", "strokes": []}]}
'''


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    return str(path)


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _sprite(width, height, fill, edge, highlight, radius):
    """合成带透明外扩、柔和阴影和固定圆角的验证图素。"""
    scale = 3
    size = ((width + 24) * scale, (height + 24) * scale)
    image = Image.new("RGBA", size)
    shadow = Image.new("RGBA", size)
    draw = ImageDraw.Draw(shadow)
    draw.rounded_rectangle((12 * scale, 16 * scale, (width + 12) * scale, (height + 16) * scale),
                           radius=radius * scale, fill=(5, 16, 36, 90))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3 * scale)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12 * scale, 12 * scale, (width + 12) * scale, (height + 12) * scale),
                           radius=radius * scale, fill=fill, outline=edge, width=3 * scale)
    draw.rounded_rectangle((17 * scale, 17 * scale, (width + 7) * scale, (height + 7) * scale),
                           radius=(radius - 4) * scale, outline=highlight, width=2 * scale)
    return image.resize((width + 24, height + 24), Image.Resampling.LANCZOS)


def _preview(path, green):
    image = Image.new("RGBA", (660, 330))
    panel = _sprite(320, 280, "#F3F7FC", "#6C829D", "#FFFFFF", 22)
    palette = ("#38B77B", "#185F43", "#ABF0C9") if green else ("#438EFF", "#234E86", "#ADD2FF")
    button = _sprite(232, 72, *palette, radius=16)
    image.alpha_composite(panel, (0, 0))
    image.alpha_composite(button, (384, 0))
    image.save(path)


def _record_simulated_run(store, context, run_id, green):
    root = Path(context["project_root"])
    component_id = "fixture-sheet"
    directory = root / "smart-ui" / "components" / component_id
    document = root / "smart-ui" / "sources" / f"{component_id}.pcraft"
    preview = root / "smart-ui" / "previews" / "components" / f"{component_id}.png"
    for folder in (directory, document.parent, preview.parent):
        folder.mkdir(parents=True, exist_ok=True)
    state = context["data"]
    component = state["components"][component_id]
    program = store.get_program(str(root), component["spec"]["program_id"])
    document.write_text("SMARTUI SYNTHETIC FIXTURE; NOT A PHOTOCRAFT DOCUMENT\n" + SCOPE, encoding="utf-8")
    _preview(preview, green)
    plan = {"schema_version": 1, "width": 660, "height": 330,
            "nodes": [{"key": "fixture", "name": "模拟来源", "kind": "pixel", "parent": "root", "strokes": []}]}
    run = {"run_id": run_id, "status": "drawn", "style_sha256": style_sha256(state["style"]),
           "spec_sha256": spec_sha256(component["spec"]), "document": str(document), "preview": str(preview),
           "program_id": program["id"], "program_sha256": program["sha256"], "simulation_notice": SCOPE,
           "backend": {"name": "photocraft", "version": "SYNTHETIC FIXTURE; NO BACKEND EXECUTED",
                       "sha256": "0" * 64, "cli_path": str(directory / "NO-CLI-WAS-EXECUTED")},
           "plan_path": _write_json(directory / "plan.json", plan),
           "style_snapshot_path": _write_json(directory / "inputs.json", {
               "style": state["style"], "spec": component["spec"], "component_id": component_id,
               "simulation_notice": SCOPE})}
    return store.record_run(str(root), context["revision"], "fixture-sheet", run)


def _export_items():
    return [{"asset_id": "panel", "name": "验证面板", "output_path": "smart-ui/exports/ui/panel.png",
             "region": {"x": 0, "y": 0, "width": 344, "height": 304}, "state": "normal", "purpose": "验证页面的面板背景",
             "geometry": {"mode": "nine_slice", "content_rect": {"x": 12, "y": 12, "width": 320, "height": 280},
                          "borders": {"left": 40, "top": 40, "right": 40, "bottom": 40}}},
            {"asset_id": "button", "name": "验证按钮", "output_path": "smart-ui/exports/ui/button.png",
             "region": {"x": 384, "y": 0, "width": 256, "height": 96}, "state": "normal", "purpose": "验证资产复用的按钮背景",
             "geometry": {"mode": "nine_slice", "content_rect": {"x": 12, "y": 12, "width": 232, "height": 72},
                          "borders": {"left": 32, "top": 32, "right": 32, "bottom": 32}}}]


def _typography(font):
    def style(name, usage, size_id, line_height, color, *, letter_spacing=0, stroke=None, shadow=None):
        return {"name": name, "usage": usage, "size_id": size_id, "font_path": str(font), "line_height": line_height,
                "letter_spacing": letter_spacing, "fill": {"color": color, "opacity": 1}, "stroke": stroke,
                "shadow": shadow, "evidence": []}

    return {"description": "合成页面使用固定字号和内容角色验证文字渲染及构建依赖。",
            "design_size": {"width": 760, "height": 700},
            "size_scale": {"display": 32, "heading": 28, "action": 20, "reading": 18, "caption": 16, "note": 14},
            "styles": {
                "page-title": style("页面标题", "标识页面主题", "display", 42, "#F5F8FF", letter_spacing=0.5,
                                    stroke={"width": 1.5, "color": "#182438", "opacity": 1},
                                    shadow={"offset_x": -2, "offset_y": 2, "blur": 1.5, "color": "#000000", "opacity": 0.65}),
                "subtitle": style("页面副标题", "简述本页验证内容", "caption", 24, "#ADC0DC"),
                "panel-title": style("面板标题", "说明面板内的验证目标", "heading", 40, "#23364F"),
                "body": style("正文", "说明操作和验证结果", "reading", 28, "#52647B"),
                "caption": style("图像说明", "区分同一图像的不同实例", "caption", 24, "#52647B"),
                "button-label": style("按钮文字", "标识短操作", "action", 28, "#FFFFFF"),
                "scope-note": style("范围说明", "说明样例验证范围", "note", 22, "#9EB0CA"),
            }}


def _nodes():
    def image(identifier, asset, x, y, width, height):
        return {"id": identifier, "kind": "image", "asset_id": asset, "x": x, "y": y,
                "width": width, "height": height}

    def text(identifier, style_id, content, x, y, width, height):
        return {"id": identifier, "kind": "text", "text": content, "text_style_id": style_id,
                "x": x, "y": y, "width": width, "height": height,
                "align": "center", "wrap": True, "overflow": "error"}

    return [image("panel-main", "panel", 56, 126, 648, 470),
            text("title", "page-title", "浏览器页面组合验证", 56, 40, 648, 56),
            text("subtitle", "subtitle", "九宫格资产 · 中文文字 · 当前预览", 56, 96, 648, 28),
            text("panel-title", "panel-title", "同一资产，两个实例", 88, 158, 584, 46),
            text("description", "body", "两个按钮引用同一张图\n重新导出后，重建页面即可更新两个实例", 110, 222, 540, 92),
            text("first-caption", "caption", "第一个实例", 104, 328, 248, 28),
            text("second-caption", "caption", "第二个实例", 408, 328, 248, 28),
            image("button-first", "button", 104, 364, 248, 88),
            image("button-second", "button", 408, 364, 248, 88),
            text("first-label", "button-label", "同一按钮", 120, 394, 216, 36),
            text("second-label", "button-label", "复用图像", 424, 394, 216, 36),
            text("page-note", "body", "HTML 与 PNG 来自同一个浏览器页面\n中文字体已嵌入，布局可离线打开", 88, 494, 584, 72),
            text("scope-note", "scope-note", "素材与绘制记录均为脚本合成，仅验证通用工具流程。\n本次不验证 PhotoCraft 绘制、源文件重开或美术质量。", 56, 628, 648, 52)]


def run(output_root, font_path):
    if not output_root.is_absolute():
        raise ValueError(f"--output-root 必须是绝对路径：{output_root}")
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"验证脚本只创建新项目，不覆盖已有路径：{output_root}")
    font = font_path.resolve(strict=True)
    if not font.is_file() or font.suffix.lower() not in (".ttf", ".otf"):
        raise ValueError(f"--font-path 必须指向真实 TTF 或 OTF 字体文件：{font}")
    output_root.mkdir(parents=True, exist_ok=False)
    report_path = output_root / ".smartui" / "report.json"
    report = {"status": "running", "scope": SCOPE, "project_root": str(output_root)}
    try:
        store = ProjectStore()
        root = str(output_root)
        context = store.create(root, "page-workflow-verification", "浏览器页面工具链验证")
        project_font = output_root / "smart-ui" / "sources" / "fonts" / ("verification" + font.suffix.lower())
        project_font.parent.mkdir(parents=True)
        shutil.copyfile(font, project_font)
        report["font"] = {"path": str(project_font), "sha256": _sha(project_font)}
        context = store.save_program(root, context["revision"], "synthetic-fixtures", "工具链验证用模拟程序", SCOPE, PROGRAM)
        context = store.define_component(root, context["revision"], "fixture-sheet", "工具链验证用模拟资产", {
            "program_id": "synthetic-fixtures", "width": 660, "height": 330, "simulation_notice": SCOPE})
        context = _record_simulated_run(store, context, "simulated-blue", False)
        exports = _export_items()
        exported = store.export_assets(root, context["revision"], "fixture-sheet", exports)
        typography = _typography(project_font)
        context = store.update_typography(root, exported["revision"], typography)
        saved = store.save_layout(root, context["revision"], "verification-screen", "浏览器页面验证",
                                  760, 700, "#182438", _nodes())
        first = store.build_layout(root, saved["revision"], "verification-screen")
        first_build = first["page_build"]
        first_nodes = {node["id"]: node for node in first_build["nodes"]}
        context = _record_simulated_run(store, store.context(root), "simulated-green", True)
        updated = store.export_assets(root, context["revision"], "fixture-sheet", exports)
        _require(updated["export"]["manifest"]["path"] == exported["export"]["manifest"]["path"],
                 "再次导出应更新同一份当前清单")
        stale = store.get_page_build(root, first_build["id"])
        _require(stale["matches_layout"] and not stale["matches_images"] and stale["matches_typography"],
                 "重新导出按钮后，当前预览应提示资产已变化")
        typography["styles"]["button-label"]["fill"]["color"] = "#E9FFEB"
        context = store.update_typography(root, updated["revision"], typography)
        stale = store.get_page_build(root, first_build["id"])
        _require(stale["matches_layout"] and not stale["matches_typography"] and stale["page_build"] == first_build,
                 "文字样式变化应将预览标为过期并保留旧快照")
        cache = output_root / ".smartui"
        ProjectLayout(output_root)._check_managed_path(cache)
        shutil.rmtree(cache)
        _require(store.context(root)["revision"] == context["revision"], "清空本机数据不应改变项目状态")
        second = store.build_layout(root, context["revision"], "verification-screen")
        final_build = second["page_build"]
        final_nodes = {node["id"]: node for node in final_build["nodes"]}
        current = store.context(root)["data"]
        digest = current["images"]["button"]["sha256"]
        _require(all(final_nodes[key]["sha256"] == digest != first_nodes[key]["sha256"]
                     for key in ("button-first", "button-second")), "重建后的两个实例必须使用当前按钮图像")
        first_label, second_label = (final_nodes[key] for key in ("first-label", "second-label"))
        _require(first_label["text_style_sha256"] == second_label["text_style_sha256"] !=
                 first_nodes["first-label"]["text_style_sha256"], "重建后的两个按钮必须使用当前文字样式")
        _require(final_nodes["title"]["text_style_sha256"] == first_nodes["title"]["text_style_sha256"],
                 "未改动的标题样式应保留原摘要")
        _require(final_build["layout_sha256"] == first_build["layout_sha256"], "重建不应改写布局定义")
        preview_parent = output_root / "smart-ui" / "previews" / "pages"
        preview_directory = preview_parent / first_build["id"]
        _require(final_build["id"] == first_build["id"] and list(preview_parent.iterdir()) == [preview_directory],
                 "重建应只保留同一目录中的当前预览")
        _require(final_build["files"]["overview"]["sha256"] != first_build["files"]["overview"]["sha256"],
                 "当前页面的 PNG 应反映新按钮图像")
        _require(second["matches_layout"] and second["matches_bindings"], "最终页面未匹配当前布局、资产和文字样式")
        report.update(status="passed", final_build=final_build, export=updated["export"],
                      checks={"current_manifest_replaced": True, "current_preview_replaced": True,
                              "rebuilt_after_removing_local_data": True, "both_instances_updated": True,
                              "layout_definition_unchanged": True, "shared_text_style_updated": True,
                              "unmodified_text_style_unchanged": True, "final_bindings_match": True},
                      overview=final_build["files"]["overview"]["path"], html=final_build["files"]["html"]["path"])
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(report_path, report)
        print(json.dumps({"status": report["status"], "report": str(report_path),
                          "overview": report.get("overview"), "html": report.get("html")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, epilog=SCOPE)
    parser.add_argument("--output-root", type=Path, required=True, help="尚不存在的项目绝对路径；拒绝覆盖已有路径")
    parser.add_argument("--font-path", type=Path, required=True, help="实际包含示例中文字形的 TTF/OTF 字体")
    arguments = parser.parse_args()
    run(arguments.output_root, arguments.font_path)
