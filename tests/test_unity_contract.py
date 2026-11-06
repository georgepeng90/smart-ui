"""校验 Unity 目录继承和交付请求中的实例约定。"""

from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from smartui_mcp.projects import ProjectError
from smartui_mcp.unity_contract import (
    HandoffManifest, SlotSource, parse_model, protocol_schema, validate_catalog, validate_handoff,
)


def definition(**changes):
    return {
        "name": "操作按钮",
        "description": "",
        "control_type": "button",
        "base_control_id": None,
        "dependencies": [],
        "source_component_ids": ["button-art"],
        "asset_ids": ["button-face"],
        "text_style_ids": ["button-caption"],
        "preview_asset_id": None,
        "layout": {"min_width": 12, "min_height": 8, "max_width": 36, "max_height": None},
        "slots": {},
        "parameters": {},
        "states": {},
        **changes,
    }


def slot(kind, component_type, required=False):
    return {"kind": kind, "component_type": component_type, "required": required, "description": ""}


def parameter(slot_id, property_name, value_type, required=False):
    return {"slot_id": slot_id, "property": property_name, "value_type": value_type,
            "required": required, "description": ""}


@pytest.fixture
def state():
    return {
        "components": {"button-art": {}, "pressed-art": {}},
        "images": {"button-face": {}, "pressed-face": {}, "alternate-face": {}},
        "typography": {"styles": {"button-caption": {}}},
    }


@pytest.fixture
def button():
    return definition(
        slots={"label": slot("text", "TMP_Text"), "face": slot("image", "Image"),
               "icon": slot("image", "Image"), "click": slot("event", "UnityEvent", required=True)},
        parameters={"caption": parameter("label", "text", "string", required=True),
                    "size": parameter("label", "fontSize", "number"),
                    "icon": parameter("icon", "sprite", "asset_id"),
                    "enabled": parameter("face", "enabled", "boolean")},
        states={"normal": {"face": "button-face", "icon": "alternate-face"}},
    )


def test_variant_merges_only_inherited_contract_fields(state, button):
    button["dependencies"] = ["badge"]
    child = definition(
        base_control_id="button",
        dependencies=["decoration", "badge"],
        source_component_ids=["pressed-art"],
        asset_ids=["pressed-face"],
        text_style_ids=[],
        slots={"label": {**button["slots"]["label"], "description": "变体文案"}},
        parameters={"caption": {**button["parameters"]["caption"], "description": "变体按钮文字"}},
        states={"normal": {"face": "pressed-face"}, "selected": {"face": "alternate-face"}},
    )
    controls = {
        "variant": child,
        "button": button,
        "badge": definition(slots={"badge-only": slot("image", "Image")}),
        "decoration": definition(),
    }
    originals = deepcopy((controls, state))
    effective = validate_catalog(controls, state)
    variant = effective["variant"]
    assert variant["base_control_id"] == "button"
    assert set(variant["dependencies"]) == {"badge", "decoration"}
    assert len(variant["dependencies"]) == 2
    assert set(variant["source_component_ids"]) == {"button-art", "pressed-art"}
    assert set(variant["asset_ids"]) == {"button-face", "pressed-face"}
    assert variant["text_style_ids"] == ["button-caption"]
    assert variant["slots"] == {**button["slots"], "label": child["slots"]["label"]}
    assert variant["parameters"] == {**button["parameters"], "caption": child["parameters"]["caption"]}
    assert variant["states"] == {"normal": {"face": "pressed-face", "icon": "alternate-face"},
                                 "selected": {"face": "alternate-face"}}
    assert effective["button"]["states"] == button["states"]
    assert (controls, state) == originals


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"slots": {"label": slot("image", "Image")}}, r"variant.*label"),
        ({"slots": {"label": slot("text", "TMP_Text", required=True)}}, r"variant.*label"),
        ({"slots": {"Label": slot("text", "TMP_Text")}}, r"variant.*[Ll]abel"),
        ({"parameters": {"caption": parameter("label", "value", "string", required=True)}},
         r"variant.*caption"),
        ({"parameters": {"missing": parameter("missing", "text", "string")}}, r"variant.*missing"),
        ({"states": {"normal": {"label": "button-face"}}}, r"variant.*normal.*label"),
        ({"states": {"normal": {"face": "absent"}}}, r"variant.*normal.*absent"),
        ({"layout": {"min_width": 50, "min_height": 8, "max_width": 36, "max_height": None}},
         r"variant.*max_width"),
        ({"dependencies": ["button"]}, r"variant.*button"),
        ({"dependencies": ["absent"]}, r"variant.*absent"),
        ({"source_component_ids": ["absent"]}, r"variant.*source_component_ids.*absent"),
        ({"asset_ids": ["absent"]}, r"variant.*asset_ids.*absent"),
        ({"text_style_ids": ["absent"]}, r"variant.*text_style_ids.*absent"),
        ({"preview_asset_id": "absent"}, r"variant.*preview_asset_id.*absent"),
        ({"prefab": {"path": "Assets/Button.prefab"}}, r"variant.*prefab"),
    ],
)
def test_invalid_catalog_contract_identifies_the_control(state, button, changes, message):
    with pytest.raises(ProjectError, match=message):
        validate_catalog({"button": button, "variant": definition(base_control_id="button", **changes)}, state)


