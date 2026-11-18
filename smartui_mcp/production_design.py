"""在项目设计分辨率下核对组件主体、制作外扩和图集区域。"""

from copy import deepcopy
import re


def _integers(value, fields, minimum):
    return all(type(value.get(key)) is int and value[key] >= minimum for key in fields)


def _validate_sprite(sprite, *, atlas):
    required = {"basis", "width", "height"} | ({"x", "y"} if atlas else set())
    if not isinstance(sprite, dict) or not required <= sprite.keys() <= required | {"outsets"}:
        raise ValueError(f"1× 图素规格必须包含 {sorted(required)}，可另填 outsets")
    basis = sprite["basis"]
    if isinstance(basis, dict):
        if (basis.keys() != {"layout_id", "node_id"} or
                any(not isinstance(key, str) or not key.strip() for key in basis.values())):
            raise ValueError("design.basis 的布局引用必须包含 layout_id 和 node_id")
    elif not isinstance(basis, str) or not basis.strip():
        raise ValueError("design.basis 必须引用布局节点，或写明控件规格或明确的设计决定")
    if not _integers(sprite, ("width", "height"), 1):
        raise ValueError("1× 主体 width、height 必须是正整数")
    if atlas and not _integers(sprite, ("x", "y"), 0):
        raise ValueError("图集裁区起点 x、y 必须是非负整数")
    if "outsets" in sprite:
        edges = sprite["outsets"]
        if (not isinstance(edges, dict) or edges.keys() != {"left", "right", "top", "bottom"}
                or not _integers(edges, edges, 0)):
            raise ValueError("design.outsets 必须包含四边非负整数 left、right、top、bottom")


def _layout_issue(state, sprite, reference):
    from .page_layouts import iter_layout_nodes

    basis = sprite["basis"]
    if not isinstance(basis, dict):
        return None
    page = state["layouts"].get(basis["layout_id"])
    location = f"布局 {basis['layout_id']} 的节点 {basis['node_id']}"
    if page is None:
        return f"规格依据不存在：{location}"
    definition = page["definition"]
    if any(reference[axis] != definition[axis] for axis in ("width", "height")):
        return f"{location} 所在画布为 {definition['width']}×{definition['height']}，项目设计基准为 {reference}"
    for node in iter_layout_nodes(definition["nodes"]):
        if node["id"] == basis["node_id"]:
            if not _integers(node, ("width", "height"), 1):
                return f"{location} 未指定固定宽高，请引用明确的 1× 基础尺寸"
            if (sprite["width"], sprite["height"]) != (node["width"], node["height"]):
                return (f"{location} 的 1× 尺寸为 {node['width']}×{node['height']}，"
                        f"组件设计规格为 {sprite['width']}×{sprite['height']}")
            return None
    return f"规格依据不存在：{location}"


def _validated_sprites(design, *, exported):
    if not isinstance(design, dict) or "reference_size" not in design:
        raise ValueError("缺少独立的 1× 设计规格")
    reference = design["reference_size"]
    if (not isinstance(reference, dict) or reference.keys() != {"width", "height"}
            or not _integers(reference, reference, 1)):
        raise ValueError("design.reference_size 必须包含正整数 width、height")
    if "sprites" not in design or exported:
        sprite = {key: value for key, value in design.items() if key != "reference_size"}
        _validate_sprite(sprite, atlas=exported and "x" in sprite)
        return (sprite,)
    sprites = design["sprites"]
    if design.keys() != {"reference_size", "sprites"} or not isinstance(sprites, dict) or not sprites:
        raise ValueError("图集 design.sprites 必须按正式资产 ID 登记每个图素的 1× 规格")
    for key, sprite in sprites.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", key):
            raise ValueError(f"design.sprites 含无效资产 ID：{key}")
        _validate_sprite(sprite, atlas=True)
    return sprites.values()


