"""通过真实 MCP 工具，在 SlimeBlock 参考上验收项目管理与原生组件制作。"""

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


async def run(project_root, reference_directory):
    evidence = project_root / ".smartui" / "integration"
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "calls": []}
    params = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"], cwd=str(ROOT))
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 15

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    entry = {"tool": name, "arguments": arguments, "is_error": bool(result.isError)}
                    report["calls"].append(entry)
                    if result.isError:
                        entry["error"] = result.content[0].text
                        raise RuntimeError(result.content[0].text)
                    value = json.loads(result.content[0].text)
                    entry["result"] = value
                    (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    return value

                common = {"project_root": str(project_root)}
                ctx = await call("project_create", {**common, "project_id": "slimeblock", "name": "SlimeBlock"})
                initial_revision = ctx["revision"]
                ctx = await call("project_save_program", {**common, "expected_revision": ctx["revision"],
                    "program_id": "slimeblock.buttons", "name": "SlimeBlock 亮面按钮", "description": "由项目维护的按钮绘制示例",
                    "source": (ROOT / "examples/drawing-programs/slimeblock_buttons.py").read_text(encoding="utf-8")})
                for ref_id, filename in [("hero_main", "SlimeBlock-英雄信息-主界面.png"),
                                         ("hero_attributes", "SlimeBlock-英雄信息-属性.png"),
                                         ("hero_list", "SlimeBlock-英雄列表.png")]:
                    ctx = await call("project_add_reference", {**common, "expected_revision": ctx["revision"],
                                     "reference_id": ref_id, "source_path": str(reference_directory / filename)})
                locations = {
                    "green_face": [299,1559,3,3], "green_lower_band": [299,1609,3,3], "green_side": [299,1654,3,3],
                    "green_outer_edge": [126,1580,1,1], "green_inner_edge": [300,1643,1,1], "green_gloss": [160,1548,1,1],
                    "yellow_face": [799,1539,3,3], "yellow_lower_band": [899,1619,3,3], "yellow_side": [749,1654,3,3],
                    "yellow_outer_edge": [561,1590,1,1], "yellow_inner_edge": [900,1643,1,1], "yellow_gloss": [597,1548,1,1],
                }
                samples = await call("project_inspect_reference", {**common, "reference_id": "hero_attributes", "coordinate_size": [1080,1920],
                                     "region": [120,1510,840,170], "patches": [{"id": key, "rect": rect} for key, rect in locations.items()]})
                (evidence / "attribute-samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
                red = await call("project_inspect_reference", {**common, "reference_id": "hero_list", "coordinate_size": [1080,1920],
                                 "region": [565,1120,200,84], "patches": [
                                     {"id":"red_face","rect":[650,1140,2,2]}, {"id":"red_lower_band","rect":[650,1180,2,2]},
                                     {"id":"red_side","rect":[650,1190,1,1]}, {"id":"red_outer_edge","rect":[571,1160,1,1]},
                                     {"id":"red_inner_edge","rect":[650,1133,1,1]}, {"id":"red_gloss","rect":[591,1141,1,1]}]})
                (evidence / "red-samples.json").write_text(json.dumps(red, ensure_ascii=False, indent=2), encoding="utf-8")
                colors = {item["id"]: item["hex"] for item in samples["samples"] + red["samples"]}
                print(json.dumps({"sampled_colors": colors}, ensure_ascii=False), flush=True)

                # 颜色来自工具对参考区域的实测；几何是模型提供的制作参数，未获用户美术确认。
                palettes = {}
                for theme in ("green", "yellow", "red"):
                    palettes[theme] = {role: colors[theme + "_" + role] for role in ("face", "lower_band", "side", "outer_edge", "inner_edge", "gloss")}
                    palettes[theme]["shadow"] = "#302820"
                geometry = {
                    "standard": {"radius":34,"outline":3,"inset":6,"depth":12,"rim_width":2,"lower_band_ratio":0.55,
                                 "gloss_width":24,"gloss_height":10,"gloss_inset_x":22,"gloss_inset_y":14,"shadow_offset":5,"shadow_opacity":0.22},
                    "compact": {"radius":28,"outline":2,"inset":5,"depth":10,"rim_width":2,"lower_band_ratio":0.5,
                                "gloss_width":15,"gloss_height":6,"gloss_inset_x":14,"gloss_inset_y":8,"shadow_offset":4,"shadow_opacity":0.22},
                }
                parameters = {"brush":"b) Basic-5 Size","palettes":palettes,"geometry_profiles":geometry}
                rules = [
                    {"id":"surface.bands","description":"正面主色、下半部较深色带和底部侧面分别绘制；参考色块由取色工具测量。","status":"observed",
                     "evidence":[{"reference_id":"hero_attributes","record":str(evidence/'attribute-samples.json')} ,{"reference_id":"hero_list","record":str(evidence/'red-samples.json')}]},
                    {"id":"highlight.layout","description":"左右亮斑与内侧亮边独立成层；当前光斑形状、边厚和圆角为初版制作估计，仍需视觉确认。","status":"hypothesis","evidence":[{"reference_id":"hero_attributes"}]},
                    {"id":"surface.texture","description":"参考主按钮没有明显颗粒，不添加粉笔纹理；几何填色与原生笔刷描边如实分开记录。","status":"observed","evidence":[{"reference_id":"hero_attributes"}]},
                ]
                ctx = await call("project_update_style", {**common,"expected_revision":ctx["revision"],
                                 "description":"从三张 SlimeBlock 参考中提取的卡通亮面按钮试制风格。色值来自源像素取样；投影色与几何参数为制作假设。原图没有 ICC 配置，暂按 sRGB 解释。技术检查通过不代表用户已确认美术效果。",
                                 "rules":rules,"parameters":parameters,"reference_ids":["hero_main","hero_attributes","hero_list"]})
                definitions = [
                    ("button.green.standard","标准绿色按钮",392,144,"green","standard"),
                    ("button.yellow.standard","标准黄色按钮",392,144,"yellow","standard"),
                    ("button.red.compact","紧凑红色按钮",184,72,"red","compact"),
                    ("button.green.wide","加宽绿色按钮",588,144,"green","standard"),
                ]
                outputs = []
                for component_id, name, width, height, palette, profile in definitions:
                    spec = {"program_id":"slimeblock.buttons","width":width,"height":height,"palette":palette,"geometry":profile,"scale":2}
                    ctx = await call("project_define_component", {**common,"expected_revision":ctx["revision"],"component_id":component_id,"name":name,"spec":spec})
                    output = await call("project_draw_component", {**common,"expected_revision":ctx["revision"],"component_id":component_id})
                    outputs.append(output)
                    ctx = await call("project_context", common)
                unaffected = {p[0]:hashlib.sha256(Path(ctx["components"][p[0]]["latest_run"]["document"]).read_bytes()).hexdigest() for p in definitions[1:]}
                spec = deepcopy(ctx["components"]["button.green.standard"]["spec"])
                spec["overrides"] = {"gloss_scale":0.8,"gloss_opacity":0.7}
                ctx = await call("project_define_component", {**common,"expected_revision":ctx["revision"],"component_id":"button.green.standard","name":"标准绿色按钮","spec":spec})
                edited = await call("project_draw_component", {**common,"expected_revision":ctx["revision"],"component_id":"button.green.standard","layers":["gloss"]})
                assert edited["verification"]["unchanged_other_nodes"]
                assert edited["verification"]["target_pixels_changed"]
                ctx = await call("project_context", common)
                for identifier,digest in unaffected.items():
                    assert hashlib.sha256(Path(ctx["components"][identifier]["latest_run"]["document"]).read_bytes()).hexdigest()==digest
                # 旧版本写入必须失败，且不得把项目内容改变。
                digest=hashlib.sha256((project_root/'.smartui'/'project.json').read_bytes()).hexdigest()
                rejected=await session.call_tool("project_update_style",{**common,"expected_revision":initial_revision,"description":"不应写入","rules":[],"parameters":{},"reference_ids":[]})
                assert rejected.isError
                assert hashlib.sha256((project_root/'.smartui'/'project.json').read_bytes()).hexdigest()==digest
                assert all(not value['needs_review'] for value in ctx['components'].values())
                for document in ('style.md','components.md'):
                    assert (project_root/'docs'/'ui'/document).is_file()
                report.update(status="passed",outputs=outputs,local_edit=edited,revision=ctx['revision'],
                              checks={"tool_count":15,"source_documents":4,"saved_and_reopened":True,"local_edit_kept_other_nodes":True,
                                      "other_assets_unchanged":True,"stale_revision_rejected":True,"documents_updated":True})
    except Exception as error:
        report.update(status="failed",error=str(error))
        raise
    finally:
        (evidence/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('端到端记录：'+str(evidence/'report.json'),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='创建一个新项目，使用SlimeBlock参考验证真实MCP和Krita流程；不覆盖已有项目。')
    parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--references',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(run(args.project_root.resolve(),args.references.resolve()))
