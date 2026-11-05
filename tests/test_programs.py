"""验证 PhotoCraft 计划预检与可信 Python 程序隔离执行。"""
from copy import deepcopy
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from smartui_mcp.programs import ProgramError, run_program, validate_plan, validate_source

@pytest.fixture
def plan():
    return {"schema_version": 1, "width": 160, "height": 96, "nodes": [
        {"key": "group", "name": "组件", "kind": "group", "parent": "root"},
        {"key": "base", "name": "底板", "kind": "shape", "parent": "group", "shape": {
            "kind": "roundedRect", "rect": [1, 2, 150, 80], "radii": [6, 8, 10, 12],
            "fill": {"gradient": {"stops": [[0, "#00112200"], [1, "#aabbccff"]]}},
            "stroke": {"width": 2, "color": "#000000"}},
         "effects": [{"kind": "outerGlow", "params": {"size": 2, "opacity": 12}}]},
        {"key": "ink", "name": "笔触", "kind": "pixel", "parent": "group", "strokes": [
            {"color": "#123abc", "size": 3, "opacity": .8, "points": [[1, 2, .5], [30, 20, 1]]}]}]}

def test_normalization_is_independent_and_idempotent(plan):
    original = deepcopy(plan)
    result = validate_plan(plan)
    assert original == plan
    assert validate_plan(result) == result
    assert result["nodes"][0]["properties"] == {"visible": True, "opacity": 1, "blend": "PassThrough"}
    assert result["nodes"][1]["shape"]["fill"]["gradient"]["dither"] is False
    assert result["nodes"][2]["strokes"][0]["brush"]["pressureSize"] is False
    assert result["nodes"][2]["strokes"][0]["seed"] == 0

@pytest.mark.parametrize("path,value", [
    (("schema_version",), 2), (("schema_version",), True), (("width",), 0), (("height",), 16385),
    (("paint",), []), (("nodes",), []), (("nodes", 1, "kind"), "paintlayer"),
    (("nodes", 1, "parent"), "ink"), (("nodes", 1, "key"), "group"),
    (("nodes", 1, "key"), "root"), (("nodes", 1, "shape", "filll"), "#ffffff"),
    (("nodes", 1, "shape", "fill", "gradient", "style"), "typo"),
    (("nodes", 1, "shape", "fill", "gradient", "stops"), [[0, "#fff"], [1, "#000000"]]),
    (("nodes", 1, "shape", "fill", "gradient", "stops"), [[1, "#ffffff"], [0, "#000000"]]),
    (("nodes", 1, "shape", "stroke", "width"), -1),
    (("nodes", 1, "effects", 0, "params", "size"), -1),
    (("nodes", 1, "effects", 0, "params", "szie"), 2),
    (("nodes", 2, "strokes", 0, "size"), .01),
    (("nodes", 2, "strokes", 0, "opacity"), 100),
    (("nodes", 2, "strokes", 0, "points"), [[1, 2, 2]]),
    (("nodes", 2, "strokes", 0, "brush"), {"preset": "Hard Round"}),
    (("nodes", 1, "properties"), {"blend": "PassThrough"}),
    (("nodes", 1, "properties"), {"visible": 1}),
])
def test_invalid_plans_are_rejected_before_backend(plan, path, value):
    target = plan
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ProgramError):
        validate_plan(plan)

@pytest.mark.parametrize("value", [float("nan"), float("inf"), (1, 2), {1: "x"}, object()])
def test_non_json_is_rejected(plan, value):
    plan["nodes"][1]["shape"]["fill"] = value
    with pytest.raises(ProgramError, match="标准 JSON"):
        validate_plan(plan)

@pytest.mark.parametrize("kind,geometry", [("rect", {"rect": [0, 0, 20, 10]}),
    ("ellipse", {"rect": [0, 0, 20, 10]}), ("polygon", {"rect": [0, 0, 20, 10], "sides": 3}),
    ("star", {"rect": [0, 0, 20, 10]}), ("line", {"from": [0, 0], "to": [20, 10]}),
    ("path", {"path": {"subpaths": [{"knots": [[0, 0], {"anchor": [2, 3], "in": [1, 3]}, [4, 0]]}]}})])
