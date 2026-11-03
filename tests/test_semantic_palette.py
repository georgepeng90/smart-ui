"""通过项目程序预检验证示例配色、状态与绘制计划。"""

import importlib.util
from pathlib import Path

import pytest

from smartui_mcp.programs import ProgramError, run_program


_program_path = Path(__file__).resolve().parents[1] / "examples" / "drawing-programs" / "semantic_palette.py"
_module_spec = importlib.util.spec_from_file_location("semantic_palette", _program_path)
_program = importlib.util.module_from_spec(_module_spec)
_module_spec.loader.exec_module(_program)
_source = _program_path.read_text(encoding="utf-8")


@pytest.fixture
def parameters():
    return _program.example_parameters("b) Basic-5 Size")


def plan(parameters, **changes):
    spec = {"program_id": "semantic-palette", "theme": "lagoon",
            "state": "available", "width": 180, "height": 52}
    return run_program(_source, parameters, spec | changes, "action")


def paint_by_node(result):
    return {step["node"]: step for step in result["paint"]}


@pytest.mark.parametrize("state", ["available", "selected", "disabled"])
def test_two_themes_use_their_own_roles_and_share_geometry(parameters, state):
    lagoon = plan(parameters, state=state)
    ember = plan(parameters, theme="ember", state=state)
    state_roles = parameters["states"][state]
    for theme, result in (("lagoon", lagoon), ("ember", ember)):
        roles = {"outline": "outline", **state_roles}
        assert {step["node"]: step.pop("color") for step in result["paint"]} == {
            node: parameters["themes"][theme][role] for node, role in roles.items()
        }
        assert all(step["brush"] == parameters["brush"] for step in result["paint"])
    assert lagoon == ember


def test_states_keep_shared_art_and_apply_only_their_declared_differences(parameters):
    available = plan(parameters)
    selected = plan(parameters, state="selected")
    disabled = plan(parameters, state="disabled")
    assert available["nodes"] == selected["nodes"] == disabled["nodes"]
    available_paint = paint_by_node(available)
    selected_paint = paint_by_node(selected)
    disabled_paint = paint_by_node(disabled)
    assert selected_paint.pop("marker")["color"] == parameters["themes"]["lagoon"]["selection"]
    assert available_paint == selected_paint
    assert disabled_paint == available_paint | {
        "surface": available_paint["surface"] | {"color": parameters["themes"]["lagoon"]["inactive"]},
    }


def test_project_can_choose_its_own_state_names_and_share_a_changed_role(parameters):
    parameters["states"] = {"claimable": {"surface": "action"},
                            "claimed": {"surface": "inactive"}}
    before = plan(parameters, state="claimable")
    claimed = plan(parameters, state="claimed")
    parameters["themes"]["lagoon"]["action"] = "#408B70"
    after = plan(parameters, state="claimable")
    assert paint_by_node(after)["surface"]["color"] == "#408B70"
    assert paint_by_node(after)["outline"] == paint_by_node(before)["outline"]
    assert plan(parameters, state="claimed") == claimed


@pytest.mark.parametrize("path,state", [
    (("brush",), "available"),
    (("themes", "lagoon"), "available"),
    (("states", "selected"), "selected"),
    (("states", "selected", "surface"), "selected"),
    (("themes", "lagoon", "selection"), "selected"),
])
def test_missing_required_configuration_reports_its_path(parameters, path, state):
    container = parameters
    for segment in path[:-1]:
        container = container[segment]
    del container[path[-1]]
    with pytest.raises(ProgramError, match=r"缺少配置项 parameters\." + r"\.".join(path)):
        plan(parameters, state=state)
