"""将 Krita 文件队列方法公开为 MCP 工具。"""

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .client import BridgeClient
from .projects import ProjectStore, ProjectError
from .references import register_reference, inspect_reference
from .pipeline import draw_component, plan_component


def _project_summary(context):
    """工具返回制作摘要；完整节点检查留在项目文件中，避免反复挤占对话。"""
    data = context["data"]
    components = {}
    for key, value in data["components"].items():
        latest = value["latest_run"]
        components[key] = {"name": value["name"], "spec": value["spec"], "needs_review": value["needs_review"],
                           "target_style_version": value["target_style_version"], "attempts": len(value["runs"]),
                           "last_attempt": {k: v for k, v in value["runs"][-1].items() if k in ("run_id", "status", "error")} if value["runs"] else None,
                           "latest_run": {k: v for k, v in latest.items() if k in ("run_id", "status", "style_version", "program_id", "program_version", "program_sha256", "document", "preview", "verification", "layer_ids")} if latest else None}
    return {"project_root": context["project_root"], "revision": context["revision"],
            "project_id": data["project_id"], "name": data["name"], "style": data["style"],
            "references": data["references"], "programs": data["programs"], "components": components}


def _document_path(document: str) -> str:
    path = Path(document)
    if not path.is_absolute() or path.suffix.lower() != ".kra":
        raise ValueError(f"document 必须是绝对 .kra 路径：{document}")
    return str(path)


def create_server(client: BridgeClient | None = None) -> FastMCP:
    bridge = client if client is not None else BridgeClient()
    projects = ProjectStore()
    server = FastMCP(
        "SmartUI Krita",
        instructions=(
            "使用 Krita 原生笔刷编辑 .kra 文档。文档必须使用绝对路径；节点使用 UUID 或 name:唯一名称。"
            "批量执行遇错停止并保留已经完成的操作，不会自动回滚。"
            "超时不代表执行失败，请通过错误中的请求 ID 和结果路径确认结果，避免重复绘制。"
        ),
    )

    @server.tool()
    async def krita_status() -> Any:
        """实际请求 Krita 状态，返回版本、协议版本和支持的操作。"""
        return await asyncio.to_thread(bridge.call, "status", {})

    @server.tool()
    async def krita_list_brushes(query: str = "") -> Any:
        """按可选名称片段查询已安装的原生笔刷预设，返回排序后的名称。"""
        return await asyncio.to_thread(bridge.call, "brushes", {"query": query})

    @server.tool()
    async def krita_inspect_document(document: str) -> Any:
        """检查 .kra 文档和递归图层树，包含节点 UUID、属性与像素 SHA256。"""
        return await asyncio.to_thread(bridge.call, "inspect", {"document": _document_path(document)})

    @server.tool()
    async def krita_execute_batch(operations: list[dict[str, Any]]) -> Any:
        """顺序执行绘制操作；错误会返回 operation_index 和 completed，不会自动回滚。

        每项包含 op；路径必须为绝对 .kra 路径，节点用 UUID 或 name:唯一名称。
        操作及参数如下：
        document.create: path, width, height, name（仅允许新路径）；
        document.open: path；document.save/close: document（close 仅关闭已保存文档）；
        layer.create: document, name, kind（paintlayer/grouplayer）, parent（默认 root）；
        layer.update: document, node, properties，允许 name/visible/locked/opacity(0–255)/
        blending_mode/inherit_alpha/alpha_locked；layer.clear: document, node；
        mask.create: document, parent, name, rects；mask.update: document, node, rects；
        rects 每项为 [x,y,w,h,alpha]，未列区域透明；
        paint: document, node, brush（精确预设名）, color（#RRGGBB）, size,
        opacity（0–1，默认 1）, strokes。
        每条 stroke 包含 kind：line 使用 from/to=[x,y]，可选 pressure_from/pressure_to；
        rectangle/ellipse 使用 rect=[x,y,w,h]；path 使用 commands，支持
        [M,x,y]、[L,x,y]、[C,c1x,c1y,c2x,c2y,x,y]、[Q,cx,cy,x,y]、[Z]。
        几何和路径可选 fill=none/foreground（默认 none）。描边使用 Krita 原生笔刷。
        """
        return await asyncio.to_thread(bridge.call, "execute", {"operations": operations})

    @server.tool(structured_output=False)
    async def krita_preview(document: str) -> list[Any]:
        """导出 .kra 文档预览，同时返回 PNG 绝对路径和 MCP 图片。"""
        result = await asyncio.to_thread(bridge.call, "preview", {"document": _document_path(document)})
        if not isinstance(result, dict) or not isinstance(result.get("path"), str):
            raise ValueError("Krita preview 结果缺少 PNG 路径 path")
        path = Path(result["path"])
        if not path.is_absolute() or path.suffix.lower() != ".png":
            raise ValueError(f"Krita preview 必须返回 PNG 绝对路径：{path}")
        image_data = await asyncio.to_thread(path.read_bytes)
        return [json.dumps(result, ensure_ascii=False), Image(data=image_data, format="png")]

    @server.tool()
    async def project_create(project_root: str, project_id: str, name: str) -> Any:
        """创建本地项目及自动维护的 style.md、components.md；拒绝覆盖现有同名文档。"""
        return _project_summary(await asyncio.to_thread(projects.create, project_root, project_id, name))

    @server.tool()
    async def project_context(project_root: str) -> Any:
        """读取项目、参考、风格及组件摘要，返回后续写入必须提供的 revision。受管文档被手改时明确报错。"""
        return _project_summary(await asyncio.to_thread(projects.context, project_root))

    @server.tool()
    async def project_add_reference(project_root: str, expected_revision: str, reference_id: str, source_path: str) -> Any:
        """登记只读 PNG/JPEG/WebP 参考，计算源文件哈希并生成预览；项目风格文档自动更新。"""
        current = await asyncio.to_thread(projects.context, project_root)
        if current["revision"] != expected_revision:
            raise ProjectError("项目版本已变化，请重新读取 project_context")
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
        """将已定义组件交给 Krita 原生笔刷绘制、保存重开、核验，并自动更新组件文档。

        执行 spec.program_id 引用的项目 Python 程序。省略 layers 完整绘制，或提供计划节点 key 列表作局部修改。
        局部修改要求完整计划中的未选节点没有变化，实际执行后再次核对无关节点。
        源文件位于项目 assets，参数快照和完整调用记录位于 .smartui/runs。Krita 必须已启动。
        工具通过代表技术检查通过，视觉效果及用户确认须另行查看。失败尝试会记录实际错误，不自动重试。
        """
        return await asyncio.to_thread(draw_component, project_root, expected_revision, component_id, layers,
                                        client=bridge, store=projects)

    @server.tool()
    async def project_save_program(project_root: str, expected_revision: str, program_id: str,
                                   name: str, description: str, source: str) -> Any:
        """保存或更新项目 Python 绘制程序，维护不可变源码版本及依赖组件状态；只检查语法，不执行。

        程序实现 build(parameters, spec, component_id) 并返回包含 width/height/nodes/paint 的计划。
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
        """执行当前项目 Python 程序并返回经过校验的绘制计划，不调用 Krita。

        使用与绘制相同的独立 Python 进程和 60 秒限制；用于检查节点结构、参数计算及批量笔触。
        """
        return await asyncio.to_thread(plan_component, project_root, component_id, store=projects)

    return server
