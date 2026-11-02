import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from smartui_mcp.server import create_server
from test_client import responding_bridge


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1foAAAAASUVORK5CYII=")


class FakeClient:
    def __init__(self, preview_path):
        self.preview_path = preview_path
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "preview":
            return {"path": str(self.preview_path)}
        return {"method": method, "params": params}


class MCPToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_forward_protocol_and_return_preview_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            png = root / "preview.png"
            png.write_bytes(PNG)
            fake = FakeClient(png)
            server = create_server(fake)
            tools = await server.list_tools()
            self.assertEqual({t.name for t in tools}, {
                "krita_status", "krita_list_brushes", "krita_inspect_document", "krita_execute_batch", "krita_preview"
            })
            self.assertIsNone(next(t for t in tools if t.name == "krita_preview").outputSchema)
            document = str(root / "测试.kra")
            await server.call_tool("krita_status", {})
            await server.call_tool("krita_list_brushes", {"query": "Ink"})
            await server.call_tool("krita_inspect_document", {"document": document})
            operations = [{"op": "document.save", "document": document}]
            await server.call_tool("krita_execute_batch", {"operations": operations})
            preview = await server.call_tool("krita_preview", {"document": document})
            self.assertEqual(fake.calls, [
                ("status", {}), ("brushes", {"query": "Ink"}), ("inspect", {"document": document}),
                ("execute", {"operations": operations}), ("preview", {"document": document}),
            ])
            self.assertEqual(json.loads(preview[0].text), {"path": str(png)})
            self.assertEqual(preview[1].mimeType, "image/png")
            self.assertEqual(base64.b64decode(preview[1].data), PNG)

    async def test_document_validation_prevents_submission(self):
        fake = FakeClient(Path("unused.png"))
        server = create_server(fake)
        for document in ["relative.kra", str(Path.cwd() / "wrong.png")]:
            with self.subTest(document=document), self.assertRaisesRegex(Exception, "绝对 .kra 路径"):
                await server.call_tool("krita_inspect_document", {"document": document})
        self.assertEqual(fake.calls, [])

    async def test_stdio_handshake_tool_call_preview_and_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            png = root / "preview.png"
            png.write_bytes(PNG)
            document = str(root / "document.kra")

            def respond(request):
                if request["method"] == "execute":
                    return {"id": request["id"], "ok": False, "error": {
                        "type": "ValueError", "message": "未找到指定图层", "operation_index": 1,
                        "completed": [{"op": "document.open"}],
                    }}
                result = {"path": str(png)} if request["method"] == "preview" else {"protocol_version": 1}
                return {"id": request["id"], "ok": True, "result": result}

            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_BRIDGE_DIR": str(root), "SMARTUI_BRIDGE_TIMEOUT": "2"},
            )
            with responding_bridge(root, respond):
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        initialize = await session.initialize()
                        self.assertEqual(initialize.serverInfo.name, "SmartUI Krita")
                        tools = await session.list_tools()
                        self.assertEqual(len(tools.tools), 5)
                        result = await session.call_tool("krita_status", {})
                        self.assertFalse(result.isError)
                        self.assertEqual(json.loads(result.content[0].text), {"protocol_version": 1})
                        preview = await session.call_tool("krita_preview", {"document": document})
                        self.assertFalse(preview.isError)
                        self.assertIsNone(preview.structuredContent)
                        self.assertEqual(json.loads(preview.content[0].text), {"path": str(png)})
                        self.assertEqual(preview.content[1].mimeType, "image/png")
                        self.assertEqual(base64.b64decode(preview.content[1].data), PNG)
                        failure = await session.call_tool("krita_execute_batch", {"operations": [{"op": "paint"}]})
                        self.assertTrue(failure.isError)
                        self.assertIn('"operation_index": 1', failure.content[0].text)
                        self.assertIn('"completed"', failure.content[0].text)
                        self.assertIn("未找到指定图层", failure.content[0].text)
                        self.assertIn("request_id=", failure.content[0].text)
                        self.assertIn(str(root / "responses"), failure.content[0].text)


if __name__ == "__main__":
    unittest.main()
