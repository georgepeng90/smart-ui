"""从制作定义生成 Unity CLI 请求，核对执行产物并维护当前登记。"""

from copy import deepcopy
from graphlib import TopologicalSorter
import json
import re
from uuid import uuid4

from .layout import ProjectLayout
from .projects import ProjectError, _identifier, _json, _sha, spec_sha256
from .unity import UnityStore, _asset_ids, _contract, _fields
from .unity_cli import UnityCliClient, UnityCliError
from .unity_contract import ControlDefinition, parse_model
from .unity_prefabs import _is_file_id, inspect_prefab


CONSTRUCTION = "smart-ui/unity/construction.json"
OPERATIONS = "smart-ui/unity/operations"
_RESULT_TARGET_STATES = {
    "plan": {"create", "update", "verify", "keep", "conflict"},
    "apply": {"created", "updated", "verified", "unchanged", "failed"},
    "verify": {"verified", "failed"},
}


def _bytes(value):
    return (_json(value) + "\n").encode("utf-8")


def _path(value, label, *, suffix=None):
    normalized = ProjectLayout._relative_directory(value, label)
    if normalized != value or not normalized.startswith("Assets/") or (suffix and not value.endswith(suffix)):
        raise ProjectError(f"{label} 必须是 Assets/ 下使用 / 的规范相对路径" + (f"，后缀为 {suffix}" if suffix else ""))
    return value


def _config(value):
    _fields(value, {"output_root", "fonts"}, "Unity 构建配置")
    _path(value["output_root"], "output_root")
    if not isinstance(value["fonts"], dict):
        raise ProjectError("Unity fonts 必须是按文字角色 ID 组织的对象")
    for key, font in value["fonts"].items():
        _identifier(key, "Unity 字体角色")
        _fields(font, {"font_asset_path", "font_asset_guid", "material_path", "material_guid"}, f"Unity 字体 {key}")
        for prefix in ("font_asset", "material"):
            _path(font[prefix + "_path"], f"Unity 字体 {key}.{prefix}_path")
            if not isinstance(font[prefix + "_guid"], str) or not re.fullmatch(r"[0-9a-f]{32}", font[prefix + "_guid"]):
                raise ProjectError(f"Unity 字体 {key}.{prefix}_guid 必须是 32 位小写 GUID")
    return deepcopy(value)


def read_construction(root):
    """只读取构建资料，不递归打开 ProjectStore 或控件目录。"""
    path = ProjectLayout(root).resolve(CONSTRUCTION)
    if not path.exists():
        return {"schema_version": 1, "config": None, "recipes": {}, "bindings": {}}, None
    try:
        value = json.loads(path.read_bytes())
        _fields(value, {"schema_version", "config", "recipes", "bindings"}, CONSTRUCTION)
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ProjectError(f"{CONSTRUCTION} 只接受 schema_version=1")
        if value["config"] is not None:
            _config(value["config"])
        for field in ("recipes", "bindings"):
            if not isinstance(value[field], dict):
                raise ProjectError(f"{CONSTRUCTION}.{field} 必须是对象")
            for key in value[field]:
                _identifier(key, field)
        return value, spec_sha256(value)
    except (OSError, ValueError, TypeError) as error:
        raise ProjectError(f"无法读取 {CONSTRUCTION}：{error}") from error


def _file_hash(layout, path):
    file = layout.resolve(path)
    return _sha(file.read_bytes()) if file.is_file() else None


