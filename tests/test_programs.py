"""验证项目程序执行及 Krita 调用前的计划检查，不依赖 Krita 安装。"""

from copy import deepcopy
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from smartui_mcp.programs import ProgramError, run_program, validate_plan, validate_source


@pytest.fixture
def plan():
    return {
        "width": 160, "height": 96,
        "nodes": [
            {"key": "group", "name": "组件", "kind": "grouplayer", "parent": "root"},
            {"key": "ink", "name": "线稿", "kind": "paintlayer", "parent": "group"},
            {"key": "mask", "name": "裁剪", "kind": "transparencymask", "parent": "ink",
             "rects": [[-1, 0, 100, 80, 255]]},
        ],
        "paint": [{"node": "ink", "brush": "任意原生笔刷", "color": "#123abc", "size": 3,
                   "strokes": [
                       {"kind": "line", "from": [-2, 1], "to": [40, 2]},
                       {"kind": "rectangle", "rect": [0.5, 1.5, 30, 20]},
                       {"kind": "ellipse", "rect": [2, 3, 10, 12], "fill": "foreground"},
                       {"kind": "path", "commands": [["M", 1.5, 2], ["L", 3, 4],
                           ["C", 1, 2, 3, 4, 5, 6], ["Q", 1, 2, 3, 4], ["Z"]]},
                   ]}],
    }


def test_valid_plan_is_independent_and_defaults_follow_bridge(plan):
    original = deepcopy(plan)
    result = validate_plan(plan)
    assert plan == original
    assert result["nodes"][0]["properties"] == {"visible": True, "locked": False,
        "opacity": 255, "blending_mode": "normal", "inherit_alpha": False}
    assert result["nodes"][1]["properties"]["alpha_locked"] is False
    assert "alpha_locked" not in result["nodes"][2]["properties"]
    assert result["paint"][0]["opacity"] == 1.0
    assert result["paint"][0]["strokes"][0]["pressure_from"] == 1.0
    assert result["paint"][0]["strokes"][0]["pressure_to"] == 1.0
    assert result["paint"][0]["strokes"][1]["fill"] == "none"
    assert result["paint"][0]["strokes"][3]["fill"] == "none"
    assert validate_plan(result) == result


def test_optional_empty_content_and_final_layer_properties(plan):
    plan["nodes"][0]["properties"] = {"locked": True, "visible": False, "opacity": 0,
        "blending_mode": "multiply", "inherit_alpha": True}
    plan["nodes"][1]["properties"] = {"alpha_locked": True}
    plan["nodes"][2]["rects"] = []
    plan["paint"] = []
    result = validate_plan(plan)
    assert result["nodes"][0]["properties"] == plan["nodes"][0]["properties"]
    assert result["nodes"][1]["properties"]["alpha_locked"] is True
    assert result["nodes"][2]["rects"] == []
    assert validate_plan({"width": 1, "height": 16384, "nodes": [], "paint": []})


@pytest.mark.parametrize("path,value,reason", [
    (("width",), True, "width"),
    (("height",), 1.5, "height"),
    (("width",), 0, "width"),
    (("height",), 16385, "height"),
    (("unexpected",), 1, "未知"),
    (("nodes",), {}, "nodes"),
    (("nodes", 1, "key"), "group", "重复"),
    (("nodes", 1, "key"), "root", "保留"),
    (("nodes", 1, "key"), "bad key", "标识"),
    (("nodes", 1, "name"), "组件", "name 重复"),
    (("nodes", 1, "name"), " ", "非空"),
    (("nodes", 0, "parent"), "ink", "此前声明"),
    (("nodes", 1, "parent"), "mask", "此前声明"),
    (("nodes", 2, "parent"), "root", "蒙版必须"),
    (("nodes", 1, "kind"), "vectorlayer", "kind"),
    (("nodes", 1, "rects"), [], "只适用"),
    (("nodes", 1, "properties"), {"name": "新名称"}, "未知"),
    (("nodes", 0, "properties"), {"alpha_locked": False}, "只适用"),
    (("nodes", 1, "properties"), {"locked": 1}, "布尔值"),
    (("nodes", 1, "properties"), {"opacity": 1.5}, "整数"),
    (("nodes", 1, "properties"), {"opacity": 256}, "范围"),
    (("nodes", 1, "properties"), {"blending_mode": "unknown"}, "blending_mode"),
    (("nodes", 2, "rects"), [[0, 0, 0, 1, 255]], "width"),
    (("nodes", 2, "rects"), [[0, 0, 1, 1, 256]], "alpha"),
    (("nodes", 2, "rects"), [[0, 0, 1, 1.5, 255]], "整数"),
    (("paint",), {}, "paint"),
    (("paint", 0, "node"), "mask", "paintlayer"),
    (("paint", 0, "node"), "missing", "paintlayer"),
    (("paint", 0, "node"), [], "paintlayer"),
    (("paint", 0, "brush"), "", "非空"),
    (("paint", 0, "color"), "red", "RRGGBB"),
    (("paint", 0, "size"), 0.001, "size"),
    (("paint", 0, "size"), 10001, "size"),
    (("paint", 0, "opacity"), 1.1, "opacity"),
    (("paint", 0, "strokes"), [], "非空数组"),
    (("paint", 0, "strokes", 0, "from"), [0.5, 0], "整数"),
    (("paint", 0, "strokes", 0, "pressure_from"), -0.1, "pressure_from"),
    (("paint", 0, "strokes", 0, "fill"), "none", "未知"),
    (("paint", 0, "strokes", 1, "rect"), [0, 0, 0, 1], "rect"),
    (("paint", 0, "strokes", 1, "fill"), "background", "fill"),
    (("paint", 0, "strokes", 1, "kind"), "unsupported", "kind"),
    (("paint", 0, "strokes", 3, "commands"), [["L", 0, 0]], "M 命令"),
    (("paint", 0, "strokes", 3, "commands"), [["M", 0, 0], ["C", 1]], "6 个"),
    (("paint", 0, "strokes", 3, "commands"), [["M", 0, 0], [[], 1]], "无效"),
    (("paint", 0, "strokes", 3, "commands"), [["M", 0, 0], ["A", 1]], "无效"),
])
def test_invalid_plan_fails_before_krita(plan, path, value, reason):
    target = plan
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ProgramError, match=reason):
        validate_plan(plan)


