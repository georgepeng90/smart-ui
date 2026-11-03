from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


_program_path = Path(__file__).resolve().parents[1] / "examples" / "drawing-programs" / "slimeblock_buttons.py"
_program_spec = importlib.util.spec_from_file_location("slimeblock_buttons", _program_path)
_program = importlib.util.module_from_spec(_program_spec)
_program_spec.loader.exec_module(_program)


def build(component_id, spec, parameters):
    return _program.build(parameters, spec, component_id)


def paints(result):
    return {paint["node"]: [paint] for paint in result["paint"]}


def properties(result):
    return {node["key"]: node["properties"] for node in result["nodes"][1:]}


@pytest.fixture
def parameters():
    return {
        "brush": "b) Basic-5 Size",
        "palettes": {
            "green": {"face": "#67DB11", "lower_band": "#59C50E", "side": "#248200",
                      "outer_edge": "#173800", "inner_edge": "#B9FF5B", "gloss": "#F3FFD8", "shadow": "#102600"},
            "yellow": {"face": "#FFDB14", "lower_band": "#FFC311", "side": "#BB7800",
                       "outer_edge": "#673800", "inner_edge": "#FFF698", "gloss": "#FFFFD8", "shadow": "#3C2600"},
        },
        "geometry_profiles": {
            "standard": {"radius": 34, "outline": 3, "inset": 6, "depth": 12, "rim_width": 2,
                         "lower_band_ratio": 0.55, "gloss_width": 24, "gloss_height": 10,
                         "gloss_inset_x": 22, "gloss_inset_y": 18, "shadow_offset": 5, "shadow_opacity": 0.22},
            "compact": {"radius": 28, "outline": 2, "inset": 5, "depth": 10, "rim_width": 2,
                        "lower_band_ratio": 0.55, "gloss_width": 18, "gloss_height": 8,
                        "gloss_inset_x": 20, "gloss_inset_y": 16, "shadow_offset": 4, "shadow_opacity": 0.22},
        },
    }


@pytest.fixture
def spec():
    return {"program_id": "slimeblock_buttons", "width": 392, "height": 144, "palette": "green", "geometry": "standard", "scale": 2}


def without_colors(result):
    result = deepcopy(result)
    for paint in result["paint"]:
        del paint["color"]
    return result


def test_palette_variants_share_all_geometry(spec, parameters):
    green = build("primary", spec, parameters)
    yellow = build("primary", {**spec, "palette": "yellow"}, parameters)
    assert without_colors(green) == without_colors(yellow)
    for role in paints(green):
        assert paints(green)[role][0]["color"] != paints(yellow)[role][0]["color"]


def test_canvas_padding_layers_and_native_paint_contract(spec, parameters):
    result = build("arbitrary-master", spec, parameters)
    assert set(result) == {"width", "height", "nodes", "paint"}
    assert (result["width"], result["height"]) == (816, 320)
    assert len(result["nodes"]) == 8
    assert len(result["paint"]) == 7
    assert len({node["key"] for node in result["nodes"]}) == 8
    assert result["nodes"][0] == {"key": "component", "name": "arbitrary-master", "kind": "grouplayer", "parent": "root"}
    roles = ["shadow", "outline", "side", "rim", "face", "lower_band", "gloss"]
    assert [node["key"] for node in result["nodes"][1:]] == roles
    for node in result["nodes"][1:]:
        assert node["name"] == f"arbitrary-master/{node['key']}"
        assert node["kind"] == "paintlayer"
        assert node["parent"] == "component"
    assert properties(result) == {"shadow": {"opacity": 56}, "outline": {}, "side": {}, "rim": {}, "face": {}, "lower_band": {}, "gloss": {}}
    for role, role_paints in paints(result).items():
        assert role in roles
        for paint in role_paints:
            assert set(paint) == {"node", "brush", "color", "size", "opacity", "strokes"}
            assert paint["node"] == role
            assert paint["brush"] == parameters["brush"]
            for stroke in paint["strokes"]:
                assert stroke["kind"] in {"path", "ellipse"}
                assert stroke["fill"] == "foreground"
                if stroke["kind"] == "path":
                    assert stroke["commands"][-1] == ["Z"]
                    assert all(command[0] in {"M", "L", "C", "Q", "Z"} for command in stroke["commands"])
    assert len(paints(result)["gloss"][0]["strokes"]) == 4
    assert build("arbitrary-master", spec, parameters) == result