def _inspect_result_prefab(root, request, registry, target, item, sources):
    path = item["path"]
    local = item["slot_bindings"]
    if not isinstance(local, dict):
        raise ProjectError(f"Unity 产物 {path} 的 slot_bindings 必须是对象")
    bindings = dict(local)
    expected = {node["id"]: node["control_id"] for node in target["nodes"] if node["kind"] == "control"}
    instances = item["instances"]
    if not isinstance(instances, dict) or instances.keys() != expected.keys():
        raise ProjectError(f"Unity 产物 {path} 的嵌套实例 ID 与请求不一致")
    scope_owners = {}
    index = 0
    for instance_id, slots in instances.items():
        control_id = expected[instance_id]
        if not isinstance(slots, dict) or slots.keys() != request["controls"][control_id]["slots"].keys():
            raise ProjectError(f"Unity 产物 {path} 的实例 {instance_id} 槽位与请求不一致")
        if not slots:
            continue
        registered = registry["controls"][control_id]["prefab"]["slot_sources"]
        scope = None
        for slot_id, entry in slots.items():
            label = f"Unity 产物 {path} 的实例 {instance_id}/{slot_id}"
            _fields(entry, {"scope_file_id", "control_id", "guid", "file_id"}, label)
            for field in ("scope_file_id", "file_id"):
                if not _is_file_id(entry[field]) or entry[field] == 0:
                    raise ProjectError(f"{label} 的 {field} 必须是非零有符号 64 位整数")
            source = registered[slot_id]
            source_file = registry["controls"][source["control_id"]]["prefab"]["file"]
            if (entry["control_id"], entry["guid"], entry["file_id"]) != (
                source["control_id"], source_file["guid"], source["file_id"],
            ):
                raise ProjectError(f"{label} 的来源 GUID、fileID 或控件 ID 与登记不一致")
            if scope is not None and scope != entry["scope_file_id"]:
                raise ProjectError(f"{label} 的实例容器 fileID 与该实例的其他槽位不一致")
            scope = entry["scope_file_id"]
        owner = scope_owners.setdefault(scope, instance_id)
        if owner != instance_id:
            raise ProjectError(f"Unity 产物 {path} 的实例 {instance_id} 与另一实例 {owner} 共用容器 fileID {scope}")
        key = f"instance-scope-{index}"
        while key in bindings:
            index += 1
            key = f"instance-scope-{index}"
        bindings[key] = scope
        index += 1
    file = inspect_prefab(root, path, item["guid"], bindings, sources)
    file["slot_bindings"] = dict(local)
    return file


def _flat_nodes(state, definition):
    from .page_layouts import iter_layout_nodes, resolve_definition
    identifiers = set()
    for node in iter_layout_nodes(definition["nodes"]):
        identifier = node["id"].casefold()
        if identifier == "root":
            raise ProjectError(f"Unity 节点 {node['id']} 使用了保留 ID root（不区分大小写）")
        if identifier in identifiers:
            raise ProjectError(f"Unity 节点 ID {node['id']} 重复（不区分大小写）")
        identifiers.add(identifier)
        if node.get("overflow") in ("shrink", "clip") or "max_lines" in node or node.get("layout", {}).get("align") == "baseline":
            raise ProjectError(f"Unity 静态构建暂不支持节点 {node['id']} 的裁切、缩字、max_lines 或基线排列")
    resolved = resolve_definition(state, definition, include_layout_tree=True)
    if resolved.get("baseline_groups"):
        raise ProjectError("Unity 静态构建暂不支持基线排列")
    result = []
    adaptation = resolved.get("unity_adaptation", {})

    def visit(nodes, parent=None, x=0, y=0):
        for node in nodes:
            item = {key: deepcopy(node[key]) for key in (
                "id", "kind", "width", "height", "asset_id", "text", "text_style_id", "align", "wrap", "overflow",
                "control_id", "parameters") if key in node}
            item.update(parent_id=parent, x=node["x"] - x, y=node["y"] - y)
            for field in ("anchor_min", "anchor_max", "pivot"):
                if field in adaptation.get(node["id"], {}):
                    item[field] = adaptation[node["id"]][field]
            result.append(item)
            if node["kind"] == "group":
                visit(node["children"], node["id"], node["x"], node["y"])

    visit(resolved["layout_tree"])
    return result


