"""验证示例当前要求：颜色用途共享、状态差异及缺失配置报错。"""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest
from smartui_mcp.programs import ProgramError, run_program

_path = Path(__file__).resolve().parents[1] / "examples/drawing-programs/semantic_palette.py"
_spec = importlib.util.spec_from_file_location("semantic_palette", _path)
_program = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_program)
_source = _path.read_text(encoding="utf-8")


def plan(parameters, **changes):
    spec = {"program_id": "semantic-palette", "theme": "lagoon", "state": "available", "width": 180, "height": 52}
    return run_program(_source, parameters, spec | changes, "action")


@pytest.mark.parametrize("state", ["available", "selected", "disabled"])
def test_themes_change_colors_and_preserve_geometry(state):
    parameters = _program.example_parameters()
    a, b = (plan(parameters, theme=theme, state=state) for theme in ("lagoon", "ember"))
    for result, theme in ((a, "lagoon"), (b, "ember")):
        roles = parameters["states"][state]
        assert result["nodes"][0]["shape"].pop("fill") == parameters["themes"][theme][roles["surface"]].lower()
        assert result["nodes"][0]["shape"]["stroke"].pop("color") == parameters["themes"][theme]["outline"].lower()
        result["nodes"][1]["shape"].pop("fill")
    assert a == b


def test_states_only_change_declared_colors_and_marker_visibility():
    parameters = _program.example_parameters()
    available = plan(parameters)
    selected = plan(parameters, state="selected")
    disabled = plan(parameters, state="disabled")
    assert available["nodes"][0] == selected["nodes"][0]
    assert selected["nodes"][1]["properties"]["visible"] is True
    assert available["nodes"][1]["properties"]["visible"] is False
    disabled["nodes"][0]["shape"]["fill"] = available["nodes"][0]["shape"]["fill"]
    assert disabled == available


def test_shared_role_change_does_not_change_unrelated_state():
    parameters = _program.example_parameters()
    disabled = plan(parameters, state="disabled")
    available = plan(parameters)
    parameters["themes"]["lagoon"]["action"] = "#408B70"
    changed = plan(parameters)
    assert changed["nodes"][0]["shape"]["fill"] == "#408b70"
    changed["nodes"][0]["shape"]["fill"] = available["nodes"][0]["shape"]["fill"]
    assert changed == available
    assert plan(parameters, state="disabled") == disabled


@pytest.mark.parametrize("path,state", [
    (("themes", "lagoon"), "available"),
    (("states", "selected"), "selected"),
    (("states", "selected", "surface"), "selected"),
    (("themes", "lagoon", "selection"), "selected"),
])
def test_missing_configuration_reports_its_path(path, state):
    parameters = deepcopy(_program.example_parameters())
    container = parameters
    for key in path[:-1]:
        container = container[key]
    del container[path[-1]]
    with pytest.raises(ProgramError, match=r"缺少配置项 parameters\." + r"\.".join(path)):
        plan(parameters, state=state)
