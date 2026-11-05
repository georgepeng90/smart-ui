"""提供 PhotoCraft 制作与 SmartUI 项目管理 MCP 工具。"""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP, Image
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from .client import PhotoCraftClient
from .photocraft import read_document
from .projects import ProjectStore, ProjectError
from .references import register_reference, inspect_reference
from .pipeline import draw_component, plan_component


class VisualCheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: StrictStr
    run_id: StrictStr
    label: StrictStr
    region: list[StrictInt] | None = None
    scale: StrictFloat = 1.0


class CompositionNodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr
    x: StrictInt
    y: StrictInt


class CompositionComponent(CompositionNodeBase):
    kind: Literal["component"]
    component_id: StrictStr
    run_id: StrictStr
    width: StrictInt
    height: StrictInt
    region: list[StrictInt] | None = None


class CompositionImage(CompositionNodeBase):
    kind: Literal["image"]
    asset_id: StrictStr
    version: StrictInt
    width: StrictInt
    height: StrictInt
    region: list[StrictInt] | None = None


class CompositionText(CompositionNodeBase):
    kind: Literal["text"]
    text: StrictStr
    font_path: StrictStr
    font_size: StrictInt
    color: StrictStr
    stroke_width: StrictInt = 0
    stroke_color: StrictStr = "#000000"


CompositionNode = Annotated[CompositionComponent | CompositionImage | CompositionText, Field(discriminator="kind")]


def _project_summary(context):
    """工具返回制作摘要；完整节点检查留在项目文件中，避免反复挤占对话。"""
    data = context["data"]
    components = {}
    for key, value in data["components"].items():
        latest = value["latest_run"]
        components[key] = {"name": value["name"], "spec": value["spec"], "needs_review": value["needs_review"],
                           "open_feedback_count": value["open_feedback_count"],
                           "target_style_version": value["target_style_version"], "attempts": len(value["runs"]),
                           "last_attempt": {k: v for k, v in value["runs"][-1].items() if k in ("run_id", "status", "error")} if value["runs"] else None,
                           "latest_run": {k: v for k, v in latest.items() if k in ("run_id", "status", "style_version", "program_id", "program_version", "program_sha256", "document", "preview", "verification", "layer_ids", "backend")} if latest else None}
    comparisons = {key: {field: value[field] for field in ("id", "component_id", "run", "is_current", "created_at")}
                   for key, value in data.get("comparisons", {}).items()}
    visual_checks = {
        key: {field: value[field] for field in ("id", "is_current", "created_at")} | {
            "items": [{"component_id": item["component_id"], "run_id": item["run"]["run_id"],
                       "label": item["label"], "is_current": item["is_current"]} for item in value["items"]],
        }
        for key, value in data.get("visual_checks", {}).items()
    }
    feedback = {key: {field: value[field] for field in ("id", "comparison_id", "component_id", "observation",
                                                       "suggested_change", "target_nodes", "rule_ids", "source", "status",
                                                       "reviewed_comparison_id", "applies_to_current_run")}
                | {"latest_update": value["history"][-1] if value["history"] else None}
                for key, value in data.get("feedback", {}).items()}
    return {"project_root": context["project_root"], "revision": context["revision"],
            "project_id": data["project_id"], "name": data["name"], "paths": data["paths"], "style": data["style"],
            "references": data["references"], "programs": data["programs"], "components": components,
            "comparisons": comparisons, "visual_checks": visual_checks, "feedback": feedback,
            "images": data.get("images", {}),
            "exports": {key: {field: value[field] for field in ("id", "source", "manifest", "created_at")}
                        for key, value in data.get("exports", {}).items()},
            "layouts": {key: {field: value[field] for field in ("id", "name", "current_version")}
                        for key, value in data.get("layouts", {}).items()},
            "page_builds": {key: {field: value[field] for field in ("id", "layout_id", "layout_version", "created_at")}
                            | {"html": value["files"]["html"], "overview": value["files"]["overview"]}
                            for key, value in data.get("page_builds", {}).items()},
            "compositions": {key: {field: value[field] for field in ("id", "width", "height", "background", "is_current", "created_at")}
                             | {"overview": value["images"]["overview"]}
                             for key, value in data.get("compositions", {}).items()}}