class UnityBuildStore:
    def __init__(self, unity=None, cli=None):
        self.unity = unity if unity is not None else UnityStore()
        self.projects = self.unity.projects
        self.cli = cli if cli is not None else UnityCliClient()

    def _read(self, root):
        from .unity_recipes import resolve_recipes
        source, source_revision, registry, registry_revision = self.unity._read(root)
        construction, revision = read_construction(root)
        controls = resolve_recipes({key: item["definition"] for key, item in registry["controls"].items()},
                                   construction["recipes"], source)
        source["unity_controls"] = controls
        return source, source_revision, registry, registry_revision, construction, revision

    @staticmethod
    def _revision(expected, revision):
        if expected != revision or (expected is not None and not isinstance(expected, str)):
            raise ProjectError("Unity 构建资料 revision 冲突，请重新读取；首次使用 null")

    def _save(self, root, construction):
        self.projects._publish_batch(ProjectLayout(root), {root / CONSTRUCTION: _bytes(construction)})
        return {"revision": spec_sha256(construction), "construction": deepcopy(construction)}

    def context(self, project_root):
        root = self.projects._root(project_root)
        with self.projects._lock(root):
            source, source_revision, _, registry_revision, construction, revision = self._read(root)
            operations = []
            directory = ProjectLayout(root).resolve(OPERATIONS)
            if directory.exists():
                for path in sorted(directory.glob("*/operation.json")):
                    _, operation, _ = self._operation(root, path.parent.name)
                    operations.append({key: operation[key] for key in ("request_id", "status", "operation", "error")})
            return {"revision": revision, "source_revision": source_revision, "registry_revision": registry_revision,
                    "construction": construction, "controls": source["unity_controls"], "operations": operations,
                    "validation": "editor_execution_required"}

    def configure(self, project_root, expected_revision, config):
        root = self.projects._root(project_root)
        config = _config(config)
        with self.projects._lock(root):
            *_, construction, revision = self._read(root)
            self._revision(expected_revision, revision)
            if construction["config"] is not None and construction["config"]["output_root"] != config["output_root"]:
                if ProjectLayout(root).resolve("smart-ui/unity/import-state").exists():
                    raise ProjectError("已有 Unity 生成状态，不能通过配置迁移 output_root")
            construction["config"] = config
            return self._save(root, construction)

    def define_recipe(self, project_root, expected_revision, control_id, recipe,
                      definition=None, expected_registry_revision=None):
        from .unity_recipes import parse_recipe, resolve_recipes
        from .page_layouts import resolve_definition
        root = self.projects._root(project_root)
        _identifier(control_id, "control_id")
        recipe = parse_recipe(recipe)
        if definition is not None:
            definition = parse_model(ControlDefinition, definition, f"控件 {control_id}")
        elif expected_registry_revision is not None:
            raise ProjectError("仅同时提供公开控件 definition 时才可指定 expected_registry_revision")
        with self.projects._lock(root):
            source, source_revision, registry, registry_revision, construction, revision = self._read(root)
            self._revision(expected_revision, revision)
            if definition is not None:
                self.unity._revision(expected_registry_revision, registry_revision)
                self.projects._check_id_collision(registry["controls"], control_id, "control_id")
                previous = registry["controls"].get(control_id)
                registry["controls"][control_id] = {"definition": definition,
                    "prefab": previous["prefab"] if previous is not None else None}
            elif control_id not in registry["controls"]:
                raise ProjectError(f"请先定义公开控件：{control_id}")
            construction["recipes"][control_id] = recipe
            source["unity_controls"] = resolve_recipes(
                {key: item["definition"] for key, item in registry["controls"].items()}, construction["recipes"], source)
            for key, page in source["layouts"].items():
                try:
                    resolve_definition(source, page["definition"])
                except ValueError as error:
                    raise ProjectError(f"控件修改使页面 {key} 无效：{error}") from error
            if definition is not None:
                registered = self.unity._write(root, source, source_revision, registry,
                                               {root / CONSTRUCTION: _bytes(construction)})
                return {"revision": spec_sha256(construction), "construction": deepcopy(construction),
                        "registry_revision": registered["revision"]}
            return self._save(root, construction)

    def set_bindings(self, project_root, expected_revision, page_id, bindings):
        root = self.projects._root(project_root)
        _identifier(page_id, "page_id")
        with self.projects._lock(root):
            source, _, registry, _, construction, revision = self._read(root)
            self._revision(expected_revision, revision)
            self._bindings(source, registry, page_id, bindings)
            construction["bindings"][page_id] = deepcopy(bindings)
            return self._save(root, construction)

    def _bindings(self, source, registry, page_id, bindings, *, require_complete=False):
        from .page_layouts import iter_layout_nodes
        if page_id not in source["layouts"]:
            raise ProjectError(f"正式页面不存在：{page_id}")
        if not isinstance(bindings, dict):
            raise ProjectError("页面 bindings 必须是按实例 ID 组织的对象")
        effective = self.unity._effective(registry, source)
        instances = {node["id"]: node for node in iter_layout_nodes(source["layouts"][page_id]["definition"]["nodes"])
                     if node["kind"] == "control"}
        if bindings.keys() - instances.keys():
            raise ProjectError(f"页面 {page_id} bindings 引用了不存在的控件实例")
        for instance, values in bindings.items():
            slots = effective[instances[instance]["control_id"]]["slots"]
            if not isinstance(values, dict) or values.keys() - slots.keys():
                raise ProjectError(f"页面 {page_id}/{instance} bindings 引用了不存在的公开槽位")
            for slot, value in values.items():
                if slots[slot]["kind"] not in ("content", "event", "data") or not isinstance(value, str) or not value.strip():
                    raise ProjectError(f"页面 {page_id}/{instance}/{slot} 必须是业务槽位的非空绑定键")
        if require_complete:
            for instance, node in instances.items():
                for slot, definition in effective[node["control_id"]]["slots"].items():
                    if (definition["kind"] in ("content", "event", "data") and definition["required"]
                            and slot not in bindings.get(instance, {})):
                        raise ProjectError(f"页面 {page_id}/{instance}/{slot} 缺少必需业务绑定")
        return deepcopy(bindings)

    def prepare(self, project_root, expected_revision, control_ids, page_ids, operation="plan"):
        root = self.projects._root(project_root)
        if operation not in ("plan", "apply", "verify"):
            raise ProjectError("operation 必须是 plan、apply 或 verify")
        for values, label in ((control_ids, "control_ids"), (page_ids, "page_ids")):
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values) or len(set(values)) != len(values):
                raise ProjectError(f"{label} 必须是无重复 ID 的列表")
            for value in values:
                _identifier(value, label)
        if not control_ids and not page_ids:
            raise ProjectError("至少选择一个控件或正式页面")
        if control_ids and page_ids:
            raise ProjectError("请分开准备控件和页面请求，先构建并登记控件，再准备页面")
        with self.projects._lock(root):
            source, source_revision, registry, registry_revision, construction, revision = self._read(root)
            self._revision(expected_revision, revision)
            self._ensure_idle(root)
            request_id = uuid4().hex
            request = self._request(root, source, registry, construction, control_ids, page_ids, operation, request_id)
            request_path = f"{OPERATIONS}/{request_id}/request.json"
            record = {"schema_version": 1, "request_id": request_id, "operation": operation, "status": "prepared",
                      "request_sha256": _sha(_bytes(request)), "source_revision": source_revision,
                      "registry_revision": registry_revision, "construction_revision": revision,
                      "bindings": {key: deepcopy(construction["bindings"].get(key, {})) for key in page_ids},
                      "error": None, "result_sha256": None}
            self.projects._publish_batch(ProjectLayout(root), {
                root / request_path: _bytes(request), root / OPERATIONS / request_id / "operation.json": _bytes(record)})
            return {"request_id": request_id, "request_path": str(root / request_path), "request": request,
                    "status": "prepared", "unity_execution": "not_performed"}

    def _request(self, root, source, registry, construction, control_ids, page_ids, operation, request_id,
                 *, check_dependency_inputs=True, written_controls=()):
        config = construction["config"]
        if config is None:
            raise ProjectError("请先配置 Unity output_root 和文字角色对应的 TMP 字体资源")
        layout = ProjectLayout(root)
        effective = self.unity._effective(registry, source)
        recipes, resolved = construction["recipes"], source["unity_controls"]
        for key in control_ids:
            if key not in recipes:
                raise ProjectError(f"控件 {key} 尚无制作定义")
            if recipes[key]["kind"] == "external":
                raise ProjectError(f"外部控件 {key} 只登记和引用，不由构建器改写")
        page_nodes, selected = {}, set(control_ids)
        for page_id in page_ids:
            self._bindings(source, registry, page_id, construction["bindings"].get(page_id, {}), require_complete=True)
            page_nodes[page_id] = _flat_nodes(source, source["layouts"][page_id]["definition"])
            selected.update(node["control_id"] for node in page_nodes[page_id] if node["kind"] == "control")
        graph = {}

        def visit(key):
            if key in graph:
                return
            if key not in recipes:
                raise ProjectError(f"引用控件 {key} 缺少制作或外部预览定义")
            definition = registry["controls"][key]["definition"]
            dependencies = list(definition["dependencies"])
            if definition["base_control_id"] is not None:
                dependencies.append(definition["base_control_id"])
            graph[key] = dependencies
            for dependency in dependencies:
                visit(dependency)

        for key in sorted(selected):
            visit(key)
        ordered = list(TopologicalSorter(graph).static_order())
        closure = {}
        for key in ordered:
            closure[key] = {key}
            for dependency in graph[key]:
                closure[key].update(closure[dependency])
        # 先构建并登记控件，再单独构建页面；不让页面引用尚未核对的对象身份。
        issues = self.unity._prefab_issues(root, registry, effective, written_controls=written_controls)
        for key in ordered:
            if key not in control_ids and key in issues:
                raise ProjectError(f"请先构建或核对依赖控件 {key}：{issues[key]['message']}")
        dependencies = [key for key in ordered if key not in control_ids and recipes[key]["kind"] != "external"]
        if check_dependency_inputs and dependencies:
            current = self._request(root, source, registry, construction, dependencies, [], "plan", request_id,
                                     check_dependency_inputs=False, written_controls=written_controls)
            for target in current["targets"]:
                key, kind = target["id"], target["kind"]
                baseline_path = layout.resolve(f"smart-ui/unity/import-state/{kind}/{key}.json")
                try:
                    baseline = json.loads(baseline_path.read_bytes())
                except (OSError, ValueError) as error:
                    raise ProjectError(f"请先构建依赖控件 {key}，无法读取当前构建基线：{baseline_path}") from error
                if (not isinstance(baseline, dict) or baseline.get("project_id") != source["project_id"]
                        or baseline.get("path") != target["path"]
                        or baseline.get("guid") != registry["controls"][key]["prefab"]["file"]["guid"]
                        or baseline.get("direct_sha256") != target["direct_sha256"]):
                    raise ProjectError(f"请先构建依赖控件 {key}，当前制作输入与构建基线不一致")
        controls, targets = {}, []
        extra_assets, extra_styles = set(), set()
        direct_resources = {}
        for key in ordered:
            item, recipe = resolved[key], recipes[key]
            prefab = registry["controls"][key]["prefab"]
            output = f"{config['output_root']}/Controls/{key}.prefab"
            if prefab is not None:
                output = prefab["file"]["path"]
                if recipe["kind"] != "external" and output != f"{config['output_root']}/Controls/{key}.prefab":
                    raise ProjectError(f"控件 {key} 已登记其他路径，请使用 external 定义引用已有预制体")
            controls[key] = {"path": output, "guid": prefab["file"]["guid"] if prefab else None,
                             "sha256": prefab["file"]["sha256"] if prefab else None,
                             "meta_sha256": prefab["file"]["meta_sha256"] if prefab else None,
                             "width": item["width"], "height": item["height"], "slots": item["slots"],
                             "parameters": item["parameters"], "slot_sources": prefab["slot_sources"] if prefab else {}}
            if key not in control_ids:
                continue
            kind = recipe["kind"]
            nodes = [] if kind == "variant" else _flat_nodes(source, {
                "width": item["width"], "height": item["height"], "background": "#000000", "nodes": recipe["nodes"]})
            target = {"id": key, "kind": kind, "path": output, "width": item["width"], "height": item["height"],
                      "base_control_id": effective[key]["base_control_id"], "nodes": nodes,
                      "overrides": deepcopy(recipe.get("overrides", [])), "slots": item["slots"], "button": recipe.get("button")}
            direct_resources[(kind, key)] = self._resource_ids(nodes, target["overrides"], target["button"], resolved)
            targets.append(target)
        for page_id, nodes in page_nodes.items():
            definition = source["layouts"][page_id]["definition"]
            targets.append({"id": page_id, "kind": "page", "path": f"{config['output_root']}/Pages/{page_id}.prefab",
                            "width": definition["width"], "height": definition["height"], "base_control_id": None,
                            "background": definition["background"], "nodes": nodes, "overrides": [], "slots": {}, "button": None})
            direct_resources[("page", page_id)] = self._resource_ids(nodes, [], None, resolved)
        for asset_ids, style_ids in direct_resources.values():
            extra_assets.update(asset_ids)
            extra_styles.update(style_ids)
        # 继承树内部的资源用于验证和参数覆盖；声明之外的实际节点引用也必须核对。
        from .component_usage import get_usage
        from .page_layouts import iter_layout_nodes
        control_resources, control_inputs = {}, {}
        for key in ordered:
            item = resolved[key]
            nodes = list(iter_layout_nodes(item["nodes"]))
            assets, styles = self._resource_ids(nodes, [], item["button"], resolved)
            assets.update(_asset_ids(effective[key]))
            styles.update(effective[key]["text_style_ids"])
            control_resources[key] = assets, styles
            control_inputs[key] = {
                "definition": _contract(effective[key]), "recipe": item["dependencies"], "path": controls[key]["path"],
                "component_usage": {node["component_id"]: get_usage(source, node["component_id"])
                                    for node in nodes if node["kind"] == "group" and "component_id" in node},
                # 生成控件的文件身份另行核对，首次登记不会改变制作输入。
                "external_prefab": registry["controls"][key]["prefab"] if recipes[key]["kind"] == "external" else None,
            }
            extra_assets.update(assets)
            extra_styles.update(styles)
        definitions = [effective[key] for key in ordered]
        definitions.append({"asset_ids": [], "preview_asset_id": None, "states": {},
                            "text_style_ids": sorted(extra_styles), "source_component_ids": []})
        resources = self.unity._sources(root, definitions, source, extra_assets)
        assets, fonts, font_inputs = resources["assets"], {}, {}
        for key, asset in assets.items():
            asset.setdefault("geometry", None)
            asset["output_path"] = f"{config['output_root']}/Sprites/{key}.png"
            if asset["path"] == asset["output_path"]:
                raise ProjectError(f"Unity 生成图像不能同时作为制作源文件：{asset['path']}")
            asset["expected"] = {"sha256": _file_hash(layout, asset["output_path"]),
                                 "meta_sha256": _file_hash(layout, asset["output_path"] + ".meta")}
        for key, style in resources["text_styles"].items():
            if key not in config["fonts"]:
                raise ProjectError(f"Unity 字体映射缺少文字角色 {key}")
            if style["stroke"] is not None or style["shadow"] is not None:
                raise ProjectError(f"Unity 文字角色 {key} 的描边和投影尚不支持，不能忽略效果")
            fonts[key] = deepcopy(config["fonts"][key]) | {
                field: deepcopy(style[field]) for field in ("font_size", "line_height", "letter_spacing", "fill")}
            font_inputs[key] = {"source_sha256": style["font_sha256"]}
            for prefix in ("font_asset", "material"):
                path = fonts[key][prefix + "_path"]
                digest = _file_hash(layout, path)
                if digest is None:
                    raise ProjectError(f"Unity 文字角色 {key} 的资源不存在：{path}")
                font_inputs[key][prefix] = digest
                try:
                    text = layout.resolve(path + ".meta").read_text(encoding="utf-8")
                except (OSError, UnicodeError) as error:
                    raise ProjectError(f"Unity 文字角色 {key} 无法读取资源 .meta：{path}") from error
                if not re.search(r"(?m)^guid: " + fonts[key][prefix + "_guid"] + r"\s*$", text):
                    raise ProjectError(f"Unity 文字角色 {key} 的资源 GUID 不符：{path}")
                fonts[key][prefix + "_sha256"] = digest
                fonts[key][prefix + "_meta_sha256"] = _file_hash(layout, path + ".meta")
        for target in targets:
            kind, key = target["kind"], target["id"]
            asset_ids, style_ids = direct_resources[(kind, key)]
            # 资源的生成前摘要不是设计输入，不能让首次创建后的第二次请求误判为修改。
            images = {asset_id: {field: assets[asset_id][field] for field in ("output_path", "width", "height", "geometry")}
                      for asset_id in asset_ids}
            nested = {node["control_id"] for node in target["nodes"] if node["kind"] == "control"}
            identity = {control_id: controls[control_id]["path"] for control_id in nested}
            own = deepcopy(target)
            if kind == "variant":
                own.pop("slots")
                own.pop("width")
                own.pop("height")
            target["direct_sha256"] = spec_sha256({"target": own, "assets": images,
                "fonts": {style_id: {field: value for field, value in fonts[style_id].items()
                                     if not field.endswith("_sha256") or field == "font_asset_sha256"}
                          for style_id in style_ids}, "nested_identity": identity})
            scoped_controls = closure[key] if kind != "page" else set().union(*(closure[control_id] for control_id in nested))
            dependency_assets, dependency_styles = set(asset_ids), set(style_ids)
            source_components = set()
            for control_id in scoped_controls:
                image_ids, text_ids = control_resources[control_id]
                dependency_assets.update(image_ids)
                dependency_styles.update(text_ids)
                source_components.update(effective[control_id]["source_component_ids"])
            target["dependency_sha256"] = spec_sha256({"controls": {control_id: control_inputs[control_id]
                                                                  for control_id in scoped_controls},
                "assets": {asset_id: {k: v for k, v in assets[asset_id].items() if k != "expected"}
                           for asset_id in dependency_assets},
                "fonts": {style_id: {"mapping": fonts[style_id], "sources": font_inputs[style_id]} for style_id in dependency_styles},
                "components": {component_id: resources["component_inputs"][component_id] for component_id in source_components}})
            target["expected"] = {"prefab_sha256": _file_hash(layout, target["path"]),
                                  "meta_sha256": _file_hash(layout, target["path"] + ".meta"),
                                  "baseline_sha256": _file_hash(layout, f"smart-ui/unity/import-state/{kind}/{key}.json")}
        return {"schema_version": 1, "request_id": request_id, "operation": operation, "project_id": source["project_id"],
                "result_path": f"{OPERATIONS}/{request_id}/result.json", "targets": targets,
                "assets": assets, "fonts": fonts, "controls": controls}

    @staticmethod
    def _resource_ids(nodes, overrides, button, controls):
        assets, styles = set(), set()
        for node in nodes:
            if node["kind"] == "image":
                assets.add(node["asset_id"])
            elif node["kind"] == "text":
                styles.add(node["text_style_id"])
            elif node["kind"] == "control":
                parameters = controls[node["control_id"]]["parameters"]
                assets.update(value for key, value in node["parameters"].items() if parameters[key]["value_type"] == "asset_id")
        for item in overrides:
            if item["property"] == "asset_id":
                assets.add(item["value"])
            if item["property"] == "text_style_id":
                styles.add(item["value"])
        if button:
            assets.update(button["states"].values())
        return assets, styles

    def _operation(self, root, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ProjectError("request_id 必须是准备请求时返回的 32 位标识")
        layout = ProjectLayout(root)
        directory = layout.resolve(f"{OPERATIONS}/{request_id}")
        try:
            record = json.loads((directory / "operation.json").read_bytes())
            data = (directory / "request.json").read_bytes()
            request = json.loads(data)
            if record["request_id"] != request_id or request["request_id"] != request_id or _sha(data) != record["request_sha256"]:
                raise ProjectError("执行请求身份或摘要不一致")
            return directory, record, request
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProjectError(f"无法读取 Unity 请求 {request_id}：{error}") from error

    def _ensure_idle(self, root, except_id=None):
        directory = ProjectLayout(root).resolve(OPERATIONS)
        if directory.exists():
            for path in directory.glob("*/operation.json"):
                _, record, _ = self._operation(root, path.parent.name)
                if record["request_id"] != except_id and record["status"] in ("running", "indeterminate", "reconcile_required"):
                    raise ProjectError(f"Unity 请求 {record['request_id']} 尚未核对，必须先 reconcile，不能重放构建")

    @staticmethod
    def _guard(record, source_revision, registry_revision, construction_revision):
        if (record["source_revision"], record["registry_revision"], record["construction_revision"]) != (
                source_revision, registry_revision, construction_revision):
            raise ProjectError("Unity 请求准备后项目、控件目录或制作定义已改变；不得使用过期请求")

    def execute(self, project_root, request_id):
        root = self.projects._root(project_root)
        with self.projects._lock(root):
            directory, record, request = self._operation(root, request_id)
            if record["status"] != "prepared":
                raise ProjectError(f"请求 {request_id} 状态为 {record['status']}，只能查询和 reconcile，不可重复执行")
            _, source_revision, _, registry_revision, _, revision = self._read(root)
            self._guard(record, source_revision, registry_revision, revision)
            self._ensure_idle(root, request_id)
            record["status"] = "running"
            self.projects._publish_batch(ProjectLayout(root), {directory / "operation.json": _bytes(record)})
        # Unity 调用可能较长；不占用项目写锁，登记时重新核对所有输入版本。
        try:
            self.cli.execute(root, directory / "request.json")
        except UnityCliError as error:
            with self.projects._lock(root):
                directory, record, _ = self._operation(root, request_id)
                if record["status"] == "running":
                    record.update(status="indeterminate" if error.indeterminate else "failed", error=str(error))
                    self.projects._publish_batch(ProjectLayout(root), {directory / "operation.json": _bytes(record)})
            if not (directory / "result.json").is_file():
                return {"request_id": request_id, "status": record["status"], "error": record["error"]}
        else:
            with self.projects._lock(root):
                directory, record, _ = self._operation(root, request_id)
                if record["status"] == "running" and not (directory / "result.json").is_file():
                    record.update(status="indeterminate", error="Unity CLI 已结束，同一次请求尚无结果文件")
                    self.projects._publish_batch(ProjectLayout(root), {directory / "operation.json": _bytes(record)})
        return self.reconcile(project_root, request_id)

    def reconcile(self, project_root, request_id):
        root = self.projects._root(project_root)
        with self.projects._lock(root):
            directory, record, request = self._operation(root, request_id)
            if record["status"] == "prepared":
                raise ProjectError("尚未执行的请求不能 reconcile")
            path = directory / "result.json"
            if record["result_sha256"] is not None:
                if not path.is_file() or _sha(data := path.read_bytes()) != record["result_sha256"]:
                    raise ProjectError(f"已核对的 Unity 结果文件缺失或摘要已改变：{path}")
                return {"request_id": request_id, "status": record["status"], "result": json.loads(data),
                        "bindings": record["bindings"], "runtime_validation": "not_performed"}
            if not path.is_file():
                return {"request_id": request_id, "status": record["status"], "error": "同一次请求尚无完整结果；不能据此重复执行"}
            try:
                data = path.read_bytes()
                result = json.loads(data)
                if (not isinstance(result, dict) or type(result.get("schema_version")) is not int
                        or result["schema_version"] != 1 or result.get("request_id") != request_id
                        or result.get("request_sha256") != record["request_sha256"]):
                    raise ProjectError("Unity 结果与本次请求的身份或摘要不符")
                if result.get("status") not in ("success", "failed") or not isinstance(result.get("targets"), list):
                    raise ProjectError("Unity 结果状态或目标列表无效")
                source, source_revision, registry, registry_revision, construction, revision = self._read(root)
                self._guard(record, record["source_revision"], registry_revision, revision)
                if source_revision != record["source_revision"]:
                    control_ids = [target["id"] for target in request["targets"] if target["kind"] != "page"]
                    page_ids = [target["id"] for target in request["targets"] if target["kind"] == "page"]
                    current = self._request(root, source, registry, construction, control_ids, page_ids,
                                            request["operation"], request_id, written_controls=set(control_ids))
                    fields = ("kind", "id", "path", "direct_sha256", "dependency_sha256")
                    # 已选目标的文件和生成资源可能由本次执行更新，只比较制作输入。
                    if current["project_id"] != request["project_id"] or any(
                        before[field] != after[field]
                        for before, after in zip(request["targets"], current["targets"], strict=True)
                        for field in fields
                    ):
                        raise ProjectError("Unity 执行期间目标或依赖的制作输入已改变")
                effective = self.unity._effective(registry, source)
                expected = {(item["kind"], item["id"]): item for item in request["targets"]}
                seen = set()
                for item in result["targets"]:
                    key = (item["kind"], item["id"])
                    if key not in expected or key in seen or item["path"] != expected[key]["path"]:
                        raise ProjectError("Unity 结果的目标或路径与请求不符")
                    if item["status"] not in _RESULT_TARGET_STATES[request["operation"]] or (
                        result["status"] == "success" and item["status"] in ("failed", "conflict")
                    ):
                        raise ProjectError(f"Unity 结果的目标状态无效：{key} {item['status']}")
                    seen.add(key)
                    if request["operation"] == "plan" or item["status"] not in ("created", "updated", "verified", "unchanged"):
                        continue
                    target = expected[key]
                    if target["kind"] == "page":
                        sources = sorted({request["controls"][node["control_id"]]["guid"]
                                          for node in target["nodes"] if node["kind"] == "control"})
                    else:
                        definition = registry["controls"][item["id"]]["definition"]
                        dependencies = list(definition["dependencies"])
                        if definition["base_control_id"]:
                            dependencies.append(definition["base_control_id"])
                        sources = sorted({registry["controls"][key]["prefab"]["file"]["guid"] for key in dependencies})
                    file = _inspect_result_prefab(root, request, registry, target, item, sources)
                    if any(file[field] != item[field] for field in ("sha256", "meta_sha256", "source_guids")):
                        raise ProjectError(f"Unity 产物与回执不一致：{item['path']}")
                    if target["kind"] != "page":
                        key = item["id"]
                        previous = registry["controls"][key]["prefab"]
                        if previous is not None and previous["file"]["guid"] != file["guid"]:
                            raise ProjectError(f"Unity 产物的已登记 GUID 发生变化：{item['path']}")
                        for other, control in registry["controls"].items():
                            existing = control["prefab"]
                            if other != key and existing is not None and (
                                existing["file"]["guid"] == file["guid"]
                                or existing["file"]["path"].casefold() == file["path"].casefold()
                            ):
                                raise ProjectError(f"Unity 产物 {item['path']} 的路径或 GUID 已由控件 {other} 登记")
                        slot_sources = self.unity._slot_sources(registry, effective, key, file["slot_bindings"])
                        registry["controls"][key]["prefab"] = {"file": file, "slot_sources": slot_sources,
                            "contract_sha256": _contract(effective[key])}
                if seen != expected.keys():
                    raise ProjectError("Unity 结果缺少请求目标，不能认定执行完成")
                record.update(status="completed" if result["status"] == "success" else "failed",
                              result_sha256=_sha(data), error=result.get("error"))
                artifacts = {directory / "operation.json": _bytes(record)}
                if request["operation"] != "plan":
                    self.unity._write(root, source, source_revision, registry, artifacts)
                else:
                    self.projects._publish_batch(ProjectLayout(root), artifacts)
                return {"request_id": request_id, "status": record["status"], "result": result,
                        "bindings": record["bindings"], "runtime_validation": "not_performed"}
            except (ProjectError, ValueError, OSError, KeyError, TypeError) as error:
                record.update(status="reconcile_required", result_sha256=None, error=str(error))
                self.projects._publish_batch(ProjectLayout(root), {directory / "operation.json": _bytes(record)})
                raise ProjectError(f"Unity 已执行但结果尚未完成核对：{request_id}；{error}") from error