def test_catalog_rejects_mixed_inheritance_and_dependency_cycles(state):
    with pytest.raises(ProjectError, match=r"循环.*first.*second.*first"):
        validate_catalog({
            "first": definition(base_control_id="second"),
            "second": definition(dependencies=["first"]),
        }, state)


def test_catalog_control_id_is_unique_ignoring_case(state, button):
    with pytest.raises(ProjectError, match=r"大小写.*button.*Button"):
        validate_catalog({"button": button, "Button": button}, state)


def instance(**changes):
    return {"id": "play", "control_id": "button", "x": 10, "y": 4, "width": 20, "height": 8,
            "parameters": {"caption": "开始"}, "bindings": {"click": "Game.Start"}, **changes}


def page(page_id="home", **changes):
    return {"id": page_id, "name": "主页", "width": 64, "height": 48, "instances": [instance()], **changes}


def request(*pages, **changes):
    return {"schema_version": 1, "adapter_id": "ui-adapter", "adapter_version": "1.0",
            "pages": list(pages), **changes}


def test_handoff_preserves_explicit_values_and_keeps_other_parameters_inherited(state, button):
    handoff = request(page(instances=[instance(), instance(
        id="continue", parameters={"caption": "", "size": 20.5, "enabled": False, "icon": "alternate-face"},
    )]), page("settings"))
    original = deepcopy(handoff)
    result = validate_handoff(handoff, validate_catalog({"button": button}, state), state)
    assert result == original
    assert list(result["pages"][0]["instances"][0]["parameters"]) == ["caption"]
    assert result["pages"][0]["instances"][1]["parameters"]["enabled"] is False
    assert handoff == original


def test_handoff_accepts_fractional_coordinates_on_the_page_edge(state, button):
    button["layout"]["min_width"] = 0
    controls = validate_catalog({"button": button}, state)
    handoff = request(page(width=0.3, instances=[instance(x=0.1, width=0.2)]))
    assert validate_handoff(handoff, controls, state) == handoff
    handoff["pages"][0]["instances"][0]["width"] = 0.20000000000000004
    with pytest.raises(ProjectError, match=r"home.*play.*页面范围"):
        validate_handoff(handoff, controls, state)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"control_id": "absent"}, r"home.*play.*absent"),
        ({"x": 55}, r"home.*play.*页面范围"),
        ({"height": 7}, r"home.*play.*min_height"),
        ({"width": 37}, r"home.*play.*max_width"),
        ({"width": True}, r"home.*play.*width"),
        ({"width": Decimal("20")}, r"home.*play.*width.*必须是数值"),
        ({"width": float("nan")}, r"home.*play.*width"),
        ({"parameters": {}}, r"home.*play.*caption"),
        ({"parameters": {"caption": "", "size": False}}, r"home.*play.*size"),
        ({"parameters": {"caption": "", "size": float("inf")}}, r"home.*play.*size 必须是有限数值"),
        ({"parameters": {"caption": "", "size": Decimal("20")}}, r"home.*play.*size 必须是字符串"),
        ({"parameters": {"caption": "", "enabled": 1}}, r"home.*play.*enabled"),
        ({"parameters": {"caption": "", "icon": "absent"}}, r"home.*play.*icon.*absent"),
        ({"parameters": {"caption": "", "unpublished": 1}}, r"home.*play.*unpublished"),
        ({"bindings": {}}, r"home.*play.*click"),
        ({"bindings": {"click": "   "}}, r"home.*play.*click"),
        ({"bindings": {"click": "Game.Start", "label": "Some.Key"}}, r"home.*play.*label"),
    ],
)
def test_handoff_error_identifies_page_instance_and_invalid_field(state, button, changes, message):
    with pytest.raises(ProjectError, match=message):
        validate_handoff(request(page(instances=[instance(**changes)])),
                         validate_catalog({"button": button}, state), state)


