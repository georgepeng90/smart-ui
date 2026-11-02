"""将 Krita 文件队列方法公开为 MCP 工具。"""

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .client import BridgeClient


def _document_path(document: str) -> str:
    path = Path(document)
    if not path.is_absolute() or path.suffix.lower() != ".kra":
        raise ValueError(f"document 必须是绝对 .kra 路径：{document}")
    return str(path)


def create_server(client: BridgeClient | None = None) -> FastMCP:
    bridge = client if client is not None else BridgeClient()
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

    return server