def test_width_change_keeps_corner_radius_and_brush_width(spec, parameters):
    narrow = build("narrow", spec, parameters)
    wide = build("wide", {**spec, "width": 560}, parameters)
    for role in paints(narrow):
        assert paints(narrow)[role][0]["size"] == paints(wide)[role][0]["size"]
    for role in ("outline", "side", "rim", "face"):
        a = paints(narrow)[role][0]["strokes"][0]["commands"]
        b = paints(wide)[role][0]["strokes"][0]["commands"]
        assert a[0] == b[0]
        assert a[2][1] - a[1][1] == b[2][1] - b[1][1]
        assert a[2][4] - a[1][2] == b[2][4] - b[1][2]
    assert wide["width"] - narrow["width"] == (560 - 392) * spec["scale"]


@pytest.mark.parametrize("height,ratio", [(144, 0.55), (144, 0.5), (136, 0.75)])
def test_lower_band_rounds_top_corners_and_preserves_face_bottom_corners(spec, parameters, height, ratio):
    profile = parameters["geometry_profiles"]["standard"]
    profile["lower_band_ratio"] = ratio
    result = build("rounded-band", {**spec, "height": height, "scale": 1}, parameters)
    face = paints(result)["face"][0]["strokes"][0]["commands"]
    band = paints(result)["lower_band"][0]["strokes"][0]["commands"]
    padding = 8
    x = y = padding + profile["inset"]
    width = spec["width"] - 2 * profile["inset"]
    face_height = height - profile["depth"] - 2 * profile["inset"]
    radius = profile["radius"] - profile["inset"]
    split = y + face_height * ratio
    top_radius = min(radius, face_height * (1 - ratio) - radius)
    assert band[0] == ["M", x + top_radius, split]
    assert band[1] == ["L", x + width - top_radius, split]
    assert band[2] == ["Q", x + width, split, x + width, split + top_radius]
    assert band[8] == ["Q", x, split, x + top_radius, split]
    assert band[4] == face[4]
    assert band[5] == face[5]
    assert band[6] == face[6]
    assert band[2][-1] <= band[3][-1]
    assert band[7][-1] <= band[6][-1]
    assert band[-1] == ["Z"]
    if height == 136:
        assert top_radius == 0
        assert band[2][1:3] == band[2][3:5]
        assert band[8][1:3] == band[8][3:5]
    else:
        assert top_radius > 0


def test_scale_changes_all_coordinates_and_brush_sizes(spec, parameters):
    base = build("base", {**spec, "scale": 1}, parameters)
    scaled = build("large", {**spec, "scale": 3}, parameters)
    assert scaled["width"] == base["width"] * 3
    assert scaled["height"] == base["height"] * 3
    assert properties(scaled) == properties(base)
    for role in paints(base):
        for a, b in zip(paints(base)[role], paints(scaled)[role], strict=True):
            assert b["size"] == a["size"] * 3
            for sa, sb in zip(a["strokes"], b["strokes"], strict=True):
                if sa["kind"] == "path":
                    for ca, cb in zip(sa["commands"], sb["commands"], strict=True):
                        assert cb == [ca[0], *[value * 3 for value in ca[1:]]]
                else:
                    assert sb["rect"] == [value * 3 for value in sa["rect"]]


def test_gloss_overrides_change_only_gloss(spec, parameters):
    original = build("primary", spec, parameters)
    changed = build("primary", {**spec, "overrides": {"gloss_color": "#AAFFCC", "gloss_opacity": 0.7, "gloss_scale": 0.8}}, parameters)
    assert changed["width"] == original["width"]
    assert changed["height"] == original["height"]
    assert properties(changed) == properties(original)
    for role in paints(original):
        if role != "gloss":
            assert paints(changed)[role] == paints(original)[role]
    gloss = paints(changed)["gloss"][0]
    assert gloss["color"] == "#AAFFCC"
    assert gloss["opacity"] == 0.7
    assert gloss["size"] == paints(original)["gloss"][0]["size"] * 0.8
    assert gloss["strokes"] != paints(original)["gloss"][0]["strokes"]


