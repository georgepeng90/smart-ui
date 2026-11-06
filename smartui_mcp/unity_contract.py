"""Unity 控件目录与实例交付协议的结构和引用校验。"""

from fractions import Fraction
from graphlib import CycleError, TopologicalSorter
import math
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, StringConstraints, ValidationError
from pydantic_core import PydanticCustomError

from .projects import ProjectError, _identifier


SCHEMA_VERSION = 1


def _require_number(value):
    if type(value) not in (int, float):
        raise PydanticCustomError("float_type", "必须是数值")
    return value


def _require_parameter_value(value):
    if type(value) not in (str, int, float, bool):
        raise PydanticCustomError("parameter_scalar", "必须是字符串、有限数值或布尔值")
    if type(value) is float and not math.isfinite(value):
        raise PydanticCustomError("finite_number", "必须是有限数值")
    return value


def _require_integer_version(value):
    if type(value) is not int:
        raise ValueError(f"必须是整数 {SCHEMA_VERSION}")
    return value


Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Nonblank = Annotated[str, StringConstraints(pattern=r"\S")]
ProtocolVersion = Annotated[Literal[1], BeforeValidator(_require_integer_version)]
Positive = Annotated[float, Field(gt=0), BeforeValidator(_require_number)]
Nonnegative = Annotated[float, Field(ge=0), BeforeValidator(_require_number)]
ParameterValue = Annotated[str | int | float | bool, BeforeValidator(_require_parameter_value)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
UnityGuid = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
FileId = Annotated[int, Field(ge=-(2**63), lt=2**63)]
T = TypeVar("T")
Identified = Annotated[dict[Identifier, T], Field(json_schema_extra={"additionalProperties": False})]

_BINDABLE_SLOTS = {"content", "event", "data"}
_SOURCE_FIELDS = ("source_component_ids", "asset_ids", "text_style_ids")


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class ControlLayout(_ProtocolModel):
    min_width: Nonnegative
    min_height: Nonnegative
    max_width: Positive | None
    max_height: Positive | None


class ControlSlot(_ProtocolModel):
    kind: Literal["text", "image", "content", "event", "data"]
    component_type: Nonblank = Field(description="项目适配器识别的组件类型")
    required: bool
    description: str


class ControlParameter(_ProtocolModel):
    slot_id: Identifier
    property: Nonblank = Field(description="公开槽位允许实例覆盖的属性")
    value_type: Literal["string", "number", "boolean", "asset_id"]
    required: bool
    description: str


class ControlDefinition(_ProtocolModel):
    name: Nonblank
    description: str
    control_type: Nonblank = Field(description="项目适配器识别的控件类型")
    base_control_id: Identifier | None
    dependencies: list[Identifier] = Field(description="此控件嵌套使用的其他控件")
    source_component_ids: list[Identifier]
    asset_ids: list[Identifier]
    text_style_ids: list[Identifier]
    preview_asset_id: Identifier | None
    layout: ControlLayout
    slots: Identified[ControlSlot]
    parameters: Identified[ControlParameter]
    states: Identified[Identified[Identifier]] = Field(description="每个状态下图像槽位使用的资产")


class HandoffInstance(_ProtocolModel):
    id: Identifier
    control_id: Identifier
    x: Nonnegative
    y: Nonnegative
    width: Positive
    height: Positive
    parameters: Identified[ParameterValue] = Field(description="实例显式提供的设计参数")
    bindings: Identified[Nonblank] = Field(description="公开业务槽位对应的项目绑定键")


class HandoffPage(_ProtocolModel):
    id: Identifier
    name: Nonblank
    width: Positive
    height: Positive
    instances: list[HandoffInstance]


class HandoffRequest(_ProtocolModel):
    schema_version: ProtocolVersion
    adapter_id: Identifier
    adapter_version: Nonblank
    pages: list[HandoffPage]


class ManifestPrefab(_ProtocolModel):
    path: Nonblank = Field(description="预制体在项目内的相对路径")
    guid: UnityGuid
    sha256: Sha256
    meta_sha256: Sha256
    slot_bindings: Identified[FileId] = Field(description="槽位在当前预制体文件中的局部对象标识")
    source_guids: list[UnityGuid]


class SlotSource(_ProtocolModel):
    control_id: Identifier = Field(description="持有槽位对象的控件，可为当前控件或其基础控件")
    file_id: FileId = Field(description="槽位对象在来源预制体文件中的局部对象标识")


class ManifestControl(_ProtocolModel):
    definition: ControlDefinition = Field(description="已解析基础继承的控件定义")
    prefab: ManifestPrefab
    slot_sources: Identified[SlotSource] = Field(description="所有公开槽位解析后的对象来源")


class ManifestAsset(_ProtocolModel):
    path: Nonblank = Field(description="图像在项目内的相对路径")
    sha256: Sha256
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    geometry: dict[str, JsonValue] | None = Field(default=None, description="图像登记已有的伸缩几何，由图像来源校验")


class ManifestComponentInput(_ProtocolModel):
    spec_sha256: Sha256
    run: Identified[Sha256] = Field(description="当前制作产物字段与文件摘要")


class ManifestPage(HandoffPage):
    direct_sha256: Sha256 = Field(description="本页实例、显式参数及绑定等直接输入的摘要")
    dependency_sha256: Sha256 = Field(description="控件及资源传递依赖的摘要")


class HandoffManifest(_ProtocolModel):
    schema_version: ProtocolVersion
    kind: Literal["smartui_unity_handoff"]
    project_id: Identifier
    handoff_id: Identifier
    source_revision: Sha256
    adapter_id: Identifier
    adapter_version: Nonblank
    controls: Identified[ManifestControl]
    assets: Identified[ManifestAsset]
    text_styles: Identified[dict[str, JsonValue]] = Field(description="已解析文字角色，由项目文字规范校验")
    component_inputs: Identified[ManifestComponentInput]
    pages: list[ManifestPage]
    unity_validation: Literal["not_performed"]


def protocol_schema() -> dict:
    """返回控件定义、实例交付请求和交付清单的 JSON Schema。"""
    return {"schema_version": SCHEMA_VERSION, "control_definition": ControlDefinition.model_json_schema(),
            "handoff_request": HandoffRequest.model_json_schema(),
            "handoff_manifest": HandoffManifest.model_json_schema()}


def _schema_location(label, location, value):
    for item in location:
        if isinstance(value, list) and isinstance(item, int):
            value = value[item]
            identifier = value.get("id") if isinstance(value, dict) else None
            label += f"[{identifier!r}]" if isinstance(identifier, str) else f"[{item}]"
        elif item != "[key]":
            label += f".{item}"
            value = value.get(item) if isinstance(value, dict) else None
    return label


def parse_model(model, value, label):
    """校验协议结构并将错误定位到输入字段。"""
    try:
        return model.model_validate(value).model_dump(mode="json")
    except ValidationError as error:
        issue = error.errors(include_url=False, include_input=False)[0]
        location = issue["loc"]
        kind = issue["type"]
        if location == ("schema_version",):
            reason = f"必须是整数 {SCHEMA_VERSION}"
        elif kind == "string_pattern_mismatch":
            reason = {
                r"\S": "必须是非空字符串",
                r"^[0-9a-f]{32}$": "必须是 32 位小写十六进制 Unity GUID",
                r"^[0-9a-f]{64}$": "必须是 64 位小写十六进制 SHA-256 摘要",
            }.get(issue["ctx"]["pattern"], "必须是以字母或数字开头、仅包含字母、数字、点、下划线和连字符的标识")
        elif kind == "literal_error":
            reason = f"必须取以下值之一：{issue['ctx']['expected']}"
        elif kind == "greater_than":
            reason = f"必须大于 {issue['ctx']['gt']}"
        elif kind == "greater_than_equal":
            reason = f"必须大于等于 {issue['ctx']['ge']}"
        elif kind == "less_than":
            reason = f"必须小于 {issue['ctx']['lt']}"
        else:
            reason = {
                "extra_forbidden": "存在未定义的字段",
                "missing": "缺少必需字段",
                "string_type": "必须是字符串",
                "int_type": "必须是整数",
                "float_type": "必须是数值",
                "finite_number": "必须是有限数值",
                "bool_type": "必须是布尔值",
                "dict_type": "必须是对象",
                "model_type": "必须是对象",
                "list_type": "必须是数组",
                "parameter_scalar": "必须是字符串、有限数值或布尔值",
            }.get(kind, "值不符合协议")
        raise ProjectError(f"{_schema_location(label, location, value)} {reason}") from error


def _unique_ids(identifiers, label):
    seen = {}
    for identifier in identifiers:
        folded = identifier.casefold()
        if folded in seen:
            if seen[folded] == identifier:
                raise ProjectError(f"{label} 包含重复标识 {identifier}")
            raise ProjectError(f"{label} 包含仅大小写不同的标识 {seen[folded]} 和 {identifier}")
        seen[folded] = identifier


def _merge_interface(base, local, fields, label):
    for identifier in local:
        if identifier not in base:
            continue
        for field in fields:
            if base[identifier][field] != local[identifier][field]:
                raise ProjectError(f"{label}.{identifier} 不能改变基础控件公开约定的 {field}")
    return {**base, **local}


def _validate_control(identifier, definition, sources):
    label = f"控件 {identifier}"
    for field, references in sources.items():
        for reference in definition[field]:
            if reference not in references:
                raise ProjectError(f"{label}.{field} 引用的标识 {reference} 未登记")
    assets = sources["asset_ids"]
    preview = definition["preview_asset_id"]
    if preview is not None and preview not in assets:
        raise ProjectError(f"{label}.preview_asset_id 引用的资产 {preview} 未登记")

    layout = definition["layout"]
    for axis in ("width", "height"):
        maximum = layout[f"max_{axis}"]
        if maximum is not None and maximum < layout[f"min_{axis}"]:
            raise ProjectError(f"{label}.layout.max_{axis} 不能小于 min_{axis}")

    slots = definition["slots"]
    parameters = definition["parameters"]
    states = definition["states"]
    for field in ("slots", "parameters", "states"):
        _unique_ids(definition[field], f"{label}.{field}")
    targets = {}
    for parameter_id, parameter in parameters.items():
        slot_id = parameter["slot_id"]
        parameter_label = f"{label}.parameters.{parameter_id}"
        if slot_id not in slots:
            raise ProjectError(f"{parameter_label} 引用了未公开的槽位 {slot_id}")
        if slots[slot_id]["kind"] in _BINDABLE_SLOTS:
            raise ProjectError(f"{parameter_label} 必须指向文字或图像槽位，收到 {slot_id}")
        target = (slot_id, parameter["property"])
        if target in targets:
            raise ProjectError(f"{parameter_label} 与参数 {targets[target]} 指向同一个槽位属性")
        targets[target] = parameter_id

    for state_name, images in states.items():
        for slot_id, asset_id in images.items():
            state_label = f"{label}.states.{state_name}.{slot_id}"
            if slot_id not in slots or slots[slot_id]["kind"] != "image":
                raise ProjectError(f"{state_label} 必须指向公开的图像槽位")
            if asset_id not in assets:
                raise ProjectError(f"{state_label} 引用的资产 {asset_id} 未登记")


def validate_catalog(controls: dict, state: dict) -> dict:
    """校验当前目录，返回各控件继承解析后的定义。"""
    if not isinstance(controls, dict):
        raise ProjectError("Unity 控件目录必须是对象")
    for identifier in controls:
        _identifier(identifier, f"控件 ID {identifier!r}")
    _unique_ids(controls, "控件目录")
    definitions = {
        identifier: parse_model(ControlDefinition, value, f"控件 {identifier}")
        for identifier, value in controls.items()
    }
    graph = TopologicalSorter()
    for identifier, definition in definitions.items():
        base = definition["base_control_id"]
        dependencies = definition["dependencies"]
        for field in ("dependencies", *_SOURCE_FIELDS):
            _unique_ids(definition[field], f"控件 {identifier}.{field}")
        predecessors = dependencies if base is None else [base, *dependencies]
        for dependency in predecessors:
            if dependency not in definitions:
                raise ProjectError(f"控件 {identifier} 引用的控件 {dependency} 未登记")
        graph.add(identifier, *predecessors)
    try:
        order = list(graph.static_order())
    except CycleError as error:
        chain = " -> ".join(error.args[1])
        raise ProjectError(f"控件继承与嵌套依赖形成循环：{chain}") from error

    typography = state["typography"]
    sources = {"source_component_ids": state["components"], "asset_ids": state["images"],
               "text_style_ids": typography["styles"] if typography is not None else {}}
    ancestors = {}
    for identifier in order:
        definition = definitions[identifier]
        base_id = definition["base_control_id"]
        ancestors[identifier] = set()
        if base_id is not None:
            base = definitions[base_id]
            ancestors[identifier] = {base_id} | ancestors[base_id]
            conflicts = ancestors[identifier].intersection(definition["dependencies"])
            if conflicts:
                dependency = min(conflicts)
                raise ProjectError(f"控件 {identifier} 同时继承并嵌套控件 {dependency}")
            for field in ("dependencies", *_SOURCE_FIELDS):
                definition[field] = list(dict.fromkeys([*base[field], *definition[field]]))
            definition["slots"] = _merge_interface(
                base["slots"], definition["slots"], ("kind", "component_type", "required"),
                f"控件 {identifier}.slots",
            )
            definition["parameters"] = _merge_interface(
                base["parameters"], definition["parameters"], ("slot_id", "property", "value_type", "required"),
                f"控件 {identifier}.parameters",
            )
            local_states = definition["states"]
            definition["states"] = {**base["states"], **local_states}
            for name, images in local_states.items():
                definition["states"][name] = {**base["states"].get(name, {}), **images}
        _validate_control(identifier, definition, sources)
    return definitions


def _validate_instance(instance, page, control, assets, label):
    for axis, position in (("width", "x"), ("height", "y")):
        # 按 JSON 数值的十进制表示判定贴边，避免浮点求和把贴边的实例判为越界。
        edge = Fraction(str(instance[position])) + Fraction(str(instance[axis]))
        if edge > Fraction(str(page[axis])):
            raise ProjectError(f"{label} 的矩形超出页面范围（{position} + {axis}）")
        minimum = control["layout"][f"min_{axis}"]
        maximum = control["layout"][f"max_{axis}"]
        if instance[axis] < minimum:
            raise ProjectError(f"{label}.{axis} 小于控件约定的 min_{axis}（{minimum:g}）")
        if maximum is not None and instance[axis] > maximum:
            raise ProjectError(f"{label}.{axis} 大于控件约定的 max_{axis}（{maximum:g}）")

    parameters = instance["parameters"]
    parameter_types = {"string": (str,), "number": (int, float), "boolean": (bool,), "asset_id": (str,)}
    for parameter_id, value in parameters.items():
        parameter_label = f"{label}.parameters.{parameter_id}"
        if parameter_id not in control["parameters"]:
            raise ProjectError(f"{parameter_label} 未在控件中公开")
        value_type = control["parameters"][parameter_id]["value_type"]
        if type(value) not in parameter_types[value_type]:
            raise ProjectError(f"{parameter_label} 必须提供 {value_type} 类型的值")
        if value_type == "asset_id":
            _identifier(value, parameter_label)
            if value not in assets:
                raise ProjectError(f"{parameter_label} 引用的资产 {value} 未登记")
    for parameter_id, parameter in control["parameters"].items():
        if parameter["required"] and parameter_id not in parameters:
            raise ProjectError(f"{label}.parameters.{parameter_id} 缺少必需参数")

    slots = control["slots"]
    bindings = instance["bindings"]
    for slot_id in bindings:
        if slot_id not in slots or slots[slot_id]["kind"] not in _BINDABLE_SLOTS:
            raise ProjectError(f"{label}.bindings.{slot_id} 必须指向公开的内容、事件或数据槽位")
    for slot_id, slot in slots.items():
        if slot["required"] and slot["kind"] in _BINDABLE_SLOTS and slot_id not in bindings:
            raise ProjectError(f"{label}.bindings.{slot_id} 缺少必需绑定")


def validate_handoff(request: dict, effective_controls: dict, state: dict) -> dict:
    """校验各页面的控件实例，保留显式参数和绑定。"""
    result = parse_model(HandoffRequest, request, "Unity 交付请求")
    _unique_ids((page["id"] for page in result["pages"]), "交付页面")
    for page in result["pages"]:
        page_label = f"页面 {page['id']}"
        _unique_ids((instance["id"] for instance in page["instances"]), f"{page_label} 的控件实例")
        for instance in page["instances"]:
            label = f"{page_label} 的实例 {instance['id']}"
            control_id = instance["control_id"]
            if control_id not in effective_controls:
                raise ProjectError(f"{label} 引用的控件 {control_id} 未登记")
            _validate_instance(instance, page, effective_controls[control_id], state["images"], label)
    return result
