"""在已迁移的 SlimeBlock 项目上验证通用程序、依赖状态和任意节点局部修改。"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


async def run(project_root):
    report = {"status": "running", "calls": []}
    path = project_root / ".smartui/integration/project-program-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    common = {"project_root": str(project_root)}
    try:
        async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"], cwd=str(ROOT))) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 15

                async def call(tool_name, **arguments):
                    result = await session.call_tool(tool_name, {**common, **arguments})
                    if result.isError:
                        raise RuntimeError(result.content[0].text)
                    value = json.loads(result.content[0].text)
                    report["calls"].append({"tool": tool_name, "result": value})
                    return value

                ctx = await call("project_context")
                buttons = [key for key, value in ctx["components"].items() if value["spec"]["program_id"] == "slimeblock.buttons"]
                for key in buttons:
                    if ctx["components"][key]["needs_review"]:
                        await call("project_draw_component", expected_revision=ctx["revision"], component_id=key)
                        ctx = await call("project_context")
                hashes = {key: hashlib.sha256(Path(ctx["components"][key]["latest_run"]["document"]).read_bytes()).hexdigest() for key in buttons}
                source = (ROOT / "examples/drawing-programs/radial_badge.py").read_text(encoding="utf-8")
                ctx = await call("project_save_program", expected_revision=ctx["revision"], program_id="slimeblock.badge",
                                 name="圆形徽章绘制程序", description="项目定义圆盘、循环条纹、蒙版和星形，用于验证任意结构", source=source)
                spec = {"program_id": "slimeblock.badge", "size": 192, "clip_width": 96,
                        "face": "#8CCC47", "edge": "#4B6818", "shine": "#FDF0AF"}
                component_id = "badge.star.program-check"
                ctx = await call("project_define_component", expected_revision=ctx["revision"], component_id=component_id,
                                 name="通用程序验证用圆形徽章", spec=spec)
                plan = await call("project_plan_component", component_id=component_id)
                assert len(plan["plan"]["nodes"]) == 4
                assert len(plan["plan"]["paint"][2]["strokes"]) == 16
                first = await call("project_draw_component", expected_revision=ctx["revision"], component_id=component_id)
                ctx = await call("project_context")
                original_ids = ctx["components"][component_id]["latest_run"]["layer_ids"]
                updated_source = source.replace('"opacity": 0.35', '"opacity": 0.6')
                assert updated_source != source
                ctx = await call("project_save_program", expected_revision=ctx["revision"], program_id="slimeblock.badge",
                                 name="圆形徽章绘制程序", description="提高条纹不透明度，其他绘制逻辑保持不变", source=updated_source)
                assert ctx["components"][component_id]["needs_review"]
                assert all(not ctx["components"][key]["needs_review"] for key in buttons)
                assert ctx["components"][component_id]["latest_run"]["program_version"] == first["program_version"]
                edited = await call("project_draw_component", expected_revision=ctx["revision"], component_id=component_id, layers=["stripes"])
                assert edited["verification"]["unchanged_other_nodes"] and edited["verification"]["target_pixels_changed"]
                assert edited["program_version"] == first["program_version"] + 1
                ctx = await call("project_context")
                spec["clip_width"] = 64
                ctx = await call("project_define_component", expected_revision=ctx["revision"], component_id=component_id,
                                 name="通用程序验证用圆形徽章", spec=spec)
                mask = await call("project_draw_component", expected_revision=ctx["revision"], component_id=component_id, layers=["clip"])
                assert mask["verification"]["unchanged_other_nodes"] and mask["verification"]["target_pixels_changed"]
                ctx = await call("project_context")
                assert ctx["components"][component_id]["latest_run"]["layer_ids"] == original_ids
                assert all(not value["needs_review"] for value in ctx["components"].values())
                for key, digest in hashes.items():
                    assert hashlib.sha256(Path(ctx["components"][key]["latest_run"]["document"]).read_bytes()).hexdigest() == digest
                report.update(status="passed", checks={"tools": 15, "custom_program_nodes": 4, "loop_strokes": 16,
                    "program_version_marks_only_dependents": True, "program_update_local_edit": True,
                    "mask_local_edit": True, "other_assets_unchanged": True, "same_source_and_node_ids": True},
                    preview=mask["preview"], document=mask["document"], revision=ctx["revision"])
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(path), "preview": report.get("preview")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    asyncio.run(run(parser.parse_args().project_root.resolve()))