def test_supported_native_shapes(plan, kind, geometry):
    plan["nodes"][1]["shape"] = {"kind": kind, **geometry}
    normalized = validate_plan(plan)
    assert validate_plan(normalized) == normalized
    if kind == "path":
        assert normalized["nodes"][1]["shape"]["path"]["subpaths"][0]["knots"][1]["out"] == [2, 3]

def test_group_vector_mask_and_shape_boundary(plan):
    mask = {"path": {"subpaths": [{"knots": [[0, 0], [100, 0], [100, 90]]}]}}
    plan["nodes"][0]["mask"] = mask
    assert validate_plan(plan)["nodes"][0]["mask"]["density"] == 100
    plan["nodes"][1]["mask"] = mask
    with pytest.raises(ProgramError, match="父 group"):
        validate_plan(plan)


EFFECT_COLOR_FIELDS = [
    ("dropShadow", "color"), ("innerShadow", "color"), ("outerGlow", "color"),
    ("innerGlow", "color"), ("stroke", "color"), ("colorOverlay", "color"),
    ("gradientOverlay", "from"), ("gradientOverlay", "to"), ("satin", "color"),
]


@pytest.mark.parametrize("kind,field", EFFECT_COLOR_FIELDS)
@pytest.mark.parametrize("color", ["#ff000000", "#ff000080", [1, 0, 0, 0], [1, 0, 0, .5]])
def test_effect_colors_reject_alpha_ignored_by_native_backend(plan, kind, field, color):
    plan["nodes"][1]["effects"] = [{"kind": kind, "params": {field: color}}]
    with pytest.raises(ProgramError, match=rf"plan\.nodes\[1\]\.effects\[0\]\.params\.{field}.*opacity"):
        validate_plan(plan)


@pytest.mark.parametrize("kind,field", EFFECT_COLOR_FIELDS)
@pytest.mark.parametrize("color", ["#ff0000", "#ff0000FF", [1, 0, 0, 1]])
def test_effect_colors_accept_opaque_values(plan, kind, field, color):
    plan["nodes"][1]["effects"] = [{"kind": kind, "params": {field: color, "opacity": 40}}]
    effect = validate_plan(plan)["nodes"][1]["effects"][0]
    assert effect["params"][field] == (color.lower() if type(color) is str else color)
    assert effect["params"]["opacity"] == 40


@pytest.mark.parametrize("color", ["#ff000080", [1, 0, 0, .5]])
def test_shape_and_pixel_colors_keep_supported_alpha(plan, color):
    plan["nodes"][1]["shape"]["fill"] = color
    plan["nodes"][1]["shape"]["stroke"]["color"] = color
    plan["nodes"][2]["strokes"][0]["color"] = color
    normalized = validate_plan(plan)
    assert normalized["nodes"][1]["shape"]["fill"] == color
    assert normalized["nodes"][1]["shape"]["stroke"]["color"] == color
    assert normalized["nodes"][2]["strokes"][0]["color"] == color


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
    return {"schema_version": 1, "width": math.ceil(spec["width"]), "height": parameters["height"],
            "nodes": [{"key": "content", "name": component_id, "kind": "pixel", "parent": "root", "strokes": []}]}
'''
    result = run_program(source, {"parent_pid": os.getpid(), "height": 28}, {"width": 20.5}, "component-1")
    assert (result["width"], result["height"]) == (21, 28)
    assert result["nodes"][0]["name"] == "component-1"

@pytest.mark.parametrize("source,reason", [
    ("build = 1", "必须定义 build"),
    ("def build(a, b, c):\n    raise RuntimeError('项目几何错误')", "项目几何错误"),
    ("def build(a, b, c):\n    return (1, 2)", "标准 JSON"),
    ("def build(a, b, c):\n    return {'width': float('nan')}", "标准 JSON"),
    ("def build(a, b, c):\n    return {}", "缺少"), ("raise SystemExit(0)", "SystemExit")])
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
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("输入无效时不得启动子进程"))
    with pytest.raises(ProgramError):
        run_program("def build(a, b, c): pass", parameters, spec, component_id)
