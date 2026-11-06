"""维护 Unity 控件目录、文件登记及尚未执行导入的交付协议。"""

from copy import deepcopy
import json
import re

from .layout import ProjectLayout
from .projects import ProjectError, ProjectStore, _identifier, _json, _sha, spec_sha256
from .unity_contract import (ControlDefinition, HandoffManifest, HandoffRequest, ManifestPrefab,
                             SlotSource, parse_model, validate_catalog, validate_handoff)
from .unity_prefabs import inspect_prefab, verify_prefab


REGISTRY = "smart-ui/unity/registry.json"
DOCUMENT = "smart-ui/docs/unity-controls.md"


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ProjectError(f"{label} 必须恰好包含 {', '.join(sorted(expected))}")


def _model(model, value, label):
    return parse_model(model, value, label)


def _digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProjectError(f"{label} 必须是小写 SHA-256 摘要")


def _contract(definition):
    """说明文字不改变已经登记的控件接口。"""
    value = deepcopy(definition)
    for key in ("name", "description", "preview_asset_id"):
        value.pop(key)
    for collection in ("slots", "parameters"):
        for item in value[collection].values():
            item.pop("description")
    return spec_sha256(value)


def _asset_ids(definition):
    result = set(definition["asset_ids"])
    if definition["preview_asset_id"] is not None:
        result.add(definition["preview_asset_id"])
    for mapping in definition["states"].values():
        result.update(mapping.values())
    return result


def _closure(selected, controls):
    result = set()

    def visit(key):
        if key in result:
            return
        result.add(key)
        definition = controls[key]["definition"]
        if definition["base_control_id"] is not None:
            visit(definition["base_control_id"])
        for dependency in definition["dependencies"]:
            visit(dependency)

    for key in selected:
        visit(key)
    return sorted(result)


