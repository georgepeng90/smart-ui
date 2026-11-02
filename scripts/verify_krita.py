"""在真实 Krita 中验收原生绘制、蒙版、源文件修改和 MCP stdio。

运行示例：uv run --locked python -X utf8 scripts/verify_krita.py
脚本只在新建的 artifacts/verification-时间戳 目录写入绘画文件。
任何断言失败都会记录报告并以非零退出码结束，不会重新提交请求。
"""

import argparse
import asyncio
import base64
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smartui_mcp import BridgeClient, BridgeRemoteError  # noqa: E402


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def nodes_by_name(document):
    """根节点不属于保存的图层；只比较持久化的子节点 UUID。"""
    result = {}

    def visit(node):
        for child in node["children"]:
            if child["name"] in result:
                raise AssertionError("验收文档存在重名节点：" + child["name"])
            result[child["name"]] = child
            visit(child)

    visit(document["root"])
    return result


def pixel_difference(left, right):
    with Image.open(left) as image:
        first = image.convert("RGBA")
    with Image.open(right) as image:
        second = image.convert("RGBA")
    if first.size != second.size:
        raise AssertionError("比较图片的尺寸不同")
    first_pixels, second_pixels = first.tobytes(), second.tobytes()
    return sum(first_pixels[index:index + 4] != second_pixels[index:index + 4]
               for index in range(0, len(first_pixels), 4))


