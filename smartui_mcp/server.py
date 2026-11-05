"""提供 PhotoCraft 制作与 SmartUI 项目管理 MCP 工具。"""

import asyncio
import base64
import hashlib
import json
import shutil
import tempfile
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
    width: StrictInt
    height: StrictInt
    region: list[StrictInt] | None = None


class CompositionImage(CompositionNodeBase):
    kind: Literal["image"]
    asset_id: StrictStr
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
    """返回当前制作定义和检查状态，省略冗长原生节点检查。"""
    from copy import deepcopy
    data = deepcopy(context["data"])
    for component in data["components"].values():
        if component["latest_run"]:
            component["latest_run"].pop("inspection", None)
    return {"project_root": context["project_root"], "revision": context["revision"], **data}


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
    record = result["image"]
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
            project = next((parent.parent for parent in path.parents if parent.name == "smart-ui"), path.parent)
            temporary_root = project / ".smartui/temp"
            temporary_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
                shutil.copyfile(path, Path(temporary) / path.name)
                with renderer.session(Path(temporary)) as session:
                    opened = session.call("doc.open", {"path": path.name})
                    result = session.call("doc.render", {"maxSide": 0})
            if not isinstance(result, dict) or result.get("mime") != "image/png":
                raise ValueError("PhotoCraft 未返回文档 PNG")
            payload = base64.b64decode(result["base64"], validate=True)
            return {"document": str(path), "width": opened["width"], "height": opened["height"]}, payload
        info, payload = await asyncio.to_thread(render)
        return [json.dumps(info, ensure_ascii=False), Image(data=payload, format="png")]

    @server.tool()
    async def project_create(project_root: str, project_id: str, name: str) -> Any:
        """在工程 smart-ui/ 创建当前项目状态及受管文档，.smartui/ 仅放可删除的本机数据。"""
        return _project_summary(await asyncio.to_thread(projects.create, project_root, project_id, name))

    @server.tool()
    async def project_context(project_root: str) -> Any:
        """读取项目、参考、风格及组件摘要，返回后续写入必须提供的 revision。受管文档被手改时明确报错。"""
        return _project_summary(await asyncio.to_thread(projects.context, project_root))

    @server.tool()
    async def project_add_reference(project_root: str, expected_revision: str, reference_id: str, source_path: str) -> Any:
        """登记 PNG/JPEG/WebP 参考，原图归档到 references 目录并计算摘要。

        原输入保持只读；归档使用 reference_id/原文件名，不覆盖已有文件。查看参考时生成预览。
        """
        current = await asyncio.to_thread(projects.context, project_root)
        if current["revision"] != expected_revision:
            raise ProjectError("项目版本已变化，请重新读取 project_context")
        projects._check_id_collision(current["data"]["references"], reference_id, "reference_id")
        if reference_id in current["data"]["references"]:
            raise ProjectError("参考标识已存在：" + reference_id)
        reference = await asyncio.to_thread(register_reference, project_root, reference_id, source_path)
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
        """更新当前风格规则和共用参数；内容摘要变化使相关产物需要复查，不代表用户确认。"""
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
        """执行当前项目程序，保存重开并校验源文件后覆盖当前产物。layers 指定局部节点，未选节点必须保持；失败不覆盖源文件，最近诊断留在 .smartui/。"""
        return await asyncio.to_thread(draw_component, project_root, expected_revision, component_id, layers,
                                        client=renderer, store=projects)

    @server.tool()
    async def project_save_program(project_root: str, expected_revision: str, program_id: str,
                                   name: str, description: str, source: str) -> Any:
        """覆盖当前 Python 绘制程序，校验语法并更新摘要。程序实现 build(parameters, spec, component_id)，历史由 Git 管理。"""
        return _project_summary(await asyncio.to_thread(projects.save_program, project_root, expected_revision,
                                                        program_id, name, description, source))

    @server.tool()
    async def project_get_program(project_root: str, program_id: str) -> Any:
        """读取当前程序源码、说明和内容摘要。"""
        return await asyncio.to_thread(projects.get_program, project_root, program_id)

    @server.tool()
    async def project_plan_component(project_root: str, component_id: str) -> Any:
        """执行当前项目 Python 程序并返回经过校验的绘制计划，不启动绘图进程。

        使用与绘制相同的独立 Python 进程和 60 秒限制；用于检查节点结构、参数计算及批量笔触。
        """
        return await asyncio.to_thread(plan_component, project_root, component_id, store=projects)

    @server.tool(structured_output=False)
    async def project_compare_component(project_root: str, expected_revision: str, component_id: str,
                                        reference_id: str, reference_region: list[StrictInt] | None = None,
                                        reference_coordinate_size: list[StrictInt] | None = None,
                                        artifact_region: list[StrictInt] | None = None, reference_scale: StrictFloat = 1.0,
                                        artifact_scale: StrictFloat = 1.0) -> list[Any]:
        """对照当前组件产物与参考，按组件和参考的稳定标识覆盖当前检查图；region=[x,y,width,height]，倍率和参考坐标基准显式指定。"""
        result = await asyncio.to_thread(projects.compare_component, project_root, expected_revision, component_id,
                                        reference_id, reference_region, reference_coordinate_size,
                                        artifact_region, reference_scale, artifact_scale)
        return [json.dumps(result, ensure_ascii=False), await asyncio.to_thread(_comparison_image, result)]

    @server.tool(structured_output=False)
    async def project_get_comparison(project_root: str, comparison_id: str) -> list[Any]:
        """校验并读取当前对照图及反馈；is_current 表示检查依据是否仍匹配当前产物。"""
        result = await asyncio.to_thread(projects.get_comparison, project_root, comparison_id)
        return [json.dumps(result, ensure_ascii=False), await asyncio.to_thread(_comparison_image, result)]

    @server.tool(structured_output=False)
    async def project_create_visual_check(project_root: str, expected_revision: str, check_id: StrictStr, items: list[VisualCheckItem],
                                          background: StrictStr, columns: StrictInt = 3,
                                          grayscale: StrictBool = True) -> list[Any]:
        """以稳定 check_id 覆盖当前视觉检查。items 引用 component_id 和 label，可指定 region/scale；不自动判断美术质量。"""
        result = await asyncio.to_thread(projects.create_visual_check, project_root, expected_revision, check_id,
                                         [item.model_dump() for item in items], background=background,
                                         columns=columns, grayscale=grayscale)
        return await asyncio.to_thread(_visual_check_content, result)

    @server.tool(structured_output=False)
    async def project_get_visual_check(project_root: str, check_id: str) -> list[Any]:
        """校验并读取当前视觉检查图和来源摘要。"""
        result = await asyncio.to_thread(projects.get_visual_check, project_root, check_id)
        return await asyncio.to_thread(_visual_check_content, result)

    @server.tool()
    async def project_register_image(project_root: str, expected_revision: str, asset_id: StrictStr,
                                     name: StrictStr, source_path: StrictStr, provenance: dict) -> Any:
        """登记或覆盖同一 asset_id 的当前图像；保留真实来源。generated/edited 必须填写实际 tool/prompt，imported 必须填写 description。"""
        return await asyncio.to_thread(projects.register_image, project_root, expected_revision,
                                       asset_id, name, source_path, provenance)

    @server.tool()
    async def project_export_assets(project_root: str, expected_revision: str, component_id: StrictStr,
                                    exports: list[dict[str, Any]]) -> Any:
        """从当前组件成功产物导出正式 PNG 和几何清单，输出必须位于 smart-ui/exports/。"""
        return await asyncio.to_thread(projects.export_assets, project_root, expected_revision, component_id, exports)

    @server.tool()
    async def project_get_export(project_root: str, export_id: StrictStr) -> Any:
        """校验并读取当前导出清单和资产。"""
        return await asyncio.to_thread(projects.get_export, project_root, export_id)

    @server.tool(structured_output=False)
    async def project_get_image(project_root: str, asset_id: StrictStr) -> list[Any]:
        """校验并读取当前图像及真实来源。"""
        result = await asyncio.to_thread(projects.get_image, project_root, asset_id)
        return await asyncio.to_thread(_image_content, result)

    @server.tool(structured_output=False)
    async def project_create_composition(project_root: str, expected_revision: str, composition_id: StrictStr, width: StrictInt,
                                          height: StrictInt, background: StrictStr,
                                          nodes: list[CompositionNode]) -> list[Any]:
        """按稳定 composition_id 更新当前静态组合；组件引用 component_id，图像引用 asset_id，文字指定真实字体。"""
        result = await asyncio.to_thread(projects.create_composition, project_root, expected_revision, composition_id,
                                         width, height, background, [node.model_dump() for node in nodes])
        return await asyncio.to_thread(_composition_content, result)

    @server.tool(structured_output=False)
    async def project_get_composition(project_root: str, composition_id: StrictStr) -> list[Any]:
        """读取并校验当前组合预览及来源是否仍匹配。"""
        result = await asyncio.to_thread(projects.get_composition, project_root, composition_id)
        return await asyncio.to_thread(_composition_content, result)

    @server.tool()
    async def project_save_layout(project_root: str, expected_revision: str, layout_id: StrictStr, name: StrictStr,
                                  width: StrictInt, height: StrictInt, background: StrictStr,
                                  nodes: list[dict[str, Any]]) -> Any:
        """覆盖当前布局定义；图像通过 asset_id 绑定当前资产，文字独立排版。"""
        return await asyncio.to_thread(projects.save_layout, project_root, expected_revision, layout_id, name,
                                       width, height, background, nodes)

    @server.tool()
    async def project_get_layout(project_root: str, layout_id: StrictStr) -> Any:
        """读取当前可编辑布局定义。"""
        return await asyncio.to_thread(projects.get_layout, project_root, layout_id)

    @server.tool(structured_output=False)
    async def project_build_layout(project_root: str, expected_revision: str, layout_id: StrictStr) -> list[Any]:
        """覆盖当前布局的离线 HTML 和 PNG，保留当前输入摘要。"""
        result = await asyncio.to_thread(projects.build_layout, project_root, expected_revision, layout_id)
        return [json.dumps(result, ensure_ascii=False),
                await asyncio.to_thread(_verified_png, result["page_build"]["files"]["overview"], "页面构建")]

    @server.tool(structured_output=False)
    async def project_get_page_build(project_root: str, build_id: StrictStr) -> list[Any]:
        """校验并读取当前页面预览，检查布局和资产摘要是否匹配。"""
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
        """更新当前反馈状态和复查说明，不保留历史流水；resolved 必须基于已变化的产物，技术检查不等于用户美术确认。"""
        return await asyncio.to_thread(projects.update_feedback, project_root, expected_revision, feedback_id,
                                        status, comment, comparison_id, source)

    return server