def test_nodes_cannot_parent_under_paint_or_mask(plan):
    for parent in ("ink", "mask"):
        invalid = deepcopy(plan)
        invalid["nodes"].append({"key": "child", "name": "子节点", "kind": "paintlayer", "parent": parent})
        with pytest.raises(ProgramError, match="父节点必须是分组"):
            validate_plan(invalid)
    plan["nodes"].append({"key": "child", "name": "嵌套蒙版", "kind": "transparencymask",
                          "parent": "mask", "rects": []})
    with pytest.raises(ProgramError, match="蒙版必须"):
        validate_plan(plan)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), (1, 2), {1: "value"}, object()])
def test_reject_non_json_values(plan, value):
    plan["paint"][0]["size"] = value
    with pytest.raises(ProgramError, match="标准 JSON"):
        validate_plan(plan)


def test_required_fields_are_checked(plan):
    del plan["nodes"][2]["rects"]
    with pytest.raises(ProgramError, match="rects"):
        validate_plan(plan)
    del plan["height"]
    with pytest.raises(ProgramError, match="缺少.*height"):
        validate_plan(plan)


def test_validate_source_does_not_execute(tmp_path):
    marker = tmp_path / "not-created"
    validate_source(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    assert not marker.exists()
    with pytest.raises(ProgramError, match="语法错误"):
        validate_source("def build(:")


def test_execute_project_code_with_stdlib_and_clean_stdout():
    source = '''
import math
import os
print("项目初始化输出")
def build(parameters, spec, component_id):
    print("绘制输出", component_id)
    assert os.getpid() != parameters["parent_pid"]
    assert component_id == "component-1"
    return {"width": math.ceil(spec["width"]), "height": parameters["height"],
            "nodes": [{"key": "content", "name": component_id, "kind": "paintlayer", "parent": "root"}],
            "paint": []}
'''
    import os
    result = run_program(source, {"parent_pid": os.getpid(), "height": 28}, {"width": 20.5}, "component-1")
    assert result["width"] == 21
    assert result["height"] == 28
    assert result["nodes"][0]["name"] == "component-1"


@pytest.mark.parametrize("source,reason", [
    ("build = 1", "必须定义 build"),
    ("def build(a, b, c):\n    raise RuntimeError('项目几何错误')", "项目几何错误"),
    ("def build(a, b, c):\n    return (1, 2)", "标准 JSON"),
    ("def build(a, b, c):\n    return {'width': float('nan')}", "标准 JSON"),
    ("def build(a, b, c):\n    return {}", "缺少"),
    ("raise SystemExit(0)", "SystemExit"),
])
def test_program_failures_preserve_reason(source, reason):
    with pytest.raises(ProgramError, match=reason):
        run_program(source, {}, {}, "component")


def test_runner_uses_isolated_current_interpreter_and_fixed_timeout(monkeypatch, plan):
    def execute(command, **kwargs):
        assert command[:5] == [sys.executable, "-I", "-X", "utf8", "-c"]
        assert len(command) == 6
        assert kwargs["timeout"] == 60
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["capture_output"] is True
        assert json.loads(kwargs["input"])["source"] == "def build(a, b, c): pass"
        return SimpleNamespace(returncode=0, stdout=json.dumps(plan), stderr="")
    monkeypatch.setattr(subprocess, "run", execute)
    assert run_program("def build(a, b, c): pass", {}, {}, "component") == validate_plan(plan)


def test_timeout_is_reported_without_retry(monkeypatch):
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(ProgramError, match="超过 60 秒"):
        run_program("def build(a, b, c): pass", {}, {}, "component")
    assert len(calls) == 1


@pytest.mark.parametrize("parameters,spec,component_id", [([], {}, "id"), ({}, [], "id"),
    ({"x": float("nan")}, {}, "id"), ({}, {}, "bad id")])
def test_bad_inputs_do_not_start_subprocess(monkeypatch, parameters, spec, component_id):
    def execute(*args, **kwargs):
        pytest.fail("输入无效时不得启动子进程")
    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(ProgramError):
        run_program("def build(a, b, c): pass", parameters, spec, component_id)