class Verification:
    def __init__(self, args):
        self.args = args
        self.directory = ROOT / "artifacts" / ("verification-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        self.directory.mkdir(parents=True, exist_ok=False)
        self.client = BridgeClient(args.bridge_dir, timeout=args.timeout)
        self.report = {
            "title": "Krita 原生绘制、蒙版与 MCP 实机验收",
            "started_at": datetime.now().astimezone().isoformat(),
            "status": "running", "directory": str(self.directory),
            "bridge_directory": str(self.client.bridge_dir),
            "brushes": {"comparison_a": args.brush_a, "comparison_b": args.brush_b, "texture": args.texture_brush},
            "checks": [], "calls": [], "evidence": {},
        }

    def check(self, description, condition, **details):
        self.report["checks"].append({"description": description, "passed": bool(condition), **details})
        if not condition:
            raise AssertionError(description + "：实际结果不符合要求；" + json.dumps(details, ensure_ascii=False))

    def call(self, method, params=None):
        start = time.perf_counter()
        entry = {"method": method, "params": params or {}}
        self.report["calls"].append(entry)
        try:
            result = self.client.call(method, params)
            entry["result"] = result
            return result
        except Exception as error:
            entry["error"] = str(error)
            raise
        finally:
            entry["wall_elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)

    def execute(self, operations):
        result = self.call("execute", {"operations": operations})
        self.check("批量操作全部完成", len(result["completed"]) == len(operations), operation_count=len(operations))
        return result

    def snapshot(self, document, label):
        result = self.call("inspect", {"document": str(document)})
        path = self.directory / (label + ".json")
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.report["evidence"][label] = str(path)
        return result

    def preview(self, document, label):
        result = self.call("preview", {"document": str(document)})
        path = self.directory / (label + ".png")
        shutil.copyfile(result["path"], path)
        with Image.open(path) as image:
            image.verify()
        self.report["evidence"][label] = {"path": str(path), "sha256": sha256(path)}
        return path

    def paint(self, document, node, brush, color, size, strokes, opacity=1.0):
        return {"op": "paint", "document": str(document), "node": "name:" + node,
                "brush": brush, "color": color, "size": size, "opacity": opacity, "strokes": strokes}

    def verify_brushes(self):
        presets = self.call("brushes", {})
        self.installed_brushes = set(presets)
        (self.directory / "installed-brushes.json").write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
        required = set(self.report["brushes"].values())
        self.check("指定笔刷均已安装且名称精确匹配", required <= set(presets), missing=sorted(required - set(presets)))
        self.check("比较使用两种不同预设", self.args.brush_a != self.args.brush_b)
        strokes = [
            {"kind": "line", "from": [24, 32], "to": [228, 32], "pressure_from": 0.2, "pressure_to": 1.0},
            {"kind": "line", "from": [24, 80], "to": [228, 104], "pressure_from": 1.0, "pressure_to": 0.2},
        ]
        proofs = []
        for index, brush in enumerate([self.args.brush_a, self.args.brush_b]):
            path = self.directory / ("brush-" + str(index + 1) + ".kra")
            result = self.execute([
                {"op": "document.create", "path": str(path), "width": 256, "height": 128, "name": "原生笔刷比较"},
                {"op": "layer.create", "document": str(path), "name": "比较笔触", "kind": "paintlayer"},
                self.paint(path, "比较笔触", brush, "#213547", 28, strokes),
                {"op": "document.save", "document": str(path)},
            ])
            paint_result = result["completed"][2]["result"]
            self.check("比较笔触使用 Krita 原生 paintLine", paint_result["native_methods"] == ["paintLine"], brush=brush)
            snapshot = self.snapshot(path, "brush-" + str(index + 1) + "-inspect")
            node = nodes_by_name(snapshot)["比较笔触"]
            preview = self.preview(path, "brush-" + str(index + 1))
            proofs.append({"brush": brush, "path": str(path), "uuid": node["id"], "pixel_sha256": node["pixel_sha256"],
                           "file_sha256": sha256(path), "preview": str(preview), "painting": paint_result})
            self.execute([{"op": "document.close", "document": str(path)}])
        difference = pixel_difference(proofs[0]["preview"], proofs[1]["preview"])
        self.report["brush_comparison"] = {"strokes": strokes, "results": proofs, "different_pixels": difference}
        self.check("两种原生预设沿相同轨迹产生不同像素", difference > 0 and proofs[0]["pixel_sha256"] != proofs[1]["pixel_sha256"], different_pixels=difference)

    def expect_batch_error(self, label, operations, failed_index, completed_count):
        try:
            self.call("execute", {"operations": operations})
        except BridgeRemoteError as error:
            entry = {"case": label, "request_id": error.request_id, "response_path": str(error.response_path),
                     "error": error.error}
            self.report.setdefault("negative_cases", []).append(entry)
            self.check(label + "返回预期失败下标与完成记录", error.error.get("operation_index") == failed_index and
                       len(error.error.get("completed", [])) == completed_count, error=error.error)
            return entry
        self.check(label + "必须报错", False)

    def verify_negative_cases(self):
        document = self.directory / "negative-cases.kra"
        target = "负例绘画目标"
        stroke = [{"kind": "line", "from": [20, 40], "to": [100, 80], "pressure_from": 0.2, "pressure_to": 1.0}]
        self.execute([
            {"op": "document.create", "path": str(document), "width": 128, "height": 128, "name": "真实错误与部分完成验收"},
            {"op": "layer.create", "document": str(document), "name": target, "kind": "paintlayer"},
            self.paint(document, target, self.args.brush_a, "#426B93", 14, stroke),
        ])
        baseline = nodes_by_name(self.snapshot(document, "negative-baseline-inspect"))[target]
        missing_brush = "__SmartUI_验收专用_未安装笔刷__"
        self.check("负例所用笔刷确实未安装", missing_brush not in self.installed_brushes)
        missing = self.expect_batch_error("未安装笔刷绘制", [self.paint(document, target, missing_brush, "#FF0000", 24, stroke)], 0, 0)
        after_missing = nodes_by_name(self.snapshot(document, "negative-missing-brush-inspect"))[target]
        missing["observed_target"] = after_missing
        self.check("未安装笔刷错误说明精确缺失名称", missing_brush in missing["error"]["message"])
        self.check("实测未安装笔刷未改变目标层 UUID 与像素", after_missing["id"] == baseline["id"] and after_missing["pixel_sha256"] == baseline["pixel_sha256"])

        self.execute([{"op": "layer.update", "document": str(document), "node": baseline["id"], "properties": {"locked": True}}])
        locked = self.expect_batch_error("锁定图层绘制", [self.paint(document, target, self.args.brush_a, "#FF0000", 24, stroke)], 0, 0)
        after_locked = nodes_by_name(self.snapshot(document, "negative-locked-layer-inspect"))[target]
        locked["observed_target"] = after_locked
        self.check("锁定图层错误明确说明已锁定", "锁定" in locked["error"]["message"])
        self.check("实测锁定图层保持锁定状态与原像素", after_locked["locked"] is True and after_locked["id"] == baseline["id"] and after_locked["pixel_sha256"] == baseline["pixel_sha256"])
        self.execute([{"op": "layer.update", "document": str(document), "node": baseline["id"], "properties": {"locked": False}}])

        retained, skipped = "批量失败前已创建图层", "批量失败后禁止创建图层"
        partial = self.expect_batch_error("批量第二项失败", [
            {"op": "layer.create", "document": str(document), "name": retained, "kind": "paintlayer"},
            {"op": "unsupported.operation", "document": str(document)},
            {"op": "layer.create", "document": str(document), "name": skipped, "kind": "paintlayer"},
        ], 1, 1)
        actual = nodes_by_name(self.snapshot(document, "negative-partial-batch-inspect"))
        partial["observed_nodes"] = actual
        completed = partial["error"]["completed"][0]
        self.check("批量失败没有回滚已完成的图层创建", retained in actual and completed["index"] == 0 and
                   completed["op"] == "layer.create" and completed["result"]["node"] == actual[retained]["id"])
        self.check("批量失败后停止执行后续操作", skipped not in actual)
        self.check("实测此错误批次未改变原绘画目标层像素", actual[target]["pixel_sha256"] == baseline["pixel_sha256"])
        self.report["batch_error_semantics"] = (
            "批量操作不是事务。completed 只记录完整返回的操作，失败操作自身仍可能部分生效；"
            "本报告对三个具体负例的像素与节点状态分别执行 inspect 后才作判断，不从 completed 推断未修改或自动回滚。"
        )
        self.execute([{"op": "document.save", "document": str(document)}, {"op": "document.close", "document": str(document)}])

    def verify_scene(self):
        document = self.directory / "editable-scene.kra"
        group = "原生绘制验收"
        highlight, texture, base, mask = "高光", "纹理", "底色", "高光透明度蒙版"
        operations = [{"op": "document.create", "path": str(document), "width": 512, "height": 384, "name": "原生绘制与局部修改"},
                      {"op": "layer.create", "document": str(document), "name": group, "kind": "grouplayer"}]
        # addChildNode(node, None) 添加到堆栈顶部，底色先创建以免遮挡其他层。
        operations += [{"op": "layer.create", "document": str(document), "name": name, "kind": "paintlayer", "parent": "name:" + group}
                       for name in [base, texture, highlight]]
        operations.append(self.paint(document, base, self.args.brush_a, "#254B61", 1,
                                     [{"kind": "rectangle", "rect": [0, 0, 512, 384], "fill": "foreground"}]))
        strokes = [{"kind": "line", "from": [28 + column * 46, 236 + row * 12],
                    "to": [51 + column * 46, 242 + row * 12], "pressure_from": 0.2, "pressure_to": 1.0}
                   for row in range(10) for column in range(10)]
        operations.append(self.paint(document, texture, self.args.texture_brush, "#83B6AE", 12, strokes, opacity=0.65))
        operations.append(self.paint(document, highlight, self.args.brush_a, "#FFD68A", 1,
                                     [{"kind": "rectangle", "rect": [64, 64, 384, 144], "fill": "foreground"}]))
        operations.append({"op": "mask.create", "document": str(document), "parent": "name:" + highlight,
                           "name": mask, "rects": [[64, 64, 192, 144, 255]]})
        result = self.execute(operations)
        texture_result = result["completed"][6]["result"]
        self.check("单次 paint 调用执行 100 条原生笔触", texture_result["stroke_count"] == 100 and texture_result["native_methods"] == ["paintLine"], result=texture_result)
        self.report["batch_100_strokes"] = {"batch_elapsed_ms": result["elapsed_ms"], "painting": texture_result}
        before_save = self.snapshot(document, "scene-before-save-inspect")
        before_nodes = nodes_by_name(before_save)
        self.check("分组包含底色、高光和纹理图层", set([highlight, texture, base]) <= {n["name"] for n in before_nodes[group]["children"]})
        self.check("高光图层挂载原生透明度蒙版", before_nodes[mask]["type"] == "transparencymask" and mask in {n["name"] for n in before_nodes[highlight]["children"]})
        masked_preview = self.preview(document, "scene-before-save")
        self.execute([{"op": "layer.update", "document": str(document), "node": "name:" + mask, "properties": {"visible": False}}])
        unmasked_preview = self.preview(document, "scene-mask-disabled")
        with Image.open(masked_preview) as image:
            masked = image.convert("RGBA")
        with Image.open(unmasked_preview) as image:
            unmasked = image.convert("RGBA")
        self.check("蒙版在未选中区域实际遮挡高光", masked.getpixel((340, 100)) != unmasked.getpixel((340, 100)),
                   masked_pixel=masked.getpixel((340, 100)), unmasked_pixel=unmasked.getpixel((340, 100)))
        self.check("蒙版选中区域保留高光像素", masked.getpixel((120, 100)) == unmasked.getpixel((120, 100)))
        self.execute([
            {"op": "layer.update", "document": str(document), "node": "name:" + mask, "properties": {"visible": True}},
            {"op": "document.save", "document": str(document)},
        ])
        first_file_hash = sha256(document)
        shutil.copyfile(document, self.directory / "scene-before-edit.kra")
        self.execute([{"op": "document.close", "document": str(document)}, {"op": "document.open", "path": str(document)}])
        reopened = self.snapshot(document, "scene-reopened-inspect")
        reopened_nodes = nodes_by_name(reopened)
        uuids = {name: n["id"] for name, n in before_nodes.items()}
        self.check("关闭并重开后所有保存节点 UUID 保持", uuids == {name: n["id"] for name, n in reopened_nodes.items()}, before=uuids, after={name: n["id"] for name, n in reopened_nodes.items()})
        self.check("关闭并重开后图层与蒙版像素哈希保持", {name: n.get("pixel_sha256") for name, n in before_nodes.items()} == {name: n.get("pixel_sha256") for name, n in reopened_nodes.items()})
        reopened_preview = self.preview(document, "scene-after-reopen")
        self.check("保存并重开后的预览像素相同", pixel_difference(masked_preview, reopened_preview) == 0)

        self.execute([
            {"op": "layer.clear", "document": str(document), "node": reopened_nodes[highlight]["id"]},
            self.paint(document, highlight, self.args.brush_a, "#E5F2FF", 1,
                       [{"kind": "ellipse", "rect": [80, 72, 336, 120], "fill": "foreground"}]),
            {"op": "mask.update", "document": str(document), "node": reopened_nodes[mask]["id"], "rects": [[160, 64, 224, 152, 255]]},
            {"op": "document.save", "document": str(document)},
        ])
        second_file_hash = sha256(document)
        after_edit = self.snapshot(document, "scene-after-edit-inspect")
        edited_nodes = nodes_by_name(after_edit)
        changes = {name: {"uuid_before": node["id"], "uuid_after": edited_nodes[name]["id"],
                          "pixel_sha256_before": node.get("pixel_sha256"), "pixel_sha256_after": edited_nodes[name].get("pixel_sha256")}
                   for name, node in reopened_nodes.items()}
        self.report["source_edit"] = {"path": str(document), "file_sha256_before": first_file_hash,
                                      "file_sha256_after": second_file_hash, "nodes": changes}
        self.check("原路径保存后源文件哈希发生变化", first_file_hash != second_file_hash)
        self.check("局部修改保留所有原节点 UUID", uuids == {name: n["id"] for name, n in edited_nodes.items()})
        for name in [base, texture]:
            self.check("局部修改未改变" + name + "像素", changes[name]["pixel_sha256_before"] == changes[name]["pixel_sha256_after"])
        for name in [highlight, mask]:
            self.check("局部修改改变了" + name + "像素", changes[name]["pixel_sha256_before"] != changes[name]["pixel_sha256_after"])
        edited_preview = self.preview(document, "scene-after-edit")
        self.check("局部修改后的预览发生变化", pixel_difference(reopened_preview, edited_preview) > 0)
        with zipfile.ZipFile(document) as archive:
            xml_data = archive.read("maindoc.xml")
        (self.directory / "maindoc.xml").write_bytes(xml_data)
        xml_root = ET.fromstring(xml_data)
        xml_highlights = [element for element in xml_root.iter() if element.attrib.get("name") == highlight]
        native_mask = [child for layer in xml_highlights for child in layer.iter()
                       if child.attrib.get("name") == mask and child.attrib.get("nodetype") == "transparencymask"]
        self.check("KRA 内部 XML 保存高光下的原生透明度蒙版", len(native_mask) == 1)
        self.execute([{"op": "document.close", "document": str(document)}, {"op": "document.open", "path": str(document)}])
        final = self.snapshot(document, "scene-final-reopened-inspect")
        final_nodes = nodes_by_name(final)
        self.check("修改后再次重开保持节点 UUID 和像素", all(final_nodes[name]["id"] == n["id"] and final_nodes[name].get("pixel_sha256") == n.get("pixel_sha256") for name, n in edited_nodes.items()))
        return document, final

    async def verify_mcp(self, document, expected):
        uv = shutil.which("uv")
        self.check("uv 命令已安装，可启动正式 MCP 入口", uv is not None)
        parameters = StdioServerParameters(
            command=uv, args=["run", "--locked", "--project", str(ROOT), "python", "-m", "smartui_mcp"],
            cwd=str(ROOT), env={**os.environ, "SMARTUI_BRIDGE_DIR": str(self.client.bridge_dir),
                               "SMARTUI_BRIDGE_TIMEOUT": str(self.client.timeout)},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self.client.timeout + 30)) as session:
                handshake = await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                self.check("真实 MCP stdio 握手和五个工具发现成功", handshake.serverInfo.name == "SmartUI Krita" and names == {"krita_status", "krita_list_brushes", "krita_inspect_document", "krita_execute_batch", "krita_preview"})
                status = await session.call_tool("krita_status", {})
                self.check("MCP status 实际连接 Krita", not status.isError and json.loads(status.content[0].text)["protocol_version"] == 1)
                inspected = await session.call_tool("krita_inspect_document", {"document": str(document)})
                self.check("MCP inspect 成功返回真实文档", not inspected.isError)
                actual = json.loads(inspected.content[0].text)
                self.check("MCP inspect 与直接队列检查的节点一致", nodes_by_name(actual) == nodes_by_name(expected))
                preview = await session.call_tool("krita_preview", {"document": str(document)})
                self.check("MCP preview 返回图片和路径", not preview.isError and len(preview.content) == 2 and preview.content[1].type == "image" and preview.content[1].mimeType == "image/png")
                exported = Path(json.loads(preview.content[0].text)["path"])
                binary = base64.b64decode(preview.content[1].data, validate=True)
                self.check("MCP 图片内容与导出 PNG 完全相同", binary == exported.read_bytes())
                artifact = self.directory / "mcp-preview.png"
                artifact.write_bytes(binary)
                self.check("MCP 图片与最终绘画预览像素一致", pixel_difference(artifact, self.directory / "scene-after-edit.png") == 0)
                self.report["mcp"] = {"server": handshake.serverInfo.model_dump(), "tools": sorted(names),
                                      "launch": {"command": parameters.command, "args": parameters.args, "cwd": str(parameters.cwd)},
                                      "preview_path": str(artifact), "preview_sha256": sha256(artifact), "status": json.loads(status.content[0].text)}

    def write_report(self):
        self.report["finished_at"] = datetime.now().astimezone().isoformat()
        (self.directory / "report.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# Krita 原生绘制、蒙版与 MCP 实机验收", "", "验收结果：" + {"passed": "全部通过", "failed": "失败"}.get(self.report["status"], "未完成"),
                 "", "开始时间：" + self.report["started_at"], "", "证据目录：`" + str(self.directory) + "`", "",
                 "对比预设：`" + self.args.brush_a + "`、`" + self.args.brush_b + "`；纹理预设：`" + self.args.texture_brush + "`。", "",
                 "## 检查结果", ""]
        lines += ["- " + ("通过：" if check["passed"] else "失败：") + check["description"] for check in self.report["checks"]]
        if "error" in self.report:
            lines += ["", "## 失败原因", "", self.report["error"]]
        source = self.report.get("source_edit")
        if source:
            lines += ["", "## 同一源文件在局部修改前后的两次保存", "", "同一文件：`" + source["path"] + "`", "",
                      "修改前 SHA256：`" + source["file_sha256_before"] + "`", "",
                      "修改后 SHA256：`" + source["file_sha256_after"] + "`", "",
                      "根节点 UUID 不属于持久化图层，重开文件时可能变化；下面只比较真实子节点。", "",
                      "| 节点 | 原 UUID | 修改后 UUID | 修改前像素 SHA256 | 修改后像素 SHA256 |", "| --- | --- | --- | --- | --- |"]
            lines += [f"| {name} | `{node['uuid_before']}` | `{node['uuid_after']}` | `{node['pixel_sha256_before']}` | `{node['pixel_sha256_after']}` |" for name, node in source["nodes"].items()]
        if "brush_comparison" in self.report:
            comparison = self.report["brush_comparison"]
            lines += ["", "两种预设的预览差异像素数：" + str(comparison["different_pixels"]) + "。"]
        if "batch_100_strokes" in self.report:
            batch = self.report["batch_100_strokes"]
            lines += ["", f"单次原生绘制 100 笔耗时：{batch['painting']['elapsed_ms']} ms；包含场景创建的整批耗时：{batch['batch_elapsed_ms']} ms。"]
        if "batch_error_semantics" in self.report:
            lines += ["", "## 错误处理与批量执行语义", "", self.report["batch_error_semantics"]]
        lines += ["", "## 预览与详细证据", "", "![保存前预览](scene-before-save.png)", "", "![保存并重开后的预览](scene-after-reopen.png)",
                  "", "![修改高光与蒙版后的预览](scene-after-edit.png)", "",
                  "完整调用参数、返回结果、笔刷信息、节点 UUID、图层与蒙版像素哈希见 [report.json](report.json)。", ""]
        (self.directory / "report.md").write_text("\n".join(lines), encoding="utf-8")

    def run(self):
        print("验收证据目录：" + str(self.directory), flush=True)
        try:
            self.report["krita_status"] = self.call("status", {})
            self.verify_brushes()
            self.verify_negative_cases()
            document, final = self.verify_scene()
            asyncio.run(self.verify_mcp(document, final))
            self.report["status"] = "passed"
        except Exception as error:
            self.report["status"] = "failed"
            self.report["error"] = type(error).__name__ + "：" + str(error)
            raise
        finally:
            self.write_report()
            print("报告：" + str(self.directory / "report.md"), flush=True)


def main():
    parser = argparse.ArgumentParser(description="在真实 Krita 中运行绘制验收；请勿同时操作验收文档。")
    parser.add_argument("--bridge-dir", type=Path, default=None, help="队列目录；默认读取 SMARTUI_BRIDGE_DIR")
    parser.add_argument("--timeout", type=float, default=120, help="单次请求超时秒数，默认 120；超时不重试")
    parser.add_argument("--brush-a", default="b) Basic-5 Size", help="第一种比较预设的精确名称")
    parser.add_argument("--brush-b", default="h) Chalk Grainy", help="第二种比较预设的精确名称")
    parser.add_argument("--texture-brush", default="h) Chalk Grainy", help="100 笔纹理所用预设的精确名称")
    Verification(parser.parse_args()).run()


if __name__ == "__main__":
    main()