class UnityStore:
    """Unity 登记独立版本化，和项目制作资料共用同一把写锁。"""

    def __init__(self, projects=None):
        self.projects = projects if projects is not None else ProjectStore()

    def _read(self, root):
        source, source_revision = self.projects._read(root)
        layout = ProjectLayout(root)
        path = layout.resolve(REGISTRY)
        if not path.exists():
            if layout.resolve(DOCUMENT).exists():
                raise ProjectError(f"Unity 目录文档未登记，不能覆盖：{DOCUMENT}")
            return source, source_revision, {"schema_version": 1, "controls": {}, "handoffs": {},
                                            "document_sha256": None}, None
        try:
            registry = json.loads(path.read_bytes())
            _fields(registry, {"schema_version", "controls", "handoffs", "document_sha256"}, REGISTRY)
            if type(registry["schema_version"]) is not int or registry["schema_version"] != 1:
                raise ProjectError(f"{REGISTRY} 只接受 schema_version=1")
            for collection in ("controls", "handoffs"):
                if not isinstance(registry[collection], dict):
                    raise ProjectError(f"{REGISTRY}.{collection} 必须是对象")
                seen = {}
                for key in registry[collection]:
                    _identifier(key, collection)
                    self.projects._check_id_collision(seen, key, collection)
                    seen[key] = True
            for key, record in registry["controls"].items():
                _fields(record, {"definition", "prefab"}, f"控件 {key}")
                _model(ControlDefinition, record["definition"], f"控件 {key}")
                if record["prefab"] is not None:
                    prefab = record["prefab"]
                    _fields(prefab, {"file", "contract_sha256", "slot_sources"}, f"控件 {key} 预制体登记")
                    _model(ManifestPrefab, prefab["file"], f"控件 {key} 预制体文件")
                    _digest(prefab["contract_sha256"], f"控件 {key} contract_sha256")
                    if not isinstance(prefab["slot_sources"], dict):
                        raise ProjectError(f"控件 {key} 的 slot_sources 必须是对象")
                    for slot, binding in prefab["slot_sources"].items():
                        _identifier(slot, f"控件 {key} 槽位")
                        _model(SlotSource, binding, f"控件 {key} 槽位 {slot}")
            for key, record in registry["handoffs"].items():
                _fields(record, {"request", "manifest", "page_signatures"}, f"接入计划 {key}")
                _model(HandoffRequest, record["request"], f"接入计划 {key}")
                _fields(record["manifest"], {"path", "sha256"}, f"接入计划 {key} 清单")
                expected = f"smart-ui/unity/handoffs/{key}.json"
                if record["manifest"]["path"] != expected:
                    raise ProjectError(f"接入计划 {key} 清单路径必须为 {expected}")
                manifest_bytes = layout.resolve(expected).read_bytes()
                if _sha(manifest_bytes) != record["manifest"]["sha256"]:
                    raise ProjectError(f"接入清单摘要不一致：{expected}")
                manifest = _model(HandoffManifest, json.loads(manifest_bytes), f"接入清单 {key}")
                signatures = record["page_signatures"]
                page_ids = {page["id"] for page in record["request"]["pages"]}
                if not isinstance(signatures, dict) or set(signatures) != page_ids:
                    raise ProjectError(f"接入计划 {key} 的 page_signatures 必须完整对应请求页面")
                for page_id, signature in signatures.items():
                    _fields(signature, {"direct_sha256", "dependency_sha256"}, f"接入计划 {key}/{page_id} 签名")
                    for field, value in signature.items():
                        _digest(value, f"接入计划 {key}/{page_id}/{field}")
                actual_signatures = {page["id"]: {field: page[field] for field in ("direct_sha256", "dependency_sha256")}
                                     for page in manifest["pages"]}
                actual_request = {"schema_version": 1, "adapter_id": manifest["adapter_id"],
                                  "adapter_version": manifest["adapter_version"], "pages": [
                    {field: value for field, value in page.items() if field not in ("direct_sha256", "dependency_sha256")}
                    for page in manifest["pages"]]}
                if actual_signatures != signatures or actual_request != record["request"]:
                    raise ProjectError(f"接入计划 {key} 的请求或页面签名与清单不一致")
                if manifest["handoff_id"] != key or manifest["project_id"] != source["project_id"]:
                    raise ProjectError(f"接入清单 {key} 的项目或交付标识不一致")
            document = layout.resolve(DOCUMENT).read_bytes()
            if _sha(document) != registry["document_sha256"]:
                raise ProjectError(f"受管文档已被修改：{DOCUMENT}；请通过控件工具维护")
            if document != self._render(root, registry, self._effective(registry, source)).encode("utf-8"):
                raise ProjectError(f"Unity 登记与受管文档不一致：{DOCUMENT}；请通过控件工具维护")
            return source, source_revision, registry, spec_sha256(registry)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProjectError(f"无法读取 Unity 登记 {REGISTRY}：{error}") from error

    @staticmethod
    def _effective(registry, source):
        return validate_catalog({key: item["definition"] for key, item in registry["controls"].items()}, source)

    @staticmethod
    def _slot_sources(registry, effective, control_id, local):
        slots = effective[control_id]["slots"]
        if not isinstance(local, dict) or set(local) - slots.keys():
            raise ProjectError(f"控件 {control_id} 的 slot_bindings 含未公开槽位")
        base_id = registry["controls"][control_id]["definition"]["base_control_id"]
        inherited = {}
        if base_id is not None:
            base = registry["controls"][base_id]["prefab"]
            if base is None:
                raise ProjectError(f"请先登记基础控件 {base_id} 的预制体")
            inherited = base["slot_sources"]
        result = {}
        for slot in slots:
            if slot in local:
                result[slot] = {"control_id": control_id, "file_id": local[slot]}
            elif slot in inherited:
                result[slot] = deepcopy(inherited[slot])
            else:
                raise ProjectError(f"控件 {control_id} 的 slot_bindings 缺少本地公开槽位 {slot}")
        return result

    @staticmethod
    def _prefab_issues(root, registry, effective, *, written_controls=()):
        issues = {}
        checked = set()

        def check(key):
            if key in checked:
                return
            checked.add(key)
            record = registry["controls"][key]
            prefab = record["prefab"]
            if prefab is None:
                issues[key] = {"code": "not_registered", "message": "尚未登记实际预制体文件"}
                return
            try:
                if key not in written_controls:
                    verify_prefab(root, prefab["file"])
                if prefab["contract_sha256"] != _contract(effective[key]):
                    raise ProjectError("控件契约已改变，须核对预制体后重新登记")
                definition = record["definition"]
                dependencies = list(definition["dependencies"])
                if definition["base_control_id"] is not None:
                    dependencies.append(definition["base_control_id"])
                expected_sources = []
                for dependency in dependencies:
                    check(dependency)
                    if dependency in issues:
                        raise ProjectError(f"依赖控件 {dependency} 未就绪：{issues[dependency]['message']}")
                    target = registry["controls"][dependency]["prefab"]
                    if target is None:
                        raise ProjectError(f"引用的控件 {dependency} 尚未登记预制体")
                    expected_sources.append(target["file"]["guid"])
                if sorted(expected_sources) != prefab["file"]["source_guids"]:
                    raise ProjectError("基础或嵌套控件的 GUID 与登记的源预制体连接不一致")
                if prefab["slot_sources"] != UnityStore._slot_sources(registry, effective, key, prefab["file"]["slot_bindings"]):
                    raise ProjectError("继承槽位的对象身份已改变，须重新核对并登记")
            except ProjectError as error:
                # 目录检查显式列出错误；交付入口会拒绝任何未就绪控件。
                issues[key] = {"code": "prefab_invalid", "message": str(error)}

        for key in registry["controls"]:
            check(key)
        return issues

    def _context(self, root, source, source_revision, registry, revision):
        from .page_layouts import iter_layout_nodes
        effective = self._effective(registry, source)
        issues = self._prefab_issues(root, registry, effective)
        controls = {}
        for key, record in registry["controls"].items():
            uses = [{"kind": "control", "control_id": other}
                    for other, item in sorted(registry["controls"].items())
                    if key in item["definition"]["dependencies"] or item["definition"]["base_control_id"] == key]
            uses += [{"kind": "handoff", "handoff_id": handoff_id, "page_id": page["id"], "instance_id": node["id"]}
                     for handoff_id, handoff in sorted(registry["handoffs"].items())
                     for page in handoff["request"]["pages"] for node in page["instances"] if node["control_id"] == key]
            uses += [{"kind": "layout", "page_id": page_id, "instance_id": node["id"]}
                     for page_id, page in sorted(source["layouts"].items())
                     for node in iter_layout_nodes(page["definition"]["nodes"])
                     if node["kind"] == "control" and node["control_id"] == key]
            controls[key] = deepcopy(record) | {"effective_definition": effective[key], "uses": uses,
                "file_status": "not_registered" if record["prefab"] is None else "invalid" if key in issues else "file_checked",
                "issue": issues.get(key), "unity_validation": "not_performed"}
        return {"project_root": str(root), "revision": revision, "source_revision": source_revision,
                "schema_version": 1, "configured": revision is not None, "controls": controls,
                "handoffs": deepcopy(registry["handoffs"]),
                "document": str(root / DOCUMENT) if revision is not None else None}

    def context(self, project_root):
        root = self.projects._root(project_root)
        with self.projects._lock(root):
            source, source_revision, registry, revision = self._read(root)
            return self._context(root, source, source_revision, registry, revision)

    def define_control(self, project_root, expected_revision, control_id, definition):
        root = self.projects._root(project_root)
        _identifier(control_id, "control_id")
        definition = _model(ControlDefinition, definition, f"控件 {control_id}")
        with self.projects._lock(root):
            source, source_revision, registry, revision = self._read(root)
            self._revision(expected_revision, revision)
            self.projects._check_id_collision(registry["controls"], control_id, "control_id")
            previous = registry["controls"].get(control_id)
            registry["controls"][control_id] = {"definition": definition,
                "prefab": previous["prefab"] if previous is not None else None}
            self._effective(registry, source)
            from .unity_builds import read_construction
            from .unity_recipes import resolve_recipes
            from .page_layouts import resolve_definition
            construction, _ = read_construction(root)
            source["unity_controls"] = resolve_recipes(
                {key: item["definition"] for key, item in registry["controls"].items()}, construction["recipes"], source)
            for key, page in source["layouts"].items():
                try:
                    resolve_definition(source, page["definition"])
                except ValueError as error:
                    raise ProjectError(f"控件接口修改使页面 {key} 无效：{error}") from error
            return self._write(root, source, source_revision, registry)

    def register_prefab(self, project_root, expected_revision, control_id, path, guid, slot_bindings):
        root = self.projects._root(project_root)
        with self.projects._lock(root):
            source, source_revision, registry, revision = self._read(root)
            self._revision(expected_revision, revision)
            if control_id not in registry["controls"]:
                raise ProjectError(f"控件不存在：{control_id}")
            effective = self._effective(registry, source)
            slot_sources = self._slot_sources(registry, effective, control_id, slot_bindings)
            record = registry["controls"][control_id]
            definition = record["definition"]
            dependencies = list(definition["dependencies"])
            if definition["base_control_id"] is not None:
                dependencies.append(definition["base_control_id"])
            issues = self._prefab_issues(root, registry, effective)
            source_guids = []
            for dependency in dependencies:
                if dependency in issues:
                    raise ProjectError(f"请先处理依赖控件 {dependency}：{issues[dependency]['message']}")
                prefab = registry["controls"][dependency]["prefab"]
                if prefab is None:
                    raise ProjectError(f"请先登记依赖控件 {dependency} 的预制体")
                verify_prefab(root, prefab["file"])
                source_guids.append(prefab["file"]["guid"])
            file = inspect_prefab(root, path, guid, slot_bindings, source_guids)
            for other, item in registry["controls"].items():
                if other != control_id and item["prefab"] is not None and (
                        item["prefab"]["file"]["guid"] == file["guid"] or
                        item["prefab"]["file"]["path"].casefold() == file["path"].casefold()):
                    raise ProjectError(f"预制体 {path} 已由控件 {other} 登记；请引用已有控件")
            record["prefab"] = {"file": file, "slot_sources": slot_sources,
                                 "contract_sha256": _contract(effective[control_id])}
            return self._write(root, source, source_revision, registry)

    @staticmethod
    def _revision(expected, current):
        if expected != current or (expected is not None and not isinstance(expected, str)):
            raise ProjectError("Unity 登记 revision 冲突，请重新读取控件目录；未启用时使用 null")

    def _write(self, root, source, source_revision, registry, artifacts=None):
        effective = self._effective(registry, source)
        document = self._render(root, registry, effective).encode("utf-8")
        registry["document_sha256"] = _sha(document)
        payload = (_json(registry) + "\n").encode("utf-8")
        result = self._context(root, source, source_revision, registry, spec_sha256(registry))
        files = dict(artifacts or {})
        files[root / DOCUMENT] = document
        files[root / REGISTRY] = payload
        self.projects._publish_batch(ProjectLayout(root), files)
        return result

    @staticmethod
    def _render(root, registry, effective):
        lines = ["# Unity 基础控件目录与使用说明", "", "本文由当前控件登记生成，通过项目工具维护。历史由 Git 保存。", "",
                 "预制体文件核对只覆盖文本对象标识、GUID、嵌套来源和摘要；Unity 导入、组件类型及运行效果尚未验证。", "",
                 "创建版式时选择已登记控件并保留预制体嵌套。只传入明确需要的实例参数，其余属性继承。", ""]
        for key in sorted(registry["controls"]):
            definition = effective[key]
            record = registry["controls"][key]
            lines += [f"## {definition['name']}（{key}）", "", definition["description"], "",
                      f"控件类型：`{definition['control_type']}`。", ""]
            prefab = record["prefab"]
            if prefab is None:
                lines += ["预制体：尚未登记实际文件。", ""]
            else:
                file = prefab["file"]
                # 文档链接由已登记相对路径生成；当前文件安全性由目录检查报告。
                lines += [f"预制体：[工程文件](<../../{file['path']}>)；GUID：`{file['guid']}`。", "",
                          f"登记时文件 SHA-256：`{file['sha256']}`。当前文件状态请调用控件目录检查。", "",
                          "登记接口：" + ("与当前契约一致。" if prefab["contract_sha256"] == _contract(definition)
                                         else "控件契约已变化，需要重新核对并登记。"), ""]
            if definition["base_control_id"] is not None:
                lines += [f"基础控件：`{definition['base_control_id']}`。本控件作为变体保留继承。", ""]
            if definition["dependencies"]:
                lines += ["嵌套控件：" + "、".join(f"`{value}`" for value in definition["dependencies"]) + "。", ""]
            for label, value in (("尺寸约定", definition["layout"]), ("公开槽位", definition["slots"]),
                                 ("允许的实例参数", definition["parameters"]), ("共用状态资产", definition["states"])):
                lines += [f"### {label}", "", "```json", _json(value), "```", ""]
            lines += ["### 制作来源", "", "```json", _json({field: definition[field] for field in
                       ("source_component_ids", "asset_ids", "text_style_ids", "preview_asset_id")}), "```", ""]
            uses = []
            for other, item in sorted(registry["controls"].items()):
                if key == item["definition"]["base_control_id"] or key in item["definition"]["dependencies"]:
                    uses.append(f"控件 `{other}`")
            for handoff_id, handoff in sorted(registry["handoffs"].items()):
                for page in handoff["request"]["pages"]:
                    for node in page["instances"]:
                        if node["control_id"] == key:
                            uses.append(f"接入计划 `{handoff_id}` 的页面 `{page['id']}` / 实例 `{node['id']}`")
            lines += ["### 使用位置", "", *(f"- {item}" for item in uses)] if uses else ["### 使用位置", "", "尚无已登记使用位置。"]
            lines += ["", "使用位置来自控件依赖和交付计划，不表示对应 Unity 页面已生成。", ""]
        return "\n".join(lines) + "\n"

    def _sources(self, root, definitions, source, extra_assets):
        from .image_assets import verify_image_record
        from .page_dependencies import asset_source_issues, component_needs_review
        from .projects import style_sha256
        from .typography import resolve_style, validate_font

        assets = set(extra_assets)
        styles, components = set(), set()
        for definition in definitions:
            assets.update(_asset_ids(definition))
            styles.update(definition["text_style_ids"])
            components.update(definition["source_component_ids"])
        layout = ProjectLayout(root)
        image_issues = asset_source_issues(source)
        images, typography, component_inputs = {}, {}, {}
        for key in sorted(assets):
            if key not in source["images"]:
                raise ProjectError(f"接入资产不存在：{key}")
            if key in image_issues:
                raise ProjectError(f"接入资产 {key} 来源过期：{image_issues[key]['action']}")
            record = source["images"][key]
            layout.require_persistent_input(record["path"], f"Unity 资产 {key}")
            verify_image_record(record)
            images[key] = {field: deepcopy(record[field]) for field in ("path", "sha256", "width", "height")}
            images[key]["path"] = layout.relative(record["path"])
            if "geometry" in record:
                images[key]["geometry"] = deepcopy(record["geometry"])
        for key in sorted(styles):
            style = resolve_style(source["typography"], key)
            validate_font(style["font_path"], style["font_sha256"], label=f"Unity 文字角色 {key}")
            typography[key] = deepcopy(style)
            typography[key]["font_path"] = layout.relative(style["font_path"])
        for key in sorted(components):
            component = source["components"][key]
            if component_needs_review(source, component, style_sha256(source["style"])):
                raise ProjectError(f"来源组件 {key} 尚未按当前输入制作，请先完成制作与导出")
            for field in ("document", "preview", "plan_path", "style_snapshot_path"):
                layout.require_persistent_input(component["latest_run"][field], f"Unity 来源组件 {key}/{field}")
                ProjectStore._run_artifact(component["latest_run"], field)
            self.projects._review_run(source, key)
            component_inputs[key] = {"spec_sha256": spec_sha256(component["spec"]),
                                    "run": deepcopy(component["latest_run"]["artifact_hashes"])}
        return {"assets": images, "text_styles": typography, "component_inputs": component_inputs}

    def prepare_handoff(self, project_root, expected_revision, handoff_id, request):
        root = self.projects._root(project_root)
        _identifier(handoff_id, "handoff_id")
        with self.projects._lock(root):
            source, source_revision, registry, revision = self._read(root)
            self._revision(expected_revision, revision)
            self.projects._check_id_collision(registry["handoffs"], handoff_id, "handoff_id")
            effective = self._effective(registry, source)
            request = validate_handoff(request, effective, source)
            selected = {node["control_id"] for page in request["pages"] for node in page["instances"]}
            closure = _closure(selected, registry["controls"])
            issues = self._prefab_issues(root, registry, effective)
            for key in closure:
                if key in issues:
                    raise ProjectError(f"无法准备接入，控件 {key}：{issues[key]['message']}")
            controls = {key: {"definition": deepcopy(effective[key]),
                              "prefab": deepcopy(registry["controls"][key]["prefab"]["file"]),
                              "slot_sources": deepcopy(registry["controls"][key]["prefab"]["slot_sources"])} for key in closure}
            pages, signatures, changes = [], {}, {}
            all_assets, all_styles, all_components = {}, {}, {}
            previous = registry["handoffs"].get(handoff_id)
            for page in request["pages"]:
                page_keys = _closure({node["control_id"] for node in page["instances"]}, registry["controls"])
                extra_assets = {value for node in page["instances"] for parameter, value in node["parameters"].items()
                                if effective[node["control_id"]]["parameters"][parameter]["value_type"] == "asset_id"}
                try:
                    resources = self._sources(root, [effective[key] for key in page_keys], source, extra_assets)
                except (ValueError, OSError) as error:
                    raise ProjectError(f"页面 {page['id']} 的来源未就绪：{error}") from error
                all_assets.update(resources["assets"])
                all_styles.update(resources["text_styles"])
                all_components.update(resources["component_inputs"])
                direct = spec_sha256({"page": page, "adapter_id": request["adapter_id"],
                                      "adapter_version": request["adapter_version"], "prefabs": {
                    node["control_id"]: controls[node["control_id"]]["prefab"]["guid"] for node in page["instances"]}})
                dependency = spec_sha256({"controls": {key: {
                    "contract_sha256": _contract(effective[key]), "prefab": controls[key]["prefab"],
                    "slot_sources": controls[key]["slot_sources"]} for key in page_keys}, "sources": resources})
                signatures[page["id"]] = {"direct_sha256": direct, "dependency_sha256": dependency}
                old = previous["page_signatures"].get(page["id"]) if previous is not None else None
                changes[page["id"]] = ("new" if old is None else "inputs_changed" if old["direct_sha256"] != direct
                                       else "dependencies_changed" if old["dependency_sha256"] != dependency else "unchanged")
                pages.append(deepcopy(page) | signatures[page["id"]])
            manifest = {"schema_version": 1, "kind": "smartui_unity_handoff", "project_id": source["project_id"],
                        "handoff_id": handoff_id, "source_revision": source_revision,
                        "adapter_id": request["adapter_id"], "adapter_version": request["adapter_version"],
                        "controls": controls, "assets": all_assets, "text_styles": all_styles,
                        "component_inputs": all_components, "pages": pages,
                        "unity_validation": "not_performed"}
            _model(HandoffManifest, manifest, f"交付清单 {handoff_id}")
            relative = f"smart-ui/unity/handoffs/{handoff_id}.json"
            path = ProjectLayout(root).resolve(relative)
            if previous is None and path.exists():
                raise ProjectError(f"未登记的接入清单已存在，不能覆盖：{relative}")
            payload = (_json(manifest) + "\n").encode("utf-8")
            registry["handoffs"][handoff_id] = {"request": request, "manifest": {"path": relative, "sha256": _sha(payload)},
                                               "page_signatures": signatures}
            result = self._write(root, source, source_revision, registry, {path: payload})
            result.update(handoff=manifest, handoff_path=str(path), page_changes=changes)
            return result