def _document_path(document: str) -> str:
    path = Path(document)
    if not path.is_absolute() or path.suffix.lower() != ".pcraft":
        raise ValueError(f"document 必须是绝对 .pcraft 路径：{document}")
    return str(path)


def _verified_png(artifact, label):
    path = Path(artifact["path"])
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        raise ProjectError(f"返回的{label} SHA-256 不符：{path}")
    return Image(data=payload, format="png")


def _comparison_image(result):
    return _verified_png(result["comparison"]["images"]["comparison"], "对照图片")


def _visual_check_content(result):
    images = result["visual_check"]["images"]
    content = [json.dumps(result, ensure_ascii=False), _verified_png(images["overview"], "视觉检查总览图")]
    if "grayscale" in images:
        content.append(_verified_png(images["grayscale"], "视觉检查灰度图"))
    return content


def _image_content(result):
    record = result["image_version"]
    path = Path(record["path"])
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise ProjectError(f"返回的图像资产 SHA-256 不符：{path}")
    image_format = {".png": "png", ".jpg": "jpeg", ".webp": "webp"}[path.suffix]
    return [json.dumps(result, ensure_ascii=False), Image(data=payload, format=image_format)]


def _composition_content(result):
    return [json.dumps(result, ensure_ascii=False),
            _verified_png(result["composition"]["images"]["overview"], "组合预览")]


