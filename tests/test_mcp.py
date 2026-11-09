import base64
from contextlib import contextmanager
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


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1foAAAAASUVORK5CYII=")
TOOL_NAMES = {
    "photocraft_status", "photocraft_inspect_document", "photocraft_preview",
    "project_create", "project_context", "project_add_reference", "project_inspect_reference",
    "project_update_style", "project_update_typography", "project_update_ui_settings",
    "project_check_localization", "project_compare_unity_screenshot",
    "project_define_component", "project_draw_component",
    "project_save_program", "project_get_program", "project_plan_component",
    "project_compare_component", "project_get_comparison", "project_create_visual_check", "project_get_visual_check",
    "project_record_feedback", "project_update_feedback",
    "project_register_image", "project_get_image",
    "project_export_assets", "project_get_export",
    "project_save_layout", "project_get_layout",
    "project_build_layout", "project_get_page_build",
    "project_page_dependencies", "project_rebuild_pages", "project_preview_layout_variants",
    "project_create_composition", "project_get_composition",
    "unity_protocol_schema", "project_unity_context", "project_define_unity_control",
    "project_register_unity_prefab", "project_prepare_unity_handoff",
    "project_unity_build_context", "project_configure_unity_build", "project_define_unity_recipe",
    "project_set_unity_bindings", "project_prepare_unity_build", "project_execute_unity_build",
    "project_reconcile_unity_build",
}

CUSTOM_PROGRAM = '''def build(parameters, spec, component_id):
    extent = spec["extent"]
    nodes = [{"key": "group", "name": component_id + " 自定义分组", "kind": "group", "parent": "root"}]
    for index in range(parameters["line_count"]):
        nodes.append({"key": "line-" + str(index), "name": "线条 " + str(index),
                      "kind": "shape", "parent": "group",
                      "shape": {"kind": "rect", "rect": [index, 0, 1, extent],
                                "fill": parameters["color"], "stroke": None}})
    return {"schema_version": 1, "width": extent, "height": extent, "nodes": nodes}
'''


class FakeClient:
    def __init__(self, preview_path):
        self.preview_path = preview_path
        self.calls = []

    def status(self):
        self.calls.append(("status", {}))
        return {"version": "synthetic transport fixture", "cli_path": "test", "sha256": "0" * 64}

    @contextmanager
    def session(self, root, *, preview_only):
        self.calls.append(("session", str(root), preview_only))
        yield self

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "doc.open":
            return {"width": 1, "height": 1, "warnings": []}
        if method == "doc.render":
            return {"mime": "image/png", "base64": base64.b64encode(self.preview_path.read_bytes()).decode()}
        raise AssertionError(method)


class MCPToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_unity_catalog_and_handoff_work_over_stdio_without_renderer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(root / "missing-cli.exe")},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(tool_name, **arguments):
                        response = await session.call_tool(tool_name, arguments)
                        self.assertFalse(response.isError, response.content)
                        return json.loads(response.content[0].text)

                    await call("project_create", project_root=str(root), project_id="unity-example", name="协议测试")
                    schema = await call("unity_protocol_schema")
                    self.assertIn("handoff_manifest", schema)
                    initial = await call("project_unity_context", project_root=str(root))
                    self.assertIsNone(initial["revision"])
                    definition = {
                        "name": "面板", "description": "仅用于协议测试的合成控件", "control_type": "panel",
                        "base_control_id": None, "dependencies": [], "source_component_ids": [],
                        "asset_ids": [], "text_style_ids": [], "preview_asset_id": None,
                        "layout": {"min_width": 1, "min_height": 1, "max_width": None, "max_height": None},
                        "slots": {}, "parameters": {}, "states": {},
                    }
                    defined = await call("project_define_unity_control", project_root=str(root),
                                         expected_revision=None, control_id="panel", definition=definition)
                    self.assertEqual(defined["controls"]["panel"]["file_status"], "not_registered")
                    prefab = root / "Assets/UI/Panel.prefab"
                    prefab.parent.mkdir(parents=True)
                    # 合成文本只验证协议往返，没有经过 Unity 导入。
                    prefab.write_text("%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
                                      "--- !u!1 &10\nGameObject:\n  m_Name: SyntheticPanel\n", encoding="utf-8")
                    guid = "ab" * 16
                    prefab.with_suffix(".prefab.meta").write_text(f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8")
                    registered = await call("project_register_unity_prefab", project_root=str(root),
                                            expected_revision=defined["revision"], control_id="panel",
                                            path="Assets/UI/Panel.prefab", guid=guid, slot_bindings={})
                    request = {"schema_version": 1, "adapter_id": "test", "adapter_version": "1",
                               "pages": [{"id": "home", "name": "主页", "width": 100, "height": 100,
                                          "instances": [{"id": "content", "control_id": "panel", "x": 0, "y": 0,
                                                         "width": 100, "height": 100, "parameters": {}, "bindings": {}}]}]}
                    prepared = await call("project_prepare_unity_handoff", project_root=str(root),
                                          expected_revision=registered["revision"], handoff_id="ui", request=request)
                    self.assertEqual(prepared["handoff"]["unity_validation"], "not_performed")
                    self.assertEqual(prepared["handoff"]["controls"]["panel"]["prefab"]["guid"], guid)
                    self.assertTrue(Path(prepared["handoff_path"]).is_file())
                    self.assertIn("面板", Path(prepared["document"]).read_text(encoding="utf-8"))
                    reopened = await call("project_unity_context", project_root=str(root))
                    self.assertEqual(reopened["revision"], prepared["revision"])
                    self.assertEqual(reopened["controls"]["panel"]["unity_validation"], "not_performed")

    async def test_unity_build_tools_work_over_stdio_without_an_editor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project_root = str(root / "game")
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(root / "missing-cli.exe")},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(tool_name, **arguments):
                        response = await session.call_tool(tool_name, {"project_root": project_root, **arguments})
                        self.assertFalse(response.isError, response.content)
                        return json.loads(response.content[0].text)

                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    self.assertEqual(set(tools), TOOL_NAMES)
                    prepare_schema = tools["project_prepare_unity_build"].inputSchema
                    self.assertEqual(prepare_schema["properties"]["operation"]["enum"], ["plan", "apply", "verify"])
                    self.assertEqual(prepare_schema["properties"]["operation"]["default"], "plan")
                    project = await call("project_create", project_id="fixture", name="控件构建测试")
                    initial = await call("project_unity_build_context")
                    self.assertIsNone(initial["revision"])
                    self.assertEqual(initial["source_revision"], project["revision"])
                    self.assertEqual(initial["operations"], [])
                    configured = await call("project_configure_unity_build", expected_revision=None,
                                            config={"output_root": "Assets/SmartUI", "fonts": {}})
                    image = root / "panel.png"
                    Image.new("RGBA", (32, 32), (24, 100, 175, 255)).save(image)
                    await call("project_register_image", expected_revision=project["revision"], asset_id="background",
                               name="面板背景", source_path=str(image),
                               provenance={"kind": "imported", "description": "构建请求的合成图像"})
                    project = await call("project_context")
                    definition = {
                        "name": "面板", "description": "", "control_type": "panel",
                        "base_control_id": None, "dependencies": [], "source_component_ids": [],
                        "asset_ids": ["background"], "text_style_ids": [], "preview_asset_id": None,
                        "layout": {"min_width": 1, "min_height": 1, "max_width": None, "max_height": None},
                        "slots": {"content": {"kind": "content", "component_type": "RectTransform",
                                              "required": True, "description": ""}},
                        "parameters": {}, "states": {},
                    }
                    await call("project_define_unity_control", expected_revision=None, control_id="panel",
                               definition=definition)
                    defined = await call("project_define_unity_recipe", expected_revision=configured["revision"],
                                         control_id="panel", recipe={
                                             "kind": "base", "width": 32, "height": 32, "button": None,
                                             "slots": {"content": {"node_id": "root", "component": "rect"}},
                                             "nodes": [{"id": "art", "kind": "image", "asset_id": "background",
                                                        "x": 0, "y": 0, "width": 32, "height": 32}],
                                         })
                    page = await call("project_save_layout", expected_revision=project["revision"],
                                      layout_id="home", name="主页", width=100, height=100, background="#123456",
                                      nodes=[{"id": "panel-instance", "kind": "control", "control_id": "panel",
                                              "x": 3, "y": 5, "width": 32, "height": 32, "parameters": {}}])
                    bindings = {"panel-instance": {"content": "home.content"}}
                    bound = await call("project_set_unity_bindings", expected_revision=defined["revision"],
                                       page_id="home", bindings=bindings)
                    prepared = await call("project_prepare_unity_build", expected_revision=bound["revision"],
                                          control_ids=["panel"], page_ids=[])
                    self.assertEqual(prepared["status"], "prepared")
                    self.assertEqual(prepared["unity_execution"], "not_performed")
                    self.assertEqual(json.loads(Path(prepared["request_path"]).read_bytes()), prepared["request"])
                    self.assertEqual(prepared["request"]["targets"][0]["path"], "Assets/SmartUI/Controls/panel.prefab")
                    current = await call("project_unity_build_context")
                    self.assertEqual(current["source_revision"], page["revision"])
                    self.assertEqual(current["construction"]["bindings"]["home"], bindings)
                    self.assertEqual(current["operations"][0]["request_id"], prepared["request_id"])

                    for tool, request_id, message in (
                        ("project_execute_unity_build", "invalid", "request_id"),
                        ("project_reconcile_unity_build", prepared["request_id"], "尚未执行"),
                    ):
                        failure = await session.call_tool(tool, {"project_root": project_root, "request_id": request_id})
                        self.assertTrue(failure.isError)
                        self.assertIn(message, failure.content[0].text)
                    self.assertFalse((Path(project_root) / "Assets").exists())

    async def test_visual_check_tools_require_strict_options(self):
        server = create_server(FakeClient(Path("unused.png")))
        tools = {tool.name: tool for tool in await server.list_tools()}
        create = tools["project_create_visual_check"]
        self.assertIsNone(create.outputSchema)
        self.assertIsNone(tools["project_get_visual_check"].outputSchema)
        self.assertEqual(set(create.inputSchema["required"]), {"project_root", "expected_revision", "check_id", "items", "background"})
        item_schema = create.inputSchema["$defs"]["VisualCheckItem"]
        self.assertEqual(set(item_schema["required"]), {"component_id", "label"})
        self.assertFalse(item_schema["additionalProperties"])
        item = {"component_id": "button", "label": "主按钮"}
        arguments = {"project_root": "unused", "expected_revision": "rev", "check_id": "check-1",
                     "items": [item], "background": "#FFFFFF"}
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
            item = {"component_id": "button", "label": "主按钮", "scale": 2}
            record = {"id": "check-1", "images": images}
            result = {"project_root": str(root), "revision": "updated", "visual_check": record}

            with patch.object(ProjectStore, "create_visual_check", return_value=result, create=True) as save:
                content = await server.call_tool("project_create_visual_check", {
                    "project_root": str(root), "expected_revision": "initial", "check_id": "check-1",
                    "items": [item], "background": "#FFFFFF",
                })
                save.assert_called_once_with(str(root), "initial", "check-1", [item | {"region": None, "scale": 2.0}],
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

    async def test_current_tools_return_native_inspection_and_document_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            png = root / "preview.png"
            png.write_bytes(PNG)
            fake = FakeClient(png)
            server = create_server(fake)
            tools = await server.list_tools()
            self.assertEqual({t.name for t in tools}, TOOL_NAMES)
            self.assertIsNone(next(t for t in tools if t.name == "photocraft_preview").outputSchema)
            document = str(root / "测试.pcraft")
            Path(document).write_bytes(b"synthetic document")
            await server.call_tool("photocraft_status", {})
            with patch("smartui_mcp.server.read_document", return_value={"width": 1, "nodes": [], "manifest": {}}) as inspect:
                await server.call_tool("photocraft_inspect_document", {"document": document})
                inspect.assert_called_once_with(Path(document))
            preview = await server.call_tool("photocraft_preview", {"document": document})
            session_directory = Path(fake.calls[1][1])
            self.assertEqual(session_directory.parent, root / ".smartui" / "temp")
            self.assertEqual(fake.calls, [("status", {}), ("session", str(session_directory), True),
                ("doc.open", {"path": "测试.pcraft"}), ("doc.render", {"maxSide": 0})])
            self.assertEqual(Path(document).read_bytes(), b"synthetic document")
            self.assertEqual(json.loads(preview[0].text), {"document": document, "width": 1, "height": 1})
            self.assertEqual(preview[1].mimeType, "image/png")
            self.assertEqual(base64.b64decode(preview[1].data), PNG)

    async def test_document_validation_prevents_submission(self):
        fake = FakeClient(Path("unused.png"))
        server = create_server(fake)
        for document in ["relative.pcraft", str(Path.cwd() / "wrong.png")]:
            with self.subTest(document=document), self.assertRaisesRegex(Exception, "绝对 .pcraft 路径"):
                await server.call_tool("photocraft_inspect_document", {"document": document})
        self.assertEqual(fake.calls, [])

    async def test_stdio_reports_missing_renderer_without_preventing_project_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary).resolve() / "missing-cli.exe"
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(missing)},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    initialize = await session.initialize()
                    self.assertEqual(initialize.serverInfo.name, "SmartUI PhotoCraft")
                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, TOOL_NAMES)
                    result = await session.call_tool("photocraft_status", {})
                    self.assertTrue(result.isError)
                    self.assertIn(str(missing), result.content[0].text)
                    created = await session.call_tool("project_create", {
                        "project_root": str(Path(temporary).resolve() / "project"),
                        "project_id": "example", "name": "不依赖绘图端的项目操作"})
                    self.assertFalse(created.isError, created.content)

    async def test_stdio_project_reference_style_and_component_persist_without_renderer(self):
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
            missing_cli = root / "missing-cli.exe"
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(missing_cli)},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(tool_name, **arguments):
                        result = await session.call_tool(tool_name, arguments)
                        self.assertFalse(result.isError, result.content)
                        return json.loads(result.content[0].text)

                    project = await call("project_create", project_root=str(project_root),
                                         project_id="demo", name="色块按钮项目")
                    self.assertEqual(project["paths"], {
                        "documents": "smart-ui/docs", "references": "smart-ui/references",
                        "programs": "smart-ui/programs", "sources": "smart-ui/sources",
                    })
                    first_revision = project["revision"]
                    for filename in ("style.md", "components.md"):
                        self.assertTrue((project_root / "smart-ui" / "docs" / filename).is_file())
                    project = await call("project_add_reference", project_root=str(project_root),
                                         expected_revision=project["revision"], reference_id="colors", source_path=str(source))
                    self.assertEqual(project["references"]["colors"]["sha256"], hashlib.sha256(original).hexdigest())
                    archived = project_root / "smart-ui" / "references" / "colors" / source.name
                    self.assertEqual(Path(project["references"]["colors"]["source"]), archived)
                    self.assertEqual(archived.read_bytes(), original)
                    saved = json.loads((project_root / "smart-ui" / "project.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved["references"]["colors"]["source"], "smart-ui/references/colors/参考色块.png")
                    self.assertNotIn("preview", saved["references"]["colors"])
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
                    self.assertIsNone(context["style"]["user_confirmation"])
                    before_failure = {name: (project_root / name).read_bytes()
                                      for name in ("smart-ui/project.json", "smart-ui/docs/style.md", "smart-ui/docs/components.md")}
                    failure = await session.call_tool("project_update_style", {
                        "project_root": str(project_root), "expected_revision": first_revision,
                        "description": "过时写入", "rules": [], "parameters": {}, "reference_ids": [],
                    })
                    self.assertTrue(failure.isError)
                    self.assertIn("revision", failure.content[0].text)
                    for name, content in before_failure.items():
                        self.assertEqual((project_root / name).read_bytes(), content)
                    style_doc = (project_root / "smart-ui" / "docs" / "style.md").read_text(encoding="utf-8")
                    components_doc = (project_root / "smart-ui" / "docs" / "components.md").read_text(encoding="utf-8")
                    self.assertIn("暖色亮面按钮，参数待测", style_doc)
                    self.assertIn("face-color", style_doc)
                    self.assertIn("参考色块.png", style_doc)
                    self.assertIn("主按钮", components_doc)
                    self.assertIn("需要复查：是", components_doc)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(source.stat().st_mtime_ns, original_mtime)
            self.assertFalse(missing_cli.exists())

    async def test_stdio_project_program_save_get_and_plan_without_renderer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project_root = root / "程序项目"
            missing_cli = root / "missing-cli.exe"
            marker = root / "program-executed.txt"
            source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('已执行', encoding='utf-8')\n" + CUSTOM_PROGRAM
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "smartui_mcp"],
                cwd=str(Path(__file__).resolve().parents[1]),
                env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(missing_cli)},
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
                    self.assertEqual(fetched["sha256"], hashlib.sha256(source.encode()).hexdigest())
                    project = await call("project_define_component", project_root=str(project_root),
                                         expected_revision=project["revision"], component_id="stripes", name="条纹图案",
                                         spec={"program_id": "lines", "extent": 120, "custom": {"任意参数": True}})
                    plan_result = await call("project_plan_component", project_root=str(project_root), component_id="stripes")
                    self.assertEqual(marker.read_text(encoding="utf-8"), "已执行")
                    self.assertEqual(plan_result["revision"], project["revision"])
                    self.assertEqual(plan_result["program_id"], "lines")
                    self.assertEqual(plan_result["program_sha256"], fetched["sha256"])
                    plan = plan_result["plan"]
                    self.assertEqual([plan["width"], plan["height"]], [120, 120])
                    self.assertEqual(plan["nodes"][0]["kind"], "group")
                    self.assertEqual(plan["nodes"][0]["name"], "stripes 自定义分组")
                    self.assertEqual(len(plan["nodes"]), 97)
                    self.assertTrue(all(node["kind"] == "shape" for node in plan["nodes"][1:]))
                    self.assertEqual(plan["nodes"][1]["shape"]["fill"], "#204060")
                    self.assertEqual(plan["nodes"][-1]["shape"]["rect"], [95, 0, 1, 120])
                    self.assertEqual(await call("project_context", project_root=str(project_root)), project)

                    modified = source.replace('extent = spec["extent"]', 'extent = spec["extent"] + 1')
                    project = await call("project_save_program", project_root=str(project_root),
                                         expected_revision=project["revision"], program_id="lines", name="参数化线条",
                                         description="更新画布算法", source=modified)
                    self.assertTrue(project["components"]["stripes"]["needs_review"])
                    self.assertIsNone(project["components"]["stripes"]["latest_run"])
                    fetched = await call("project_get_program", project_root=str(project_root), program_id="lines")
                    self.assertEqual(fetched["source"], modified)
                    self.assertEqual((project_root / fetched["source_path"]).read_text(encoding="utf-8"), modified)
                    changed_plan = await call("project_plan_component", project_root=str(project_root), component_id="stripes")
                    self.assertEqual(changed_plan["plan"]["width"], 121)
                    self.assertEqual(changed_plan["program_sha256"], hashlib.sha256(modified.encode()).hexdigest())

                    syntax_failure = await session.call_tool("project_save_program", {
                        "project_root": str(project_root), "expected_revision": project["revision"],
                        "program_id": "lines", "name": "参数化线条", "description": "无效语法",
                        "source": "def build(:\n",
                    })
                    self.assertTrue(syntax_failure.isError)
                    self.assertIn("语法错误", syntax_failure.content[0].text)
                    self.assertEqual(await call("project_context", project_root=str(project_root)), project)
                    failing_source = "raise RuntimeError('程序执行阶段的实际错误')\n"
                    project = await call("project_save_program", project_root=str(project_root),
                                         expected_revision=project["revision"], program_id="lines", name="参数化线条",
                                         description="语法有效但执行失败", source=failing_source)
                    self.assertEqual(project["programs"]["lines"]["sha256"], hashlib.sha256(failing_source.encode()).hexdigest())
                    runtime_failure = await session.call_tool("project_plan_component", {
                        "project_root": str(project_root), "component_id": "stripes",
                    })
                    self.assertTrue(runtime_failure.isError)
                    self.assertIn("程序执行失败", runtime_failure.content[0].text)
                    self.assertIn("RuntimeError: 程序执行阶段的实际错误", runtime_failure.content[0].text)
                    self.assertEqual(await call("project_context", project_root=str(project_root)), project)
                    self.assertIsNone(project["components"]["stripes"]["latest_run"])
            self.assertFalse(missing_cli.exists())


if __name__ == "__main__":
    unittest.main()
