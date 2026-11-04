import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from smartui_mcp.projects import ProjectStore
from smartui_mcp.server import create_server
from test_client import responding_bridge


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1foAAAAASUVORK5CYII=")
TOOL_NAMES = {
    "krita_status", "krita_list_brushes", "krita_inspect_document", "krita_execute_batch", "krita_preview",
    "project_create", "project_context", "project_add_reference", "project_inspect_reference",
    "project_update_style", "project_define_component", "project_draw_component",
    "project_save_program", "project_get_program", "project_plan_component",
    "project_compare_component", "project_get_comparison", "project_create_visual_check", "project_get_visual_check",
    "project_record_feedback", "project_update_feedback",
    "project_register_image", "project_select_image", "project_get_image",
    "project_create_composition", "project_get_composition",
}

CUSTOM_PROGRAM = '''def build(parameters, spec, component_id):
    extent = spec["extent"]
    return {
        "width": extent, "height": extent,
        "nodes": [
            {"key": "group", "name": component_id + " 自定义分组", "kind": "grouplayer", "parent": "root"},
            {"key": "ink", "name": "程序生成的线条", "kind": "paintlayer", "parent": "group"},
            {"key": "clip", "name": "局部透明度", "kind": "transparencymask", "parent": "ink",
             "rects": [[0, 0, extent, extent, 180]]},
        ],
        "paint": [{"node": "ink", "brush": "项目自选原生笔刷", "color": parameters["color"], "size": 2,
                   "strokes": [{"kind": "line", "from": [index, 0], "to": [index, extent - 1]}
                               for index in range(parameters["line_count"])]}],
    }
'''


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
    async def test_visual_check_tools_require_strict_options(self):
        server = create_server(FakeClient(Path("unused.png")))
        tools = {tool.name: tool for tool in await server.list_tools()}
        create = tools["project_create_visual_check"]
        self.assertIsNone(create.outputSchema)
        self.assertIsNone(tools["project_get_visual_check"].outputSchema)
        self.assertEqual(set(create.inputSchema["required"]), {"project_root", "expected_revision", "items", "background"})
        item_schema = create.inputSchema["$defs"]["VisualCheckItem"]
        self.assertEqual(set(item_schema["required"]), {"component_id", "run_id", "label"})
        self.assertFalse(item_schema["additionalProperties"])
        item = {"component_id": "button", "run_id": "run-1", "label": "主按钮"}
        arguments = {"project_root": "unused", "expected_revision": "rev", "items": [item], "background": "#FFFFFF"}
        with patch.object(ProjectStore, "create_visual_check", create=True) as save:
            for invalid in (
                {"items": [item | {"scale": True}]}, {"items": [item | {"scale": "1"}]},
                {"items": [item | {"region": [0, 0, True, 1]}]}, {"items": [item | {"label": 1}]},
                {"items": [item | {"unexpected": 1}]}, {"columns": True}, {"grayscale": "true"},
            ):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(Exception, "validation error"):
                    await server.call_tool("project_create_visual_check", arguments | invalid)
            save.assert_not_called()

    async def test_visual_check_tools_return_only_verified_overviews(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fake = FakeClient(root / "unused.png")
            server = create_server(fake)
            images = {}
            for key, mode in (("overview", "RGB"), ("grayscale", "L"), ("item-1", "RGBA")):
                path = root / f"{key}.png"
                with Image.new(mode, (1, 1)) as image:
                    image.save(path)
                images[key] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            item = {"component_id": "button", "run_id": "run-1", "label": "主按钮", "scale": 2}
            record = {"id": "check-1", "images": images}
            result = {"project_root": str(root), "revision": "updated", "visual_check": record}

            with patch.object(ProjectStore, "create_visual_check", return_value=result, create=True) as save:
                content = await server.call_tool("project_create_visual_check", {
                    "project_root": str(root), "expected_revision": "initial", "items": [item], "background": "#FFFFFF",
                })
                save.assert_called_once_with(str(root), "initial", [item | {"region": None, "scale": 2.0}],
                                             background="#FFFFFF", columns=3, grayscale=True)
            self.assertEqual(json.loads(content[0].text), result)
            self.assertEqual(len(content), 3)
            for returned, key in zip(content[1:], ("overview", "grayscale"), strict=True):
                self.assertEqual(returned.mimeType, "image/png")
                self.assertEqual(base64.b64decode(returned.data), Path(images[key]["path"]).read_bytes())

            overview_only = result | {"visual_check": record | {"images": {
                key: images[key] for key in ("overview", "item-1")
            }}}
            with patch.object(ProjectStore, "get_visual_check", return_value=overview_only, create=True) as fetch:
                arguments = {"project_root": str(root), "check_id": "check-1"}
                content = await server.call_tool("project_get_visual_check", arguments)
                fetch.assert_called_once_with(str(root), "check-1")
                self.assertEqual(len(content), 2)
                self.assertEqual(json.loads(content[0].text), overview_only)
                self.assertEqual(base64.b64decode(content[1].data), Path(images["overview"]["path"]).read_bytes())
                Path(images["overview"]["path"]).write_bytes(PNG)
                with self.assertRaisesRegex(Exception, "总览图 SHA-256 不符"):
                    await server.call_tool("project_get_visual_check", arguments)
            self.assertEqual(fake.calls, [])

    async def test_tools_forward_protocol_and_return_preview_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            png = root / "preview.png"
            png.write_bytes(PNG)
            fake = FakeClient(png)
            server = create_server(fake)
            tools = await server.list_tools()
            self.assertEqual({t.name for t in tools}, TOOL_NAMES)
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
                        self.assertEqual({tool.name for tool in tools.tools}, TOOL_NAMES)
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

    async def test_stdio_project_reference_style_and_component_persist_without_krita(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project_root = root / "中文项目"
            source = root / "参考色块.png"
            with Image.new("RGB", (4, 2), (20, 40, 60)) as image:
                for x in (2, 3):
                    for y in (0, 1):
                        image.putpixel((x, y), (200, 100, 50))
                image.save(source)
            original = source.read_bytes()
            original_mtime = source.stat().st_mtime_ns
            bridge_root = root / "unused-bridge"
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_BRIDGE_DIR": str(bridge_root), "SMARTUI_BRIDGE_TIMEOUT": "0.1"},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(tool_name, **arguments):
                        result = await session.call_tool(tool_name, arguments)
                        self.assertFalse(result.isError, result.content)
                        return json.loads(result.content[0].text)

                    project = await call("project_create", project_root=str(project_root),
                                         project_id="demo", name="色块按钮项目", paths={"references": "docs/ui/source-images"})
                    self.assertEqual(project["paths"], {
                        "documents": "docs/ui", "references": "docs/ui/source-images",
                        "programs": "art/ui/programs", "sources": "art/ui/sources",
                    })
                    first_revision = project["revision"]
                    for filename in ("style.md", "components.md"):
                        self.assertTrue((project_root / "docs" / "ui" / filename).is_file())
                    project = await call("project_add_reference", project_root=str(project_root),
                                         expected_revision=project["revision"], reference_id="colors", source_path=str(source))
                    self.assertEqual(project["references"]["colors"]["sha256"], hashlib.sha256(original).hexdigest())
                    archived = project_root / "docs" / "ui" / "source-images" / "colors" / source.name
                    self.assertEqual(Path(project["references"]["colors"]["source"]), archived)
                    self.assertEqual(archived.read_bytes(), original)
                    saved = json.loads((project_root / ".smartui" / "project.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved["references"]["colors"]["source"], "docs/ui/source-images/colors/参考色块.png")
                    inspection = await session.call_tool("project_inspect_reference", {
                        "project_root": str(project_root), "reference_id": "colors", "region": [2, 0, 2, 2],
                        "patches": [{"id": "left", "rect": [0, 0, 2, 2]}, {"id": "right", "rect": [2, 0, 2, 2]}],
                    })
                    self.assertFalse(inspection.isError, inspection.content)
                    self.assertIsNone(inspection.structuredContent)
                    samples = json.loads(inspection.content[0].text)
                    self.assertEqual([item["hex"] for item in samples["samples"]], ["#14283C", "#C86432"])
                    self.assertEqual(inspection.content[1].mimeType, "image/png")
                    with Image.open(io.BytesIO(base64.b64decode(inspection.content[1].data))) as preview:
                        self.assertEqual(preview.size, (2, 2))
                        self.assertEqual(preview.convert("RGB").getpixel((0, 0)), (200, 100, 50))
                    project = await call("project_update_style", project_root=str(project_root),
                                         expected_revision=project["revision"], description="暖色亮面按钮，参数待测",
                                         rules=[{"id": "face-color", "description": "正面使用右侧色块",
                                                 "evidence": [{"reference_id": "colors", "patch_id": "right"}]}],
                                         parameters={"draft": True, "color": "#C86432", "line_count": 64}, reference_ids=["colors"])
                    project = await call("project_save_program", project_root=str(project_root),
                                         expected_revision=project["revision"], program_id="custom-lines",
                                         name="自定义线条程序", description="项目自行定义画法与图层", source=CUSTOM_PROGRAM)
                    spec = {"program_id": "custom-lines", "extent": 128, "custom_label": "任意组件参数"}
                    project = await call("project_define_component", project_root=str(project_root),
                                         expected_revision=project["revision"], component_id="primary", name="主按钮", spec=spec)
                    context = await call("project_context", project_root=str(project_root))
                    self.assertEqual(context, project)
                    self.assertEqual(context["components"]["primary"]["spec"], spec)
                    self.assertIsNone(context["components"]["primary"]["latest_run"])
                    self.assertEqual(context["components"]["primary"]["attempts"], 0)
                    self.assertIsNone(context["style"]["user_confirmation"])
                    before_failure = {name: (project_root / name).read_bytes()
                                      for name in (".smartui/project.json", "docs/ui/style.md", "docs/ui/components.md")}
                    failure = await session.call_tool("project_update_style", {
                        "project_root": str(project_root), "expected_revision": first_revision,
                        "description": "过时写入", "rules": [], "parameters": {}, "reference_ids": [],
                    })
                    self.assertTrue(failure.isError)
                    self.assertIn("revision", failure.content[0].text)
                    for name, content in before_failure.items():
                        self.assertEqual((project_root / name).read_bytes(), content)
                    style_doc = (project_root / "docs" / "ui" / "style.md").read_text(encoding="utf-8")
                    components_doc = (project_root / "docs" / "ui" / "components.md").read_text(encoding="utf-8")
                    self.assertIn("暖色亮面按钮，参数待测", style_doc)
                    self.assertIn("face-color", style_doc)
                    self.assertIn("参考色块.png", style_doc)
                    self.assertIn("主按钮", components_doc)
                    self.assertIn("已定义规格，尚未生成", components_doc)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(source.stat().st_mtime_ns, original_mtime)
            self.assertEqual(list((bridge_root / "requests").glob("*.json")), [])

    async def test_stdio_project_program_save_get_and_plan_without_krita(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project_root = root / "程序项目"
            bridge_root = root / "unused-bridge"
            marker = root / "program-executed.txt"
            source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('已执行', encoding='utf-8')\n" + CUSTOM_PROGRAM
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_BRIDGE_DIR": str(bridge_root), "SMARTUI_BRIDGE_TIMEOUT": "0.1"},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(tool_name, **arguments):
                        result = await session.call_tool(tool_name, arguments)
                        self.assertFalse(result.isError, result.content)
                        return json.loads(result.content[0].text)

                    project = await call("project_create", project_root=str(project_root), project_id="custom", name="程序项目")
                    project = await call("project_update_style", project_root=str(project_root),
                                         expected_revision=project["revision"], description="自定义线条草稿", rules=[],
                                         parameters={"draft": True, "color": "#204060", "line_count": 96}, reference_ids=[])
                    project = await call("project_save_program", project_root=str(project_root),
                                         expected_revision=project["revision"], program_id="lines", name="参数化线条",
                                         description="自由定义节点、蒙版与批量笔触", source=source)
                    self.assertFalse(marker.exists(), "保存程序只能检查语法")
                    fetched = await call("project_get_program", project_root=str(project_root), program_id="lines")
                    self.assertEqual(fetched["source"], source)
                    self.assertEqual(fetched["version"], 1)
                    self.assertEqual(fetched["sha256"], hashlib.sha256(source.encode()).hexdigest())
                    project = await call("project_define_component", project_root=str(project_root),
                                         expected_revision=project["revision"], component_id="stripes", name="条纹图案",
                                         spec={"program_id": "lines", "extent": 120, "custom": {"任意参数": True}})
                    plan_result = await call("project_plan_component", project_root=str(project_root), component_id="stripes")
                    self.assertEqual(marker.read_text(encoding="utf-8"), "已执行")
                    self.assertEqual(plan_result["revision"], project["revision"])
                    self.assertEqual(plan_result["program_id"], "lines")
                    self.assertEqual(plan_result["program_version"], 1)
                    self.assertEqual(plan_result["program_sha256"], fetched["sha256"])
                    plan = plan_result["plan"]
                    self.assertEqual([plan["width"], plan["height"]], [120, 120])
                    self.assertEqual([node["kind"] for node in plan["nodes"]],
                                     ["grouplayer", "paintlayer", "transparencymask"])
                    self.assertEqual(plan["nodes"][0]["name"], "stripes 自定义分组")
                    self.assertEqual(plan["nodes"][2]["rects"], [[0, 0, 120, 120, 180]])
                    self.assertEqual(plan["paint"][0]["color"], "#204060")
                    self.assertEqual(len(plan["paint"][0]["strokes"]), 96)
                    self.assertEqual(plan["paint"][0]["strokes"][-1]["from"], [95, 0])
                    self.assertEqual(await call("project_context", project_root=str(project_root)), project)

                    modified = source.replace('extent = spec["extent"]', 'extent = spec["extent"] + 1')
                    project = await call("project_save_program", project_root=str(project_root),
                                         expected_revision=project["revision"], program_id="lines", name="参数化线条",
                                         description="更新画布算法", source=modified)
                    self.assertEqual(project["programs"]["lines"]["version"], 2)
                    self.assertTrue(project["components"]["stripes"]["needs_review"])
                    self.assertIsNone(project["components"]["stripes"]["latest_run"])
                    fetched = await call("project_get_program", project_root=str(project_root), program_id="lines")
                    self.assertEqual(fetched["source"], modified)
                    self.assertEqual(len(fetched["history"]), 2)
                    changed_plan = await call("project_plan_component", project_root=str(project_root), component_id="stripes")
                    self.assertEqual(changed_plan["plan"]["width"], 121)
                    self.assertEqual(changed_plan["program_version"], 2)

                    syntax_failure = await session.call_tool("project_save_program", {
                        "project_root": str(project_root), "expected_revision": project["revision"],
                        "program_id": "lines", "name": "参数化线条", "description": "无效语法",
                        "source": "def build(:\n",
                    })
                    self.assertTrue(syntax_failure.isError)
                    self.assertIn("语法无效", syntax_failure.content[0].text)
                    self.assertEqual(await call("project_context", project_root=str(project_root)), project)
                    failing_source = "raise RuntimeError('程序执行阶段的实际错误')\n"
                    project = await call("project_save_program", project_root=str(project_root),
                                         expected_revision=project["revision"], program_id="lines", name="参数化线条",
                                         description="语法有效但执行失败", source=failing_source)
                    self.assertEqual(project["programs"]["lines"]["version"], 3)
                    runtime_failure = await session.call_tool("project_plan_component", {
                        "project_root": str(project_root), "component_id": "stripes",
                    })
                    self.assertTrue(runtime_failure.isError)
                    self.assertIn("程序执行失败", runtime_failure.content[0].text)
                    self.assertIn("RuntimeError: 程序执行阶段的实际错误", runtime_failure.content[0].text)
                    self.assertEqual(await call("project_context", project_root=str(project_root)), project)
                    self.assertEqual(project["components"]["stripes"]["attempts"], 0)
            self.assertEqual(list((bridge_root / "requests").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