@pytest.mark.parametrize("handoff, message", [
    (request(page(), page("Home")), r"大小写.*home.*Home"),
    (request(page(instances=[instance(), instance(id="Play")])), r"home.*大小写.*play.*Play"),
    (request(page(), schema_version=True), r"schema_version"),
    (request(page(), schema_version=2), r"schema_version"),
])
def test_handoff_rejects_identity_conflicts_and_invalid_protocol_version(state, button, handoff, message):
    with pytest.raises(ProjectError, match=message):
        validate_handoff(handoff, validate_catalog({"button": button}, state), state)


def test_protocol_schema_exposes_required_fields_and_version():
    schema = protocol_schema()
    assert schema["schema_version"] == 1
    for key in ("handoff_request", "handoff_manifest"):
        assert schema[key]["properties"]["schema_version"]["const"] == 1
    for key in ("control_definition", "handoff_request", "handoff_manifest"):
        model = schema[key]
        assert model["additionalProperties"] is False
        assert set(model["required"]) == set(model["properties"])
    assert schema["control_definition"]["properties"]["slots"]["additionalProperties"] is False
    assert schema["handoff_manifest"]["properties"]["unity_validation"]["const"] == "not_performed"
    assert "slot_sources" in schema["handoff_manifest"]["$defs"]["ManifestControl"]["required"]


@pytest.fixture
def manifest(state, button):
    digest = "a" * 64
    controls = validate_catalog({"button": button}, state)
    handoff = validate_handoff(request(page()), controls, state)
    handoff["pages"][0].update(direct_sha256=digest, dependency_sha256=digest)
    slot_ids = {slot_id: i for i, slot_id in enumerate(button["slots"], start=1)}
    return {
        **handoff,
        "kind": "smartui_unity_handoff",
        "project_id": "sample",
        "handoff_id": "release",
        "source_revision": digest,
        "controls": {
            "button": {
                "definition": controls["button"],
                "prefab": {
                    "path": "Assets/Prefabs/Button.prefab", "guid": "b" * 32, "sha256": digest,
                    "meta_sha256": digest, "slot_bindings": slot_ids,
                    "source_guids": [],
                },
                "slot_sources": {slot_id: {"control_id": "button", "file_id": file_id}
                                 for slot_id, file_id in slot_ids.items()},
            },
        },
        "assets": {"button-face": {"path": "smart-ui/assets/button.png", "sha256": digest,
                                   "width": 16, "height": 12}},
        "text_styles": {"button-caption": {"font_size": 24}},
        "component_inputs": {"button-art": {"spec_sha256": digest, "run": {"preview": digest}}},
        "unity_validation": "not_performed",
    }


def test_manifest_accepts_handoff_output_and_preserves_omitted_geometry(manifest):
    result = HandoffManifest.model_validate(manifest)
    assert result.model_dump(mode="json", exclude_unset=True) == manifest


@pytest.mark.parametrize("changes, field", [
    ({"kind": "smartui_unity_result"}, "kind"),
    ({"schema_version": True}, "schema_version"),
    ({"source_revision": "sha256"}, "source_revision"),
    ({"unity_validation": "verified"}, "unity_validation"),
    ({"extra": True}, "extra"),
])
def test_manifest_rejects_invalid_or_extra_protocol_fields(manifest, changes, field):
    manifest.update(changes)
    with pytest.raises(ValidationError) as error:
        HandoffManifest.model_validate(manifest)
    assert error.value.errors()[0]["loc"] == (field,)


def test_manifest_rejects_invalid_page_signature_and_prefab_object_id(manifest):
    manifest["pages"][0]["dependency_sha256"] = "wrong"
    manifest["controls"]["button"]["prefab"]["slot_bindings"]["label"] = True
    with pytest.raises(ValidationError) as error:
        HandoffManifest.model_validate(manifest)
    assert {issue["loc"] for issue in error.value.errors()} == {
        ("pages", 0, "dependency_sha256"),
        ("controls", "button", "prefab", "slot_bindings", "label"),
    }


def test_manifest_requires_sources_for_prefab_slots(manifest):
    del manifest["controls"]["button"]["slot_sources"]
    with pytest.raises(ValidationError) as error:
        HandoffManifest.model_validate(manifest)
    assert error.value.errors()[0]["loc"] == ("controls", "button", "slot_sources")


@pytest.mark.parametrize("file_id, message", [
    (True, "必须是整数"),
    (2**63, "必须小于"),
    (-(2**63) - 1, "必须大于等于"),
])
def test_parse_slot_source_identifies_invalid_file_id(file_id, message):
    with pytest.raises(ProjectError, match=rf"槽位 label\.file_id {message}"):
        parse_model(SlotSource, {"control_id": "base-button", "file_id": file_id}, "槽位 label")