def test_compact_profile_is_available_without_altering_inputs(spec, parameters):
    before_spec, before_parameters = deepcopy(spec), deepcopy(parameters)
    standard = build("standard", spec, parameters)
    compact = build("compact", {**spec, "geometry": "compact"}, parameters)
    assert paints(compact)["face"] != paints(standard)["face"]
    assert spec == before_spec
    assert parameters == before_parameters


@pytest.mark.parametrize("field", ["width", "height", "palette", "geometry", "scale"])
def test_required_spec_fields_are_not_defaulted(spec, parameters, field):
    del spec[field]
    with pytest.raises(ValueError, match=field):
        build("missing", spec, parameters)


@pytest.mark.parametrize("field", ["brush", "palettes", "geometry_profiles"])
def test_required_parameter_fields_are_not_defaulted(spec, parameters, field):
    del parameters[field]
    with pytest.raises(ValueError, match=field):
        build("missing", spec, parameters)


def test_missing_palette_and_palette_color_fail(spec, parameters):
    with pytest.raises(ValueError, match="色板不存在"):
        build("missing", {**spec, "palette": "red"}, parameters)
    del parameters["palettes"]["green"]["face"]
    with pytest.raises(ValueError, match="palettes.green.*face"):
        build("missing", spec, parameters)


def test_missing_geometry_and_geometry_field_fail(spec, parameters):
    with pytest.raises(ValueError, match="几何配置不存在"):
        build("missing", {**spec, "geometry": "other"}, parameters)
    del parameters["geometry_profiles"]["standard"]["depth"]
    with pytest.raises(ValueError, match="geometry_profiles.standard.*depth"):
        build("missing", spec, parameters)


@pytest.mark.parametrize("field,value", [("width", None), ("width", True), ("height", 1.5), ("scale", 0), ("scale", float("nan")), ("scale", float("inf")), ("overrides", None)])
def test_invalid_spec_values_fail(spec, parameters, field, value):
    spec[field] = value
    with pytest.raises(ValueError, match=field):
        build("invalid", spec, parameters)


@pytest.mark.parametrize("field,value", [("radius", None), ("outline", 0), ("depth", -1), ("gloss_width", float("inf")), ("lower_band_ratio", 1), ("shadow_opacity", 1.1)])
def test_invalid_geometry_values_fail(spec, parameters, field, value):
    parameters["geometry_profiles"]["standard"][field] = value
    with pytest.raises(ValueError, match=field):
        build("invalid", spec, parameters)


@pytest.mark.parametrize("overrides", [{"gloss_color": None}, {"gloss_color": "red"}, {"gloss_opacity": None}, {"gloss_opacity": -0.1}, {"gloss_scale": 0}, {"gloss_scale": None}, {"grain": 1}])
def test_invalid_gloss_overrides_fail(spec, parameters, overrides):
    with pytest.raises(ValueError, match="overrides"):
        build("invalid", {**spec, "overrides": overrides}, parameters)


def test_small_components_and_oversized_highlights_fail_instead_of_clamping(spec, parameters):
    with pytest.raises(ValueError, match="尺寸过小"):
        build("too-small", {**spec, "height": 64}, parameters)
    with pytest.raises(ValueError, match="无法容纳高光"):
        build("too-bright", {**spec, "overrides": {"gloss_scale": 20}}, parameters)
    parameters["geometry_profiles"]["standard"]["gloss_inset_x"] = 0
    parameters["geometry_profiles"]["standard"]["gloss_inset_y"] = 0
    with pytest.raises(ValueError, match="高光"):
        build("corner", spec, parameters)


def test_unknown_fields_and_none_parameters_fail(spec, parameters):
    with pytest.raises(ValueError, match="不支持的字段.*typo"):
        build("typo", {**spec, "typo": 1}, parameters)
    with pytest.raises(ValueError, match="parameters 必须是对象"):
        build("none", spec, None)
    parameters["brush"] = None
    with pytest.raises(ValueError, match="parameters.brush"):
        build("none", spec, parameters)


def test_program_id_is_selected_by_the_project_not_the_drawing_program(spec, parameters):
    original = build("primary", spec, parameters)
    assert build("primary", {**spec, "program_id": "project-specific-name"}, parameters) == original