def create_server(client: PhotoCraftClient | None = None) -> FastMCP:
    renderer = client if client is not None else PhotoCraftClient()
    projects = ProjectStore()
    server = FastMCP(
        "SmartUI PhotoCraft",
        instructions=(
            "通过 PhotoCraft 无窗口引擎制作可编辑 .pcraft，项目程序先严格预检，再执行、保存重开并核验。"
            "局部修改使用计划节点 key。失败保留候选文件与请求记录，不自动重放写操作。"
            "页面复用正式资产和独立文字；技术检查不代表用户美术确认。"
        ),
    )

    @server.tool()
    async def photocraft_status() -> Any:
        """检查正常安装的 PhotoCraft CLI，返回实际路径、完整版本和可执行文件摘要。"""
        return await asyncio.to_thread(renderer.status)

    @server.tool()
    async def photocraft_inspect_document(document: str) -> Any:
        """读取绝对 .pcraft 路径，核验原生数据并返回图层 ID、完整属性和内容摘要。"""
        result = await asyncio.to_thread(read_document, Path(_document_path(document)))
        return {key: value for key, value in result.items() if key != "manifest"}

    @server.tool(structured_output=False)
    async def photocraft_preview(document: str) -> list[Any]:
        """在独立会话中打开 .pcraft 并返回原尺寸 PNG，不修改源文件。"""
        path = Path(_document_path(document))
        def render():
            with renderer.session(path.parent) as session:
                opened = session.call("doc.open", {"path": path.name})
                result = session.call("doc.render", {"maxSide": 0})
            if not isinstance(result, dict) or result.get("mime") != "image/png":
                raise ValueError("PhotoCraft 未返回文档 PNG")
            payload = base64.b64decode(result["base64"], validate=True)
            return {"document": str(path), "width": opened["width"], "height": opened["height"]}, payload
        info, payload = await asyncio.to_thread(render)
        return [json.dumps(info, ensure_ascii=False), Image(data=payload, format="png")]

    @server.tool()
    async def project_create(project_root: str, project_id: str, name: str, paths: dict[str, str] | None = None) -> Any:
        """在业务工程绝对根目录创建 .smartui/project.json 及自动维护的风格、组件文档。

        paths 可局部指定 documents、references、programs、sources，均为工程内相对目录；
        默认分别为 docs/ui、docs/ui/references、art/ui/programs、art/ui/sources。拒绝覆盖现有受管文档。
        """
        return _project_summary(await asyncio.to_thread(projects.create, project_root, project_id, name, paths=paths))

    @server.tool()
    async def project_context(project_root: str) -> Any:
        """读取项目、参考、风格及组件摘要，返回后续写入必须提供的 revision。受管文档被手改时明确报错。"""
        return _project_summary(await asyncio.to_thread(projects.context, project_root))

    @server.tool()
    async def project_add_reference(project_root: str, expected_revision: str, reference_id: str, source_path: str) -> Any:
        """登记 PNG/JPEG/WebP 参考，工程外原图归档到 references 目录，计算哈希并生成预览。

        原输入保持只读；归档使用 reference_id/原文件名，不覆盖已有文件。项目风格文档自动更新。
        """
        current = await asyncio.to_thread(projects.context, project_root)
        if current["revision"] != expected_revision:
            raise ProjectError("项目版本已变化，请重新读取 project_context")
        if reference_id in current["data"]["references"]:
            raise ProjectError("参考标识已存在：" + reference_id)
        reference = await asyncio.to_thread(register_reference, project_root, reference_id, source_path,
                                             paths=current["data"]["paths"])
        return _project_summary(await asyncio.to_thread(projects.add_reference, project_root, expected_revision, reference))

    @server.tool(structured_output=False)
    async def project_inspect_reference(project_root: str, reference_id: str, region: list[int] | None = None,
                                        patches: list[dict[str, Any]] | None = None, coordinate_size: list[int] | None = None) -> list[Any]:
        """返回参考区域图片和原图取色依据，不自动判断艺术风格。

        region=[x,y,width,height] 控制预览；patches=[{id,rect:[x,y,width,height]}] 指定取样色块。
        坐标都相对完整参考图。coordinate_size 省略时使用原图坐标，传 [1080,1920] 时按该基准映射。
        返回原始 RGB 中位数、通道范围和透明度；不进行 ICC 转换或背景合成。
        """
        current = await asyncio.to_thread(projects.context, project_root)
        if reference_id not in current["data"]["references"]:
            raise ProjectError("未登记参考：" + reference_id)
        result = await asyncio.to_thread(inspect_reference, project_root, current["data"]["references"][reference_id],
                                        region, [] if patches is None else patches, coordinate_size)
        return [json.dumps(result, ensure_ascii=False), Image(data=await asyncio.to_thread(Path(result["preview"]).read_bytes), format="png")]

    @server.tool()
    async def project_update_style(project_root: str, expected_revision: str, description: str,
                                   rules: list[dict[str, Any]], parameters: dict[str, Any], reference_ids: list[str]) -> Any:
        """保存模型从参考中归纳的风格说明、规则及执行参数，并自动生成风格文档。

        rules 的证据用 evidence:[{reference_id,...}]。参数或规则变化提升风格版本，旧组件标记需复核。
        parameters 是项目定义的共用参数，由项目绘制程序读取；允许保存待测草稿。
        不会自动将模型判断登记为用户确认。
        """
        return _project_summary(await asyncio.to_thread(projects.update_style, project_root, expected_revision,
                                                        description, rules, parameters, reference_ids))

    @server.tool()
    async def project_define_component(project_root: str, expected_revision: str, component_id: str,
                                       name: str, spec: dict[str, Any]) -> Any:
        """创建或更新组件规格并生成组件文档，不伪造制作结果。

        spec 必须包含已登记的 program_id，其他字段由项目程序自行定义，不限制组件类型和画法。
        """
        return _project_summary(await asyncio.to_thread(projects.define_component, project_root, expected_revision,
                                                        component_id, name, spec))

    @server.tool()
    async def project_draw_component(project_root: str, expected_revision: str, component_id: str,
                                     layers: list[str] | None = None) -> Any:
        """将组件交给 PhotoCraft 制作候选源文件、保存重开和核验，成功后发布并更新文档。

        执行 spec.program_id 引用的项目 Python 程序。省略 layers 完整绘制，或提供计划节点 key 列表作局部修改。
        局部修改要求完整计划中的未选节点没有变化，实际执行后再次核对无关节点。
        源文件位于配置的 sources 目录（默认 art/ui/sources），参数快照和完整调用记录位于 .smartui/runs。
        PhotoCraft CLI 必须已安装；不要求桌面窗口正在运行。
        工具通过代表技术检查通过，视觉效果及用户确认须另行查看。失败尝试会记录实际错误，不自动重试。
        """
        return await asyncio.to_thread(draw_component, project_root, expected_revision, component_id, layers,
                                        client=renderer, store=projects)

    @server.tool()
    async def project_save_program(project_root: str, expected_revision: str, program_id: str,
                                   name: str, description: str, source: str) -> Any:
        """保存或更新项目 Python 绘制程序，维护不可变源码版本及依赖组件状态；只检查语法，不执行。

        程序实现 build(parameters, spec, component_id) 并返回包含 schema_version/width/height/nodes 的计划。
        节点、图层、蒙版及画法由项目定义。完整格式见 docs/drawing-programs.md。
        plan/draw 会在本机独立 Python 进程执行该可信源码，不是安全沙箱。
        """
        return _project_summary(await asyncio.to_thread(projects.save_program, project_root, expected_revision,
                                                        program_id, name, description, source))

    @server.tool()
    async def project_get_program(project_root: str, program_id: str) -> Any:
        """读取项目绘制程序的完整源码、版本、摘要及版本历史，修改后通过 project_save_program 保存。"""
        return await asyncio.to_thread(projects.get_program, project_root, program_id)

    @server.tool()
    async def project_plan_component(project_root: str, component_id: str) -> Any:
        """执行当前项目 Python 程序并返回经过校验的绘制计划，不启动绘图进程。

        使用与绘制相同的独立 Python 进程和 60 秒限制；用于检查节点结构、参数计算及批量笔触。
        """
        return await asyncio.to_thread(plan_component, project_root, component_id, store=projects)

    @server.tool(structured_output=False)
    async def project_compare_component(project_root: str, expected_revision: str, component_id: str,
                                        run_id: str, reference_id: str, reference_region: list[StrictInt] | None = None,
                                        reference_coordinate_size: list[StrictInt] | None = None,
                                        artifact_region: list[StrictInt] | None = None, reference_scale: StrictFloat = 1.0,
                                        artifact_scale: StrictFloat = 1.0) -> list[Any]:
        """保存参考与指定成功运行产物的对照，返回 revision、版本证据和并排图片，无需启动绘图进程。

        region 均为 [x,y,width,height]；参考按 reference_coordinate_size 映射（省略为原图），
        artifact_region 使用完整产物 PNG 的原始像素坐标。两端 scale 默认 1，分别等比缩放，
        不自动对齐宽高；裁切快照保留原始 RGBA，拼图使用棋盘背景。超限报错，不自动缩小。
        校验源图和运行快照摘要，保存到项目 .smartui/comparisons；不打相似度分，不登记用户确认。
        """
        result = await asyncio.to_thread(projects.compare_component, project_root, expected_revision, component_id,
                                        run_id, reference_id, reference_region, reference_coordinate_size,
                                        artifact_region, reference_scale, artifact_scale)
        return [json.dumps(result, ensure_ascii=False), await asyncio.to_thread(_comparison_image, result)]

    @server.tool(structured_output=False)
    async def project_get_comparison(project_root: str, comparison_id: str) -> list[Any]:
        """读取既有对照快照、实际运行版本及关联反馈历史，校验图片摘要并返回并排图片。

        is_current 只表示该运行与当前项目记录匹配，不表示视觉通过或磁盘源文件仍未改变。
        历史对照保留原始内容，不使用当前新产物替换旧图。
        """
        result = await asyncio.to_thread(projects.get_comparison, project_root, comparison_id)
        return [json.dumps(result, ensure_ascii=False), await asyncio.to_thread(_comparison_image, result)]

    @server.tool(structured_output=False)
    async def project_create_visual_check(project_root: str, expected_revision: str, items: list[VisualCheckItem],
                                          background: StrictStr, columns: StrictInt = 3,
                                          grayscale: StrictBool = True) -> list[Any]:
        """保存 1 到 16 项指定成功运行的视觉检查视图，返回彩色总览和可选灰度总览，无需启动绘图进程。

        每项用 component_id 和 run_id 指定来源，label 对应图中序号。region 为完整预览图上的
        [x,y,width,height]，省略时使用全图。scale 按原尺寸等比缩放，默认为 1。
        background 使用 #RRGGBB 纯色，columns 指定最多 16 列。先缩放和合成背景，再转灰度。
        返回原尺寸 RGBA 裁切文件、显示位置和运行版本。不自动打分或登记用户确认。
        """
        result = await asyncio.to_thread(projects.create_visual_check, project_root, expected_revision,
                                         [item.model_dump() for item in items], background=background,
                                         columns=columns, grayscale=grayscale)
        return await asyncio.to_thread(_visual_check_content, result)

    @server.tool(structured_output=False)
    async def project_get_visual_check(project_root: str, check_id: str) -> list[Any]:
        """读取已保存的视觉检查图和各项来源，校验文件摘要并返回总览图片。

        is_current 表示引用的运行与当前项目记录匹配。历史检查仍使用保存时的快照。
        """
        result = await asyncio.to_thread(projects.get_visual_check, project_root, check_id)
        return await asyncio.to_thread(_visual_check_content, result)

    @server.tool()
    async def project_register_image(project_root: str, expected_revision: str, asset_id: StrictStr,
                                     name: StrictStr, source_path: StrictStr, provenance: dict) -> Any:
        """按原字节登记 PNG/JPEG/WebP 的新版本，不调用生图，不自动选用。

        source_path 必须是绝对路径。provenance.kind 为 generated/edited 时必须有真实 tool、prompt；
        imported 时必须有 description。其他实际生成信息可附加，不填造未知模型或种子。
        """
        return await asyncio.to_thread(projects.register_image, project_root, expected_revision,
                                       asset_id, name, source_path, provenance)

    @server.tool()
    async def project_export_assets(project_root: str, expected_revision: str, component_id: StrictStr,
                                    run_id: StrictStr, exports: list[dict[str, Any]]) -> Any:
        """从成功组件运行的明确区域导出正式 PNG、几何元数据和清单；不改变选用版本。"""
        return await asyncio.to_thread(projects.export_assets, project_root, expected_revision, component_id, run_id, exports)

    @server.tool()
    async def project_get_export(project_root: str, export_id: StrictStr) -> Any:
        """校验并读取历史导出清单和原始资产版本，不要求生产路径仍为旧版本。"""
        return await asyncio.to_thread(projects.get_export, project_root, export_id)

    @server.tool()
    async def project_select_image(project_root: str, expected_revision: str,
                                   asset_id: StrictStr, version: StrictInt) -> Any:
        """明确选用一个已登记的图像版本；不重建已有组合，不表示用户美术确认。"""
        return await asyncio.to_thread(projects.select_image, project_root, expected_revision, asset_id, version)

    @server.tool(structured_output=False)
    async def project_get_image(project_root: str, asset_id: StrictStr, version: StrictInt) -> list[Any]:
        """校验并查看指定图像版本，返回实际图片、来源、摘要及当前选用版本。"""
        result = await asyncio.to_thread(projects.get_image, project_root, asset_id, version)
        return await asyncio.to_thread(_image_content, result)

    @server.tool(structured_output=False)
    async def project_create_composition(project_root: str, expected_revision: str, width: StrictInt,
                                          height: StrictInt, background: StrictStr,
                                          nodes: list[CompositionNode]) -> list[Any]:
        """按明确坐标组合组件成功运行、独立图像版本及单行文字，保存来源快照和 PNG。

        nodes 按叠放顺序排列。component 用 component_id/run_id；image 用 asset_id/version。
        图像区域 region=[x,y,width,height] 可省略；目标 width/height 内等比居中，不拉伸。
        text 必须指定真实 TTF/OTF font_path、font_size、color；x/y 为文字左上基准，描边不能越界。
        这是一份静态组合预览，不负责布局求解、九宫格、自动换行、运行时交互或生图。
        """
        result = await asyncio.to_thread(projects.create_composition, project_root, expected_revision,
                                         width, height, background, [node.model_dump() for node in nodes])
        return await asyncio.to_thread(_composition_content, result)

    @server.tool(structured_output=False)
    async def project_get_composition(project_root: str, composition_id: StrictStr) -> list[Any]:
        """校验并读取已有组合快照，源资产更新不改变原图；is_current 仅反映引用版本。"""
        result = await asyncio.to_thread(projects.get_composition, project_root, composition_id)
        return await asyncio.to_thread(_composition_content, result)

    @server.tool()
    async def project_save_layout(project_root: str, expected_revision: str, layout_id: StrictStr, name: StrictStr,
                                  width: StrictInt, height: StrictInt, background: StrictStr,
                                  nodes: list[dict[str, Any]]) -> Any:
        """保存页面修订和固定/选用资产绑定；保留独立文本框，不绘制或重新生成资产。"""
        return await asyncio.to_thread(projects.save_layout, project_root, expected_revision, layout_id, name,
                                       width, height, background, nodes)

    @server.tool()
    async def project_get_layout(project_root: str, layout_id: StrictStr, version: StrictInt | None = None) -> Any:
        """读取可修改的当前布局或明确的历史修订；省略 version 读取当前修订。"""
        return await asyncio.to_thread(projects.get_layout, project_root, layout_id, version)

    @server.tool(structured_output=False)
    async def project_build_layout(project_root: str, expected_revision: str, layout_id: StrictStr,
                                   version: StrictInt | None = None) -> list[Any]:
        """构建独立离线 HTML 和同页 Chromium PNG；明确解析选用版本，保留历史快照及字体证据。"""
        result = await asyncio.to_thread(projects.build_layout, project_root, expected_revision, layout_id, version)
        return [json.dumps(result, ensure_ascii=False),
                await asyncio.to_thread(_verified_png, result["page_build"]["files"]["overview"], "页面构建")]

    @server.tool(structured_output=False)
    async def project_get_page_build(project_root: str, build_id: StrictStr) -> list[Any]:
        """读取并校验独立页面快照；分别返回布局修订和选用资产是否匹配当前状态。"""
        result = await asyncio.to_thread(projects.get_page_build, project_root, build_id)
        return [json.dumps(result, ensure_ascii=False),
                await asyncio.to_thread(_verified_png, result["page_build"]["files"]["overview"], "页面构建")]

    @server.tool()
    async def project_record_feedback(project_root: str, expected_revision: str, comparison_id: str,
                                       observation: str, suggested_change: str, target_nodes: list[str] | None = None,
                                       rule_ids: list[str] | None = None, source: str = "model") -> Any:
        """记录对照中的具体偏差与修改建议，返回稳定反馈 ID 和新 revision。

        target_nodes 是该次绘制计划的节点 key，rule_ids 是该次风格规则 ID，可省略。
        source 为 model/user，仅记录调用方声明的反馈来源，不证明身份，也不表示用户风格确认。
        观察与建议由调用模型填写，工具不自动判断视觉偏差或改变技术检查状态。
        """
        return await asyncio.to_thread(projects.record_feedback, project_root, expected_revision, comparison_id,
                                        observation, suggested_change, target_nodes, rule_ids, source)

    @server.tool()
    async def project_update_feedback(project_root: str, expected_revision: str, feedback_id: str, status: str,
                                      comment: str, comparison_id: str, source: str = "model") -> Any:
        """追加偏差复查结果，保留原始观察和全部历史；status 为 open/resolved/dismissed。

        comment 必填。对照必须来自同组件、同参考摘要及同参考区域；resolved 必须引用
        原反馈运行之后的另一轮成功产物。看图后才能填写结论，技术 verified 不等于视觉偏差解决。
        source 为 model/user 的来源声明；本工具不会设置风格或组件的用户确认。
        """
        return await asyncio.to_thread(projects.update_feedback, project_root, expected_revision, feedback_id,
                                        status, comment, comparison_id, source)

    return server