def design_issue(state, design, *, exported=False):
    try:
        sprites = _validated_sprites(design, exported=exported)
    except ValueError as error:
        return f"1× 设计规格待核对：{error}"
    reference = state.get("ui_settings", {}).get("reference_size")
    if design["reference_size"] != reference:
        return f"原设计基准 design.reference_size {design['reference_size']} 与当前 ui_settings.reference_size {reference} 不符"
    for sprite in sprites:
        problem = _layout_issue(state, sprite, reference)
        if problem:
            return problem
    return None


def bind_design(value, state):
    """保存规格时绑定当前项目的设计基准，显式拒绝沿用其他基准的规格。"""
    reference_size = state.get("ui_settings", {}).get("reference_size")
    if reference_size is None:
        raise ValueError("请先通过 project_update_ui_settings 确定 ui_settings.reference_size")
    if not isinstance(value, dict):
        raise ValueError("spec.design 必须是 1× 主体规格或包含 sprites 的图集规格")
    design = deepcopy(value)
    design.setdefault("reference_size", deepcopy(reference_size))
    problem = design_issue(state, design)
    if problem:
        raise ValueError(problem)
    return design


def current_design(state, component_id):
    design = state["components"][component_id]["spec"].get("design")
    problem = design_issue(state, design)
    if problem:
        raise ValueError(f"组件 {component_id}：{problem}，请通过 project_define_component 确定 spec.design")
    return design


def _rectangles(sprite):
    edges = sprite.get("outsets", {})
    left, top = edges.get("left", 0), edges.get("top", 0)
    content = {"x": left, "y": top, "width": sprite["width"], "height": sprite["height"]}
    region = {"x": sprite.get("x", 0), "y": sprite.get("y", 0),
              "width": left + content["width"] + edges.get("right", 0),
              "height": top + content["height"] + edges.get("bottom", 0)}
    return region, content


def check_artwork_size(design, size, component_id):
    if "sprites" in design:
        for asset_id, sprite in design["sprites"].items():
            region, _ = _rectangles(sprite)
            if region["x"] + region["width"] > size[0] or region["y"] + region["height"] > size[1]:
                raise ValueError(f"组件 {component_id} 的图集 {size} 无法容纳 {asset_id} 的 1× 裁区 {region}。"
                                 f"尺寸依据为 {sprite['basis']}")
    else:
        region, _ = _rectangles(design)
        expected = (region["width"], region["height"])
        if size != expected:
            raise ValueError(f"组件 {component_id} 的 1× 主体与外扩要求画布 {expected}，实际为 {size}。"
                             f"尺寸依据为 {design['basis']}")


def export_design(design, asset_id):
    if "sprites" not in design:
        return design
    if asset_id not in design["sprites"]:
        raise ValueError(f"资产 {asset_id} 缺少预先登记的图集规格 design.sprites")
    return {**design["sprites"][asset_id], "reference_size": design["reference_size"]}


def check_export_region(design, region, content, asset_id):
    try:
        _validated_sprites(design, exported=True)
    except ValueError as error:
        raise ValueError(f"资产 {asset_id} 的 provenance.design 无效：{error}") from error
    expected_region, expected_content = _rectangles(design)
    if region != expected_region or content != expected_content:
        raise ValueError(f"资产 {asset_id} 的制作结果与 1× 设计规格不一致。"
                         f"预计 region={expected_region}、content_rect={expected_content}，"
                         f"实际 region={region}、content_rect={content}。尺寸依据为 {design['basis']}")


def asset_design_issue(state, asset):
    provenance = asset["provenance"]
    if provenance["kind"] != "component_export":
        return None
    design = provenance.get("design")
    if design is None:
        return (f"缺少已核验的 1× 设计规格。已有裁区为 {provenance['region']}，"
                f"PNG 为 {asset['width']}×{asset['height']}，请登记 spec.design 后重新绘制并导出")
    problem = design_issue(state, design, exported=True)
    return f"{problem}，请核对 1× 规格，重新绘制并导出" if problem else None
