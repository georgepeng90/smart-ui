"""保存项目事实，并从同一份状态生成风格和组件文档。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .layout import DEFAULT_PATHS, ProjectLayout, map_state_paths


class ProjectError(RuntimeError):
    """项目内容、版本或磁盘状态不符合约定。"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)


def spec_sha256(spec: dict) -> str:
    """计算组件规格的稳定摘要，供绘制流程标记实际使用的规格。"""
    payload = json.dumps(spec, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identifier(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or value in (".", ".."):
        raise ProjectError(f"{field} 必须是以字母或数字开头、仅包含字母、数字、点、下划线和连字符的标识")


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"{field} 必须是非空字符串")


class ProjectStore:
    def create(self, project_root: str, project_id: str, name: str, paths: dict | None = None) -> dict:
        root = self._root(project_root)
        _identifier(project_id, "project_id")
        _text(name, "name")
        try:
            layout = ProjectLayout(root, paths)
            for key in layout.paths:
                layout.directory(key)
        except ValueError as error:
            raise ProjectError(str(error)) from error
        root.mkdir(parents=True, exist_ok=True)
        with self._lock(root):
            for path in (layout.project_file, *layout.documents.values()):
                if path.exists():
                    raise ProjectError(f"创建项目不能覆盖已有文件：{path}")
            state = {
                "schema_version": 3, "project_id": project_id, "name": name, "paths": layout.paths,
                "references": {}, "programs": {},
                "style": {"version": 0, "description": "", "rules": [], "parameters": {},
                          "reference_ids": [], "user_confirmation": None},
                "components": {}, "comparisons": {}, "feedback": {}, "visual_checks": {},
                "images": {}, "compositions": {},
            }
            layout.ensure_gitignore()
            return self._write(root, state)

    def context(self, project_root: str) -> dict:
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            return self._context(root, state, revision)

    def add_reference(self, project_root: str, expected_revision: str, reference: dict) -> dict:
        def change(state):
            if not isinstance(reference, dict):
                raise ProjectError("reference 必须是 JSON 对象")
            reference_id = reference.get("id")
            _identifier(reference_id, "reference.id")
            if reference_id in state["references"]:
                raise ProjectError(f"参考标识已存在：{reference_id}")
            state["references"][reference_id] = deepcopy(reference)
        return self._update(project_root, expected_revision, change)

    def save_program(self, project_root: str, expected_revision: str, program_id: str,
                     name: str, description: str, source: str) -> dict:
        """保存程序源码及不可变版本历史；只检查语法，不执行程序。"""
        _identifier(program_id, "program_id")
        _text(name, "name")
        if not isinstance(description, str):
            raise ProjectError("description 必须是字符串")
        _text(source, "source")
        try:
            compile(source, f"{program_id}.py", "exec")
        except (SyntaxError, ValueError) as error:
            raise ProjectError(f"程序 {program_id} 的 Python 源码语法无效：{error}") from error
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            current = state["programs"].get(program_id)
            payload = source.encode("utf-8")
            digest = _sha(payload)
            if current and current["sha256"] == digest:
                current.update(name=name, description=description)
            else:
                version = current["version"] + 1 if current else 1
                relative = f"{state['paths']['programs']}/{program_id}/{version}.py"
                path = self._program_path(root, relative, state["paths"])
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path = self._program_path(root, relative, state["paths"])
                    with path.open("xb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError as error:
                    raise ProjectError(f"程序源码写入失败，已有版本不能覆盖：{path}；{error}") from error
                history = deepcopy(current["history"]) if current else []
                history.append({"version": version, "sha256": digest, "source_path": relative})
                state["programs"][program_id] = {
                    "id": program_id, "name": name, "description": description,
                    "version": version, "sha256": digest, "source_path": relative, "history": history,
                }
            return self._write(root, state)

    def get_program(self, project_root: str, program_id: str) -> dict:
        _identifier(program_id, "program_id")
        root = self._root(project_root)
        with self._lock(root):
            state, _ = self._read(root)
            if program_id not in state["programs"]:
                raise ProjectError(f"程序不存在：{program_id}")
            program = deepcopy(state["programs"][program_id])
            try:
                payload = self._program_path(root, program["source_path"], state["paths"]).read_bytes()
                if _sha(payload) != program["sha256"]:
                    raise ProjectError(f"程序源码已被手动修改，摘要不一致：{program['source_path']}")
                program["source"] = payload.decode("utf-8")
            except (OSError, UnicodeError) as error:
                raise ProjectError(f"无法读取程序源码：{program['source_path']}；{error}") from error
            return program

    def update_style(self, project_root: str, expected_revision: str, description: str,
                     rules: list[dict], parameters: dict, reference_ids: list[str]) -> dict:
        def change(state):
            if not isinstance(description, str):
                raise ProjectError("description 必须是字符串")
            if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
                raise ProjectError("rules 必须是 JSON 对象列表")
            if not isinstance(parameters, dict):
                raise ProjectError("parameters 必须是 JSON 对象")
            self._references(state, reference_ids, "style.reference_ids")
            self._rule_references(state, rules)
            style = state["style"]
            changed = style["rules"] != rules or style["parameters"] != parameters
            style.update(description=description, rules=deepcopy(rules), parameters=deepcopy(parameters),
                         reference_ids=deepcopy(reference_ids), user_confirmation=None)
            if changed:
                style["version"] += 1
                for component in state["components"].values():
                    component["target_style_version"] = style["version"]
        return self._update(project_root, expected_revision, change)

    def define_component(self, project_root: str, expected_revision: str,
                         component_id: str, name: str, spec: dict) -> dict:
        _identifier(component_id, "component_id")
        _text(name, "name")
        if not isinstance(spec, dict):
            raise ProjectError("spec 必须是 JSON 对象")
        def change(state):
            self._component_program(state, spec)
            component = state["components"].setdefault(component_id, {"latest_run": None, "runs": []})
            component.update(name=name, spec=deepcopy(spec), target_style_version=state["style"]["version"],
                             user_confirmation=None)
        return self._update(project_root, expected_revision, change)

    def record_run(self, project_root: str, expected_revision: str, component_id: str, run: dict) -> dict:
        def change(state):
            if component_id not in state["components"]:
                raise ProjectError(f"组件不存在：{component_id}")
            if not isinstance(run, dict):
                raise ProjectError("run 必须是 JSON 对象")
            record = deepcopy(run)
            _identifier(record.get("run_id"), "run.run_id")
            component = state["components"][component_id]
            if any(item["run_id"] == record["run_id"] for item in component["runs"]):
                raise ProjectError(f"绘制记录标识已存在：{record['run_id']}")
            status = record.get("status")
            if status not in ("drawn", "verified", "failed"):
                raise ProjectError("run.status 必须是 drawn、verified 或 failed")
            if status == "failed":
                _text(record.get("error"), "失败记录的 error")
            else:
                if type(record.get("style_version")) is not int or record["style_version"] != state["style"]["version"]:
                    raise ProjectError("run.style_version 必须等于当前风格版本")
                if record.get("spec_sha256") != spec_sha256(component["spec"]):
                    raise ProjectError("run.spec_sha256 与当前组件规格不一致")
                program = self._component_program(state, component["spec"])
                for field, expected in (("program_id", program["id"]), ("program_version", program["version"]),
                                        ("program_sha256", program["sha256"])):
                    if record.get(field) != expected or (field == "program_version" and type(record.get(field)) is not int):
                        raise ProjectError(f"run.{field} 与当前组件使用的程序不一致")
                if status == "verified":
                    verification = record.get("verification")
                    checks = ("reopened", "stable_node_ids", "stable_pixels", "stable_properties")
                    if not isinstance(verification, dict) or any(verification.get(key) is not True for key in checks):
                        raise ProjectError("verified 记录必须提供 verification 对象，且重新打开及稳定性检查均为 true")
                    inspection = record.get("inspection")
                    if not isinstance(inspection, dict) or any(
                        not isinstance(inspection.get(key), dict) or not inspection[key]
                        for key in ("before", "reopened")
                    ):
                        raise ProjectError("verified 记录必须包含 inspection.before 和 inspection.reopened 的实际检查数据")
                record["artifact_hashes"] = {}
                for field, suffix in (("document", ".kra"), ("preview", ".png"),
                                      ("operations_path", None), ("style_snapshot_path", None),
                                      ("program_snapshot_path", ".py"), ("plan_path", ".json")):
                    path_value = record.get(field)
                    _text(path_value, f"run.{field}")
                    path = Path(path_value)
                    if not path.is_absolute() or not path.is_file():
                        raise ProjectError(f"run.{field} 必须是现存文件的绝对路径：{path_value}")
                    if suffix and path.suffix.lower() != suffix:
                        raise ProjectError(f"run.{field} 文件必须使用 {suffix} 扩展名：{path}")
                    record["artifact_hashes"][field] = _sha(path.read_bytes())
                if record["artifact_hashes"]["program_snapshot_path"] != program["sha256"]:
                    raise ProjectError("run.program_snapshot_path 源码摘要与当前程序不一致")
                record["user_confirmation"] = None
                component["latest_run"] = record
                component["user_confirmation"] = None
            component["runs"].append(record)
        return self._update(project_root, expected_revision, change)

    def compare_component(self, project_root: str, expected_revision: str, component_id: str,
                          run_id: str, reference_id: str, reference_region: list[int] | None = None,
                          reference_coordinate_size: list[int] | None = None,
                          artifact_region: list[int] | None = None, reference_scale: float = 1.0,
                          artifact_scale: float = 1.0) -> dict:
        from .comparisons import build_comparison

        root = self._root(project_root)
        for value, field in ((component_id, "component_id"), (run_id, "run_id"), (reference_id, "reference_id")):
            _identifier(value, field)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            component, run, plan, snapshot = self._review_run(state, component_id, run_id)
            if reference_id not in state["references"]:
                raise ProjectError(f"未登记参考：{reference_id}")
            nodes, rules = plan["nodes"], snapshot["style"]["rules"]
            comparison_id = uuid4().hex
            record = build_comparison(str(root), comparison_id, state["references"][reference_id], run,
                                      reference_region=reference_region,
                                      reference_coordinate_size=reference_coordinate_size,
                                      artifact_region=artifact_region, reference_scale=reference_scale,
                                      artifact_scale=artifact_scale)
            record.update(component_id=component_id, run_index=component["runs"].index(run),
                          created_at=datetime.now(timezone.utc).isoformat(),
                          project_revision=revision,
                          run={key: run[key] for key in ("run_id", "status", "style_version", "spec_sha256",
                                                        "program_id", "program_version", "program_sha256")},
                          node_keys=[node["key"] for node in nodes],
                          rule_ids=[rule["id"] for rule in rules if isinstance(rule.get("id"), str)])
            state.setdefault("comparisons", {})[comparison_id] = record
            try:
                updated = self._write(root, state)
            except ProjectError as error:
                raise ProjectError(f"对照图片已保留，但项目记录写入失败：{record['images']['comparison']['path']}；{error}") from error
            return self._comparison_result(updated, comparison_id)

    def create_visual_check(self, project_root: str, expected_revision: str, items: list[dict],
                            background: str, columns: int = 3, grayscale: bool = True) -> dict:
        """用明确的成功运行版本保存多产物检查图，不改变视觉确认状态。"""
        from .visual_checks import MAX_ITEMS, build_visual_check

        root = self._root(project_root)
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            raise ProjectError(f"items 必须包含 1 到 {MAX_ITEMS} 项")
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            resolved, specs = [], []
            for index, item in enumerate(items):
                required = {"component_id", "run_id", "label"}
                if (not isinstance(item, dict) or not required <= item.keys()
                        or item.keys() - (required | {"region", "scale"})):
                    raise ProjectError(f"items[{index}] 必须包含 component_id、run_id、label；仅可附加 region、scale")
                _identifier(item["component_id"], f"items[{index}].component_id")
                _identifier(item["run_id"], f"items[{index}].run_id")
                _text(item["label"], f"items[{index}].label")
                _, run, _, snapshot = self._review_run(state, item["component_id"], item["run_id"])
                resolved.append({"component_id": item["component_id"], "run": run, "label": item["label"],
                                 "region": item.get("region"), "scale": item.get("scale", 1.0)})
                specs.append(snapshot["spec"])
            check_id = uuid4().hex
            record = build_visual_check(str(root), check_id, resolved, background=background,
                                        columns=columns, grayscale=grayscale)
            for entry, spec in zip(record["items"], specs, strict=True):
                entry["spec"] = spec
            record.update(created_at=datetime.now(timezone.utc).isoformat(), project_revision=revision)
            state.setdefault("visual_checks", {})[check_id] = record
            try:
                updated = self._write(root, state)
            except ProjectError as error:
                raise ProjectError(f"视觉检查图片已保留，但项目记录写入失败：{record['images']['overview']['path']}；{error}") from error
            return self._visual_check_result(updated, check_id)

    def get_visual_check(self, project_root: str, check_id: str) -> dict:
        _identifier(check_id, "check_id")
        context = self.context(project_root)
        result = self._visual_check_result(context, check_id)
        # 历史检查读取保存的快照，不要求原始预览图仍然存在。
        for item in result["visual_check"]["images"].values():
            path = Path(item["path"])
            if not path.is_absolute():
                raise ProjectError(f"视觉检查图片必须是绝对路径：{path}")
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise ProjectError(f"无法读取视觉检查图片：{path}；{error}") from error
            if _sha(payload) != item["sha256"]:
                raise ProjectError(f"视觉检查图片 SHA-256 不符：{path}")
        return result

    @staticmethod
    def _visual_check_result(context, check_id):
        record = context["data"].get("visual_checks", {}).get(check_id)
        if record is None:
            raise ProjectError(f"视觉检查记录不存在：{check_id}")
        return {"project_root": context["project_root"], "revision": context["revision"], "visual_check": record}

    def register_image(self, project_root: str, expected_revision: str, asset_id: str,
                       name: str, source_path: str, provenance: dict) -> dict:
        """登记不可变图像版本；生成候选不自动改变选用版本。"""
        from .image_assets import register_image_file

        root = self._root(project_root)
        _identifier(asset_id, "asset_id")
        _text(name, "name")
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            assets = state.setdefault("images", {})
            asset = assets.get(asset_id)
            version = len(asset["versions"]) + 1 if asset else 1
            record = register_image_file(str(root), state["paths"], asset_id, version, source_path, provenance)
            record["created_at"] = datetime.now(timezone.utc).isoformat()
            if asset is None:
                asset = {"id": asset_id, "name": name, "selected_version": None, "versions": []}
                assets[asset_id] = asset
            asset["name"] = name
            asset["versions"].append(record)
            try:
                updated = self._write(root, state)
            except ProjectError as error:
                raise ProjectError(f"图像文件已保留，但项目记录写入失败：{record['path']}；{error}") from error
            return {"project_root": str(root), "revision": updated["revision"],
                    "image": updated["data"]["images"][asset_id], "registered_version": version}

    def select_image(self, project_root: str, expected_revision: str, asset_id: str, version: int) -> dict:
        """明确选用已登记版本，不生成图像或重建已有组合。"""
        from .image_assets import verify_image_version

        _identifier(asset_id, "asset_id")
        def change(state):
            record = self._image_version(state, asset_id, version)
            verify_image_version(record)
            state["images"][asset_id]["selected_version"] = version
        updated = self._update(project_root, expected_revision, change)
        return {"project_root": updated["project_root"], "revision": updated["revision"],
                "image": updated["data"]["images"][asset_id]}

    def export_assets(self, project_root: str, expected_revision: str, component_id: str,
                      run_id: str, exports: list[dict]) -> dict:
        """从明确组件运行导出正式 PNG，保持资产身份及独立选用状态。"""
        from .exports import prepare_exports, publish_exports

        _identifier(component_id, "component_id")
        _identifier(run_id, "run_id")
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            self._review_run(state, component_id, run_id)
            plan = prepare_exports(str(root), state["paths"], state, component_id, run_id, exports)
            record = publish_exports(plan)
            record.update(created_at=datetime.now(timezone.utc).isoformat(), project_revision=revision)
            assets = state.setdefault("images", {})
            for item in record["items"]:
                asset = assets.setdefault(item["asset_id"], {"id": item["asset_id"], "name": item["name"],
                                          "selected_version": None, "versions": []})
                asset["name"] = item["name"]
                if not item["reused"]:
                    asset["versions"].append(deepcopy(item["record"]))
            state.setdefault("exports", {})[record["id"]] = record
            try:
                updated = self._write(root, state)
            except ProjectError as error:
                raise ProjectError(f"正式资产已发布，但项目记录未更新：{record['manifest']['path']}；{error}") from error
            return {"project_root": str(root), "revision": updated["revision"],
                    "export": updated["data"]["exports"][record["id"]]}

    def get_export(self, project_root: str, export_id: str) -> dict:
        """读取原始导出清单和归档资产，生产路径的后续更新不改变历史结果。"""
        from .image_assets import verify_image_version

        _identifier(export_id, "export_id")
        context = self.context(project_root)
        record = context["data"].get("exports", {}).get(export_id)
        if record is None:
            raise ProjectError(f"导出记录不存在：{export_id}")
        manifest = record["manifest"]
        path = Path(manifest["path"])
        if _sha(path.read_bytes()) != manifest["sha256"]:
            raise ProjectError(f"导出清单 SHA-256 不符：{path}")
        for item in record["items"]:
            verify_image_version(item["record"])
        return {"project_root": context["project_root"], "revision": context["revision"], "export": record}

    def get_image(self, project_root: str, asset_id: str, version: int) -> dict:
        from .image_assets import verify_image_version

        _identifier(asset_id, "asset_id")
        context = self.context(project_root)
        record = self._image_version(context["data"], asset_id, version)
        verify_image_version(record)
        return {"project_root": context["project_root"], "revision": context["revision"],
                "asset_id": asset_id, "selected_version": context["data"]["images"][asset_id]["selected_version"],
                "image_version": record}

    @staticmethod
    def _image_version(state, asset_id, version):
        if type(version) is not int or version < 1:
            raise ProjectError("version 必须是正整数")
        asset = state.get("images", {}).get(asset_id)
        if asset is None:
            raise ProjectError(f"图像资产不存在：{asset_id}")
        record = next((item for item in asset["versions"] if item["version"] == version), None)
        if record is None:
            raise ProjectError(f"图像版本不存在：{asset_id}/{version}")
        return record

    def create_composition(self, project_root: str, expected_revision: str, width: int,
                           height: int, background: str, nodes: list[dict]) -> dict:
        """以明确的资产版本和坐标生成组合快照，不调用生图或重画源组件。"""
        from .compositions import build_composition
        from .image_assets import verify_image_version
        from .visual_checks import RUN_FIELDS

        root = self._root(project_root)
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 64:
            raise ProjectError("nodes 必须包含 1 到 64 项")
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            resolved = []
            for index, node in enumerate(nodes):
                if not isinstance(node, dict):
                    raise ProjectError(f"nodes[{index}] 必须是对象")
                kind = node.get("kind")
                common = {"id", "kind", "x", "y"}
                if kind == "component":
                    required = common | {"component_id", "run_id", "width", "height"}
                    optional = {"region"}
                elif kind == "image":
                    required = common | {"asset_id", "version", "width", "height"}
                    optional = {"region"}
                elif kind == "text":
                    required = common | {"text", "font_path", "font_size", "color"}
                    optional = {"stroke_width", "stroke_color"}
                else:
                    raise ProjectError(f"nodes[{index}].kind 必须是 component、image 或 text")
                if not required <= node.keys() or node.keys() - (required | optional):
                    raise ProjectError(f"nodes[{index}] 字段不符合 {kind} 节点要求")
                entry = dict(node)
                if kind == "component":
                    _identifier(node["component_id"], f"nodes[{index}].component_id")
                    _identifier(node["run_id"], f"nodes[{index}].run_id")
                    _, run, _, snapshot = self._review_run(state, node["component_id"], node["run_id"])
                    missing = set(RUN_FIELDS) - run.keys()
                    if missing:
                        raise ProjectError(f"组件运行 {node['component_id']}/{node['run_id']} 缺少字段：{', '.join(sorted(missing))}")
                    self._run_artifact(run, "preview")
                    entry.pop("run_id")
                    entry.update(source=run["preview"], sha256=run["artifact_hashes"]["preview"],
                                 run={key: run[key] for key in RUN_FIELDS}, spec=snapshot["spec"])
                elif kind == "image":
                    _identifier(node["asset_id"], f"nodes[{index}].asset_id")
                    record = self._image_version(state, node["asset_id"], node["version"])
                    verify_image_version(record)
                    entry.update(source=record["path"], sha256=record["sha256"])
                if kind in ("component", "image"):
                    entry.setdefault("region", None)
                resolved.append(entry)
            composition_id = uuid4().hex
            record = build_composition(str(root), composition_id, width, height, background, resolved)
            record.update(created_at=datetime.now(timezone.utc).isoformat(), project_revision=revision)
            state.setdefault("compositions", {})[composition_id] = record
            try:
                updated = self._write(root, state)
            except ProjectError as error:
                raise ProjectError(f"组合图片已保留，但项目记录写入失败：{record['images']['overview']['path']}；{error}") from error
            return self._composition_result(updated, composition_id)

    def save_layout(self, project_root: str, expected_revision: str, layout_id: str, name: str,
                    width: int, height: int, background: str, nodes: list[dict]) -> dict:
        """保存可更新页面定义；不生成图像、不改变资产选用或既有快照。"""
        from .page_layouts import validate_definition, resolve_definition, definition_for_storage

        _identifier(layout_id, "layout_id")
        _text(name, "name")
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            definition = validate_definition(width, height, background, nodes)
            resolve_definition(state, definition)
            digest = spec_sha256(definition_for_storage(definition, ProjectLayout(root, state["paths"])))
            layouts = state.setdefault("layouts", {})
            layout = layouts.get(layout_id)
            if layout and layout["name"] == name and layout["versions"][-1]["definition_sha256"] == digest:
                return self._layout_result(self._context(root, state, revision), layout_id, None)
            if layout is None:
                layout = {"id": layout_id, "name": name, "current_version": 0, "versions": []}
                layouts[layout_id] = layout
            version = layout["current_version"] + 1
            layout.update(name=name, current_version=version)
            layout["versions"].append({"version": version, "name": name, "definition": definition, "definition_sha256": digest,
                                       "created_at": datetime.now(timezone.utc).isoformat()})
            return self._layout_result(self._write(root, state), layout_id, version)

    def get_layout(self, project_root: str, layout_id: str, version: int | None = None) -> dict:
        _identifier(layout_id, "layout_id")
        return self._layout_result(self.context(project_root), layout_id, version)

    def build_layout(self, project_root: str, expected_revision: str, layout_id: str,
                     version: int | None = None) -> dict:
        """用明确布局修订构建新的浏览器快照，不覆盖历史构建。"""
        from .page_builds import build_page

        _identifier(layout_id, "layout_id")
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            page = self._layout_result(self._context(root, state, revision), layout_id, version)["layout"]
            build_id = f"page-{uuid4().hex[:12]}"
            record = build_page(root, build_id, layout_id, page["version"], page["definition"], state)
            record.update(layout_definition_sha256=page["definition_sha256"],
                          created_at=datetime.now(timezone.utc).isoformat(), project_revision=revision)
            state.setdefault("page_builds", {})[build_id] = record
            try:
                updated = self._write(root, state)
            except ProjectError as error:
                raise ProjectError(f"页面快照已保留，但项目记录写入失败：{record['files']['html']['path']}；{error}") from error
            return self._page_build_result(updated, build_id)

    def get_page_build(self, project_root: str, build_id: str) -> dict:
        """读取已保存构建，只依赖快照，不要求原始图像和字体继续存在。"""
        from .page_builds import verify_build

        _identifier(build_id, "build_id")
        context = self.context(project_root)
        record = context["data"].get("page_builds", {}).get(build_id)
        if record is None:
            raise ProjectError(f"页面构建不存在：{build_id}")
        verify_build(record)
        return self._page_build_result(context, build_id)

    @staticmethod
    def _page_build_result(context, build_id):
        state = context["data"]
        record = state.get("page_builds", {}).get(build_id)
        if record is None:
            raise ProjectError(f"页面构建不存在：{build_id}")
        layout = state["layouts"][record["layout_id"]]
        matches_bindings = all(
            node["binding"]["kind"] == "fixed" or
            state["images"][node["asset_id"]]["selected_version"] == node["version"]
            for node in record["nodes"] if node["kind"] == "image")
        return {"project_root": context["project_root"], "revision": context["revision"],
                "page_build": deepcopy(record), "matches_layout": layout["current_version"] == record["layout_version"],
                "matches_bindings": matches_bindings}

    @staticmethod
    def _layout_result(context, layout_id, version):
        layout = context["data"].get("layouts", {}).get(layout_id)
        if layout is None:
            raise ProjectError(f"页面布局不存在：{layout_id}")
        selected = layout["current_version"] if version is None else version
        if type(selected) is not int or not 1 <= selected <= len(layout["versions"]):
            raise ProjectError(f"页面布局版本不存在：{layout_id}/{selected}")
        record = layout["versions"][selected - 1]
        return {"project_root": context["project_root"], "revision": context["revision"],
                "layout": {"id": layout_id, "current_version": layout["current_version"],
                           **deepcopy(record)}}

    def get_composition(self, project_root: str, composition_id: str) -> dict:
        _identifier(composition_id, "composition_id")
        result = self._composition_result(self.context(project_root), composition_id)
        # 历史记录使用保存的图片和字体，不依赖原图仍然存在。
        record = result["composition"]
        for item in [*record["images"].values(), *record["fonts"].values()]:
            path = Path(item["path"])
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise ProjectError(f"无法读取组合快照：{path}；{error}") from error
            if _sha(payload) != item["sha256"]:
                raise ProjectError(f"组合快照 SHA-256 不符：{path}")
        return result

    @staticmethod
    def _composition_result(context, composition_id):
        record = context["data"].get("compositions", {}).get(composition_id)
        if record is None:
            raise ProjectError(f"组合预览不存在：{composition_id}")
        return {"project_root": context["project_root"], "revision": context["revision"], "composition": record}

    def _review_run(self, state, component_id, run_id):
        component = state["components"].get(component_id)
        if component is None:
            raise ProjectError(f"组件不存在：{component_id}")
        run = next((item for item in component["runs"] if item["run_id"] == run_id), None)
        if run is None or run["status"] not in ("drawn", "verified"):
            raise ProjectError(f"run_id 必须对应此组件已保存的成功产物：{component_id}/{run_id}")
        try:
            plan = json.loads(self._run_artifact(run, "plan_path"))
            snapshot = json.loads(self._run_artifact(run, "style_snapshot_path"))
        except (ValueError, UnicodeError) as error:
            raise ProjectError(f"运行计划或风格快照不是有效 JSON：{run_id}；{error}") from error
        if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("style"), dict)
                or snapshot.get("component_id") != component_id
                or snapshot["style"].get("version") != run["style_version"]
                or spec_sha256(snapshot.get("spec")) != run["spec_sha256"]):
            raise ProjectError(f"运行风格快照与记录的风格版本或组件规格不一致：{run_id}")
        nodes = plan.get("nodes") if isinstance(plan, dict) else None
        rules = snapshot["style"].get("rules")
        if (not isinstance(nodes, list) or any(not isinstance(node, dict) or not isinstance(node.get("key"), str) for node in nodes)
                or not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules)):
            raise ProjectError(f"运行快照缺少有效的节点或风格规则：{run_id}")
        return component, run, plan, snapshot

    def get_comparison(self, project_root: str, comparison_id: str) -> dict:
        _identifier(comparison_id, "comparison_id")
        context = self.context(project_root)
        self._check_comparison_images(context["data"], comparison_id)
        return self._comparison_result(context, comparison_id)

    def record_feedback(self, project_root: str, expected_revision: str, comparison_id: str,
                        observation: str, suggested_change: str, target_nodes: list[str] | None = None,
                        rule_ids: list[str] | None = None, source: str = "model") -> dict:
        from .feedback import add_feedback

        feedback_id = uuid4().hex
        def change(state):
            self._check_comparison_images(state, comparison_id)
            add_feedback(state, feedback_id, comparison_id, observation, suggested_change,
                         [] if target_nodes is None else target_nodes, [] if rule_ids is None else rule_ids, source)
        context = self._update(project_root, expected_revision, change)
        return {"project_root": context["project_root"], "revision": context["revision"],
                "feedback": context["data"]["feedback"][feedback_id]}

    def update_feedback(self, project_root: str, expected_revision: str, feedback_id: str,
                        status: str, comment: str, comparison_id: str, source: str = "model") -> dict:
        from .feedback import update_feedback

        def change(state):
            self._check_comparison_images(state, comparison_id)
            update_feedback(state, feedback_id, status, comment, comparison_id, source)
        context = self._update(project_root, expected_revision, change)
        return {"project_root": context["project_root"], "revision": context["revision"],
                "feedback": context["data"]["feedback"][feedback_id]}

    @staticmethod
    def _run_artifact(run, field):
        path_value = run.get(field)
        if not isinstance(path_value, str) or not isinstance(run.get("artifact_hashes"), dict) or field not in run["artifact_hashes"]:
            raise ProjectError(f"运行记录 {run['run_id']} 缺少 {field} 路径或摘要")
        path = Path(path_value)
        if not path.is_absolute():
            raise ProjectError(f"运行记录 {field} 必须是绝对路径：{path}")
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise ProjectError(f"无法读取运行记录 {field}：{path}；{error}") from error
        if _sha(payload) != run["artifact_hashes"][field]:
            raise ProjectError(f"运行记录 {field} 的 SHA-256 不符：{path}")
        return payload

    @staticmethod
    def _check_comparison_images(state, comparison_id):
        _identifier(comparison_id, "comparison_id")
        record = state.get("comparisons", {}).get(comparison_id)
        if record is None:
            raise ProjectError(f"对照记录不存在：{comparison_id}")
        for item in record["images"].values():
            path = Path(item["path"])
            if not path.is_absolute():
                raise ProjectError(f"对照图片必须是绝对路径：{path}")
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise ProjectError(f"无法读取对照图片：{path}；{error}") from error
            if _sha(payload) != item["sha256"]:
                raise ProjectError(f"对照图片 SHA-256 不符：{path}")

    @staticmethod
    def _comparison_result(context, comparison_id):
        data = context["data"]
        feedback = {key: value for key, value in data.get("feedback", {}).items()
                    if value["comparison_id"] == comparison_id or
                    any(item["comparison_id"] == comparison_id for item in value["history"])}
        return {"project_root": context["project_root"], "revision": context["revision"],
                "comparison": data["comparisons"][comparison_id], "feedback": feedback}

    @staticmethod
    def _root(project_root):
        if not isinstance(project_root, str) or not project_root.strip() or not Path(project_root).is_absolute():
            raise ProjectError("project_root 必须是绝对路径")
        return Path(project_root).resolve()

    @staticmethod
    @contextmanager
    def _lock(root):
        path = root / ".smartui/project.lock"
        try:
            ProjectLayout(root)._check_managed_path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("x", encoding="utf-8")
        except FileExistsError as error:
            raise ProjectError(f"项目正在被其他操作使用；如进程已退出，请检查并移除锁文件：{path}") from error
        except (OSError, ValueError) as error:
            raise ProjectError(f"无法获取项目锁：{path}；{error}") from error
        try:
            with stream:
                stream.write(str(os.getpid()))
                stream.flush()
                yield
        finally:
            path.unlink()

    def _read(self, root):
        path = ProjectLayout(root).project_file
        try:
            payload = path.read_bytes()
            def reject_constant(value):
                raise ValueError(f"非法 JSON 数值：{value}")
            state = json.loads(payload, parse_constant=reject_constant)
            if not isinstance(state, dict) or state.get("schema_version") != 3:
                raise ProjectError(f"不支持的项目结构，请先显式迁移旧项目：{path}")
            for field in ("project_id", "name", "references", "style", "components", "programs", "paths"):
                if field not in state:
                    raise ProjectError(f"项目缺少 {field}：{path}")
            if not isinstance(state["paths"], dict) or set(state["paths"]) != set(DEFAULT_PATHS):
                raise ProjectError(f"项目 paths 必须完整声明 documents、references、programs、sources：{path}")
            layout = ProjectLayout(root, state["paths"])
            for key in layout.paths:
                layout.directory(key)
            for field in ("comparisons", "feedback", "visual_checks", "images", "compositions", "exports", "layouts", "page_builds"):
                if field in state and not isinstance(state[field], dict):
                    raise ProjectError(f"项目 {field} 必须是 JSON 对象：{path}")
            for asset_id, asset in state.get("images", {}).items():
                _identifier(asset_id, "images 标识")
                if (not isinstance(asset, dict) or asset.get("id") != asset_id
                        or not isinstance(asset.get("versions"), list) or not asset["versions"]):
                    raise ProjectError(f"图像资产记录无效：{asset_id}")
                _text(asset.get("name"), f"images.{asset_id}.name")
                for index, record in enumerate(asset["versions"], 1):
                    if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != index:
                        raise ProjectError(f"图像版本必须从 1 起连续记录：{asset_id}")
                selected = asset["selected_version"]
                if selected is not None and (type(selected) is not int or not 1 <= selected <= len(asset["versions"])):
                    raise ProjectError(f"图像选用版本无效：{asset_id}/{selected}")
            for layout_id, page in state.get("layouts", {}).items():
                _identifier(layout_id, "layouts 标识")
                if (not isinstance(page, dict) or page.get("id") != layout_id or
                        not isinstance(page.get("versions"), list) or not page["versions"] or
                        type(page.get("current_version")) is not int or page["current_version"] != len(page["versions"])):
                    raise ProjectError(f"页面布局记录无效：{layout_id}")
                _text(page.get("name"), f"layouts.{layout_id}.name")
                for index, record in enumerate(page["versions"], 1):
                    if (not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != index
                            or record.get("definition_sha256") != spec_sha256(record.get("definition"))):
                        raise ProjectError(f"页面布局版本或定义摘要无效：{layout_id}/{index}")
                    _text(record.get("name"), f"layouts.{layout_id}.versions[{index}].name")
            for build_id, build in state.get("page_builds", {}).items():
                _identifier(build_id, "page_builds 标识")
                if not isinstance(build, dict) or build.get("id") != build_id:
                    raise ProjectError(f"页面构建记录无效：{build_id}")
                page = state.get("layouts", {}).get(build.get("layout_id"))
                version = build.get("layout_version")
                if (page is None or type(version) is not int or not 1 <= version <= len(page["versions"]) or
                        build.get("layout_definition_sha256") != page["versions"][version - 1]["definition_sha256"]):
                    raise ProjectError(f"页面构建引用的布局修订或摘要无效：{build_id}")
                from .page_builds import validate_build_record
                validate_build_record(build)
            hashes = state.get("document_hashes")
            if not isinstance(hashes, dict) or set(hashes) != {"style.md", "components.md"}:
                raise ProjectError(f"项目缺少受管文档摘要：{path}")
            for filename, expected in hashes.items():
                document = layout.documents[filename]
                if _sha(document.read_bytes()) != expected:
                    raise ProjectError(f"受管文档已被手动修改，请先处理差异：{document}")
            self._references(state, state["style"]["reference_ids"], "style.reference_ids")
            self._rule_references(state, state["style"]["rules"])
            self._validate_programs(root, state["programs"], state["paths"])
            for component in state["components"].values():
                self._component_program(state, component["spec"])
            return map_state_paths(state, layout.resolve), _sha(payload)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProjectError(f"无法读取项目状态或受管文档：{root}；{error}") from error

    def _update(self, project_root, expected_revision, change):
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            change(state)
            return self._write(root, state)

    @staticmethod
    def _revision(root, expected_revision, revision):
        if not isinstance(expected_revision, str) or expected_revision != revision:
            raise ProjectError(f"项目 revision 冲突，请重新读取上下文：{root}")

    @staticmethod
    def _program_path(root, relative, paths):
        try:
            layout = ProjectLayout(root, paths)
            path = Path(relative)
            directory = layout.directory("programs")
            if path.is_absolute() or ".." in path.parts or not (root / path).is_relative_to(directory):
                raise ProjectError(f"程序源码路径必须位于项目 programs 目录：{relative}")
            layout._check_managed_path(root / path)
            return (root / path).resolve()
        except ValueError as error:
            raise ProjectError(str(error)) from error

    def _validate_programs(self, root, programs, paths):
        if not isinstance(programs, dict):
            raise ProjectError("programs 必须是 JSON 对象")
        for program_id, program in programs.items():
            _identifier(program_id, "program_id")
            if not isinstance(program, dict):
                raise ProjectError(f"程序元数据必须是 JSON 对象：{program_id}")
            _text(program.get("name"), f"programs.{program_id}.name")
            if program.get("id") != program_id or not isinstance(program.get("description"), str):
                raise ProjectError(f"程序标识或说明无效：{program_id}")
            version = program.get("version")
            history = program.get("history")
            if type(version) is not int or version < 1 or not isinstance(history, list) or len(history) != version:
                raise ProjectError(f"程序版本或历史无效：{program_id}")
            for index, item in enumerate(history, 1):
                expected_path = f"{paths['programs']}/{program_id}/{index}.py"
                if (not isinstance(item, dict) or type(item.get("version")) is not int or
                        item["version"] != index or item.get("source_path") != expected_path):
                    raise ProjectError(f"程序历史路径或版本无效：{program_id} 第 {index} 版")
                if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                    raise ProjectError(f"程序源码摘要无效：{expected_path}")
                path = self._program_path(root, expected_path, paths)
                if _sha(path.read_bytes()) != item["sha256"]:
                    raise ProjectError(f"程序源码已被手动修改，摘要不一致：{path}")
            if any(program.get(field) != history[-1][field] for field in ("version", "sha256", "source_path")):
                raise ProjectError(f"当前程序元数据与最新历史不一致：{program_id}")

    @staticmethod
    def _component_program(state, spec):
        program_id = spec.get("program_id")
        _identifier(program_id, "spec.program_id")
        if program_id not in state["programs"]:
            raise ProjectError(f"spec.program_id 引用了不存在的程序：{program_id}")
        return state["programs"][program_id]

    @staticmethod
    def _references(state, references, field):
        if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
            raise ProjectError(f"{field} 必须是参考标识的字符串列表")
        for reference_id in references:
            if reference_id not in state["references"]:
                raise ProjectError(f"{field} 引用了不存在的参考：{reference_id}")

    def _rule_references(self, state, rules):
        def visit(value):
            if isinstance(value, dict):
                for field, child in value.items():
                    if field in ("reference_ids", "evidence_reference_ids"):
                        self._references(state, child, f"rules.{field}")
                    elif field == "reference_id":
                        self._references(state, [child], "rules.reference_id")
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(rules)

    @staticmethod
    def _context(root, state, revision):
        data = deepcopy(state)
        for component_id, component in data["components"].items():
            latest = component["latest_run"]
            program = data["programs"][component["spec"]["program_id"]]
            component["needs_review"] = (latest is None or
                bool(component["runs"] and component["runs"][-1]["status"] == "failed") or
                latest["style_version"] != data["style"]["version"] or
                latest["spec_sha256"] != spec_sha256(component["spec"]) or
                latest.get("program_id") != program["id"] or
                latest.get("program_version") != program["version"] or
                latest.get("program_sha256") != program["sha256"])
            component["open_feedback_count"] = sum(
                item["status"] == "open" and item["component_id"] == component_id
                for item in data.get("feedback", {}).values())
        for comparison in data.get("comparisons", {}).values():
            component = data["components"][comparison["component_id"]]
            latest = component["latest_run"]
            comparison["is_current"] = bool(latest and not component["needs_review"]
                                             and latest["run_id"] == comparison["run"]["run_id"])
        for check in data.get("visual_checks", {}).values():
            for item in check["items"]:
                component = data["components"][item["component_id"]]
                latest = component["latest_run"]
                item["is_current"] = bool(latest and not component["needs_review"]
                                          and latest["run_id"] == item["run"]["run_id"])
            check["is_current"] = all(item["is_current"] for item in check["items"])
        for feedback in data.get("feedback", {}).values():
            reviewed = feedback["history"][-1]["comparison_id"] if feedback["history"] else feedback["comparison_id"]
            feedback["reviewed_comparison_id"] = reviewed
            feedback["applies_to_current_run"] = data["comparisons"][reviewed]["is_current"]
        for composition in data.get("compositions", {}).values():
            for node in composition["nodes"]:
                if node["kind"] == "image":
                    asset = data["images"][node["asset_id"]]
                    node["is_current"] = node["version"] == asset["selected_version"]
                elif node["kind"] == "component":
                    component = data["components"][node["component_id"]]
                    latest = component["latest_run"]
                    node["is_current"] = bool(latest and not component["needs_review"]
                                              and latest["run_id"] == node["run"]["run_id"])
            composition["is_current"] = all(node["is_current"] for node in composition["nodes"] if node["kind"] != "text")
        return {"project_root": str(root), "revision": revision, "data": data}

    def _render_for_root(self, root, state):
        layout = ProjectLayout(root, state["paths"])
        display = map_state_paths(state, layout.document_link)
        for program in display["programs"].values():
            program["source_path"] = layout.document_link(program["source_path"])
        return self._render(display)

    def _render(self, state):
        style = state["style"]
        style_lines = [f"# {state['name']}：项目风格规范", "", f"风格版本：{style['version']}", "",
                       "用户确认：尚未确认。", "", "## 风格说明", "", style["description"] or "尚未填写。", "",
                       "## 通用参数", "", "```json", _json(style["parameters"]), "```", "",
                       "## 风格规则", ""]
        for index, rule in enumerate(style["rules"], 1):
            style_lines.extend([f"### {rule.get('id', f'规则 {index}')}：{rule.get('description', '尚未填写说明')}", ""])
            evidence = {field: rule[field] for field in ("evidence", "evidence_reference_ids", "reference_ids") if field in rule}
            if evidence:
                for field, value in evidence.items():
                    label = "证据" if field == "evidence" else "参考标识"
                    style_lines.append(f"- {label}：{self._describe(value)}")
            else:
                style_lines.append("证据：尚未填写。")
            style_lines.append("")
        if not style["rules"]:
            style_lines.extend(["尚未定义风格规则。", ""])
        style_lines.extend(["## 参考资料", "", "| 参考标识 | 文件名 | 尺寸 | 源文件路径 |", "| --- | --- | --- | --- |"])
        for reference_id, reference in state["references"].items():
            source = reference.get("source", "")
            filename = Path(source).name if source else "未登记源文件"
            size = f"{reference['width']} × {reference['height']}" if "width" in reference and "height" in reference else "未登记"
            style_lines.append("| " + " | ".join(self._cell(value) for value in (
                reference_id, filename, size, self._link("查看源文件", source) if source else "未登记")) + " |")
        style_lines.extend(["", "当前风格引用：" + (", ".join(style["reference_ids"]) or "尚未选择参考资料"), "",
                            "完整结构化记录保存在工程根目录的 .smartui/project.json；本文档由工具生成。", ""])
        component_lines = [f"# {state['name']}：组件规格与绘制记录", "",
                           "用户确认：所有生成组件均尚未确认。", "", "## 绘制程序", "",
                           "| 程序标识 | 名称 | 当前版本 | 源码路径 | SHA-256 摘要 |",
                           "| --- | --- | --- | --- | --- |"]
        for program_id, program in state["programs"].items():
            component_lines.append("| " + " | ".join(self._cell(value) for value in (
                program_id, program["name"], program["version"],
                self._link("查看程序源码", program["source_path"]), program["sha256"])) + " |")
        if not state["programs"]:
            component_lines.append("| 尚未登记程序 | — | — | — | — |")
        component_lines.append("")
        context = self._context(Path("."), state, "")["data"]
        for component_id, component in context["components"].items():
            latest = component["latest_run"]
            program = state["programs"][component["spec"]["program_id"]]
            attempt = component["runs"][-1] if component["runs"] else None
            if attempt and attempt["status"] == "failed":
                status = "最近尝试失败，需要重新检查"
            elif latest is None:
                status = "已定义规格，尚未生成"
            elif component["needs_review"]:
                status = "已有历史产物，当前规格、风格或程序需要重新检查"
            elif latest["status"] == "verified":
                status = "工具检查通过，等待用户确认"
            else:
                status = "已生成，等待工具检查和用户确认"
            component_lines.extend([f"## {component['name']}（{component_id}）", "",
                f"状态：{status}。", "", f"目标风格版本：{component['target_style_version']}", "",
                "实际产物风格版本：" + (str(latest["style_version"]) if latest else "尚无产物"), "",
                f"目标程序：{program['name']}（{program['id']}），版本 {program['version']}", "",
                "实际产物程序版本：" + (f"{latest.get('program_id', '未记录程序')}，版本 {latest.get('program_version', '未记录')}"
                                        if latest else "尚无产物"), "",
                "需要重新检查：" + ("是" if component["needs_review"] else "否"), "",
                "用户确认：尚未确认。", "", "### 组件规格", "", "```json", _json(component["spec"]),
                "```", "", "### 最近成功产物记录", ""])
            if attempt and attempt["status"] == "failed":
                component_lines.extend([f"最近尝试 `{attempt['run_id']}` 失败：{attempt['error']}", "",
                    "下列成功记录仅代表历史结果，不能据此确认当前磁盘文件仍然有效；请重新检查文件及其摘要。", ""])
            if latest:
                for field, label in (("program_snapshot_path", "本次执行源码"), ("plan_path", "本次执行计划")):
                    component_lines.append(f"- {label}：" + (self._link("查看" + label, latest[field])
                                                            if field in latest else "历史记录未保存"))
                component_lines.extend([
                    "- Krita 源文件：" + self._link(Path(latest["document"]).name, latest["document"]),
                    "- PNG 预览：" + self._link(Path(latest["preview"]).name, latest["preview"]),
                    "- 绘制操作记录：" + self._link("查看操作记录", latest["operations_path"]),
                    "- 本次风格快照：" + self._link("查看风格快照", latest["style_snapshot_path"]), "",
                    "### 图层名称与 UUID", "", "| 图层 | UUID |", "| --- | --- |"])
                for name, layer_id in latest.get("layer_ids", {}).items():
                    component_lines.append(f"| {self._cell(name)} | {self._cell(layer_id)} |")
                if not latest.get("layer_ids"):
                    component_lines.append("| 尚未登记 | — |")
                component_lines.extend(["", "### 工具检查结果", ""])
                verification = latest.get("verification", {})
                checks = {"reopened": "重新打开文档", "stable_node_ids": "图层 UUID 保持一致",
                          "stable_pixels": "像素保持一致", "stable_properties": "图层属性保持一致"}
                for field, label in checks.items():
                    result = "通过" if verification.get(field) is True else "未通过" if verification.get(field) is False else "尚未检查"
                    component_lines.append(f"- {label}：{result}")
                component_lines.append("")
            else:
                component_lines.extend(["尚无成功产物。", ""])
            component_lines.extend(["### 执行历史", "", "| 执行标识 | 结果 | 风格版本 | 错误说明 |", "| --- | --- | --- | --- |"])
            for run in component["runs"]:
                result = {"drawn": "已绘制", "verified": "工具检查通过", "failed": "失败"}[run["status"]]
                component_lines.append("| " + " | ".join(self._cell(value) for value in (
                    run["run_id"], result, run.get("style_version", "未记录"), run.get("error") or "—")) + " |")
            component_lines.append("")
            comparisons = {key: value for key, value in context.get("comparisons", {}).items()
                           if value["component_id"] == component_id}
            if comparisons:
                component_lines.extend(["### 参考与产物对照", "",
                    "对照保留当次裁切和运行版本；当前状态只根据项目记录判断，不表示视觉通过。", "",
                    "| 对照标识 | 参考与原图区域 | 运行标识 | 风格 / 程序版本 | 对应当前记录 | 图片 |",
                    "| --- | --- | --- | --- | --- | --- |"])
                for key, comparison in comparisons.items():
                    run = comparison["run"]
                    component_lines.append("| " + " | ".join(self._cell(value) for value in (
                        key, f"{comparison['reference']['id']} {comparison['reference']['region_source_xywh']}",
                        run["run_id"], f"{run['style_version']} / {run['program_id']} v{run['program_version']}",
                        "是" if comparison["is_current"] else "否",
                        self._link("查看对照图片", comparison["images"]["comparison"]["path"]))) + " |")
                component_lines.append("")
            feedback_items = [value for value in context.get("feedback", {}).values()
                              if value["component_id"] == component_id]
            if feedback_items:
                component_lines.extend(["### 视觉偏差与复查记录", "",
                    f"待处理偏差：{component['open_feedback_count']} 项。复查结论仅针对所关联的产物，不代表用户风格确认。", ""])
                for feedback in feedback_items:
                    status_text = {"open": "待处理", "resolved": "已解决", "dismissed": "不处理"}[feedback["status"]]
                    component_lines.extend([
                        f"#### {feedback['id']}：{status_text}", "",
                        f"- 原始对照：{feedback['comparison_id']}",
                        f"- 观察：{feedback['observation']}",
                        f"- 修改建议：{feedback['suggested_change']}",
                        f"- 来源声明：{feedback['source']}",
                        f"- 目标节点：{', '.join(feedback['target_nodes']) or '未指定'}",
                        f"- 风格规则：{', '.join(feedback['rule_ids']) or '未指定'}",
                        f"- 复查结论对应当前运行：{'是' if feedback['applies_to_current_run'] else '否'}", ""])
                    for item in feedback["history"]:
                        component_lines.append(f"- {item['status']} / {item['comparison_id']} / {item['source']}：{item['comment']}")
                    component_lines.append("")
        if not context["components"]:
            component_lines.extend(["尚未定义组件。", ""])
        if context.get("visual_checks"):
            component_lines.extend(["## 多产物视觉检查", "",
                "检查图保留明确运行版本、裁切和显示参数。是否对应当前记录不表示视觉质量或用户确认。", ""])
            for check in context["visual_checks"].values():
                display = check["display"]
                component_lines.extend([f"### 检查 {check['id']}", "",
                    f"背景：{display['background']}；列数：{display['columns']}；未做 ICC 色彩转换。", "",
                    self._link("查看彩色总览", check["images"]["overview"]["path"]), ""])
                if display["grayscale"]:
                    component_lines.extend([self._link("查看背景合成后的灰度图", check["images"]["grayscale"]["path"]), ""])
                component_lines.extend(["| 序号 | 说明 | 组件 | 运行 | 风格 / 程序版本 | 原图区域 | 显示倍率 | 对应当前记录 |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |"])
                for index, item in enumerate(check["items"], 1):
                    run = item["run"]
                    component_lines.append("| " + " | ".join(self._cell(value) for value in (
                        index, item["label"], item["component_id"], run["run_id"],
                        f"{run['style_version']} / {run['program_id']} v{run['program_version']}",
                        item["region_source_xywh"], item["scale"], "是" if item["is_current"] else "否")) + " |")
                component_lines.append("")
        if context.get("images"):
            component_lines.extend(["## 独立图像资产", "",
                "登记与选用分开；选用版本不表示用户美术确认。", "",
                "| 资产 | 名称 | 选用版本 | 已登记版本与文件 |",
                "| --- | --- | --- | --- |"])
            for asset in context["images"].values():
                versions = "、".join(self._link(f"v{item['version']}（{item['width']}×{item['height']}）", item["path"])
                                     for item in asset["versions"])
                component_lines.append("| " + " | ".join(self._cell(value) for value in (
                    asset["id"], asset["name"], asset["selected_version"] if asset["selected_version"] is not None else "尚未选用", versions)) + " |")
            component_lines.append("")
        if context.get("compositions"):
            component_lines.extend(["## UI 组合预览", "",
                "预览保存组件、图像和字体的实际快照；引用版本匹配不表示视觉确认或运行态验证。", ""])
            for composition in context["compositions"].values():
                component_lines.extend([f"### 组合 {composition['id']}", "",
                    f"画布：{composition['width']} × {composition['height']}；背景：{composition['background']}。", "",
                    self._link("查看组合预览", composition["images"]["overview"]["path"]), "",
                    f"引用是否对应当前选用及组件运行：{'是' if composition['is_current'] else '否'}。", "",
                    "| 节点 | 类型 | 来源 | 实际显示区域 |", "| --- | --- | --- | --- |"])
                for node in composition["nodes"]:
                    source = (f"{node['asset_id']} v{node['version']}" if node["kind"] == "image" else
                              f"{node['component_id']} / {node['run']['run_id']}" if node["kind"] == "component" else node["text"])
                    component_lines.append("| " + " | ".join(self._cell(value) for value in (
                        node["id"], node["kind"], source, node["display_rect"])) + " |")
                component_lines.append("")
        component_lines.extend(["完整规格、产物摘要和检查数据保存在工程根目录的 .smartui/project.json；本文档由工具生成。", ""])
        return {"style.md": "\n".join(style_lines).encode("utf-8"),
                "components.md": "\n".join(component_lines).encode("utf-8")}

    @staticmethod
    def _cell(value):
        return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")

    @staticmethod
    def _link(label, path):
        return f"[{label}](<{Path(path).as_posix()}>)"

    @classmethod
    def _describe(cls, value):
        labels = {"reference_id": "参考标识", "reference_ids": "参考标识", "description": "说明",
                  "note": "说明", "notes": "说明", "reason": "依据", "patch_id": "取样区域",
                  "color": "颜色", "hex": "色值", "source": "来源", "confidence": "可信度"}
        if isinstance(value, dict):
            return "；".join(f"{labels.get(key, key)}：{cls._describe(child)}" for key, child in value.items())
        if isinstance(value, list):
            return "；".join(cls._describe(child) for child in value)
        return str(value)

    def _write(self, root, state):
        try:
            layout = ProjectLayout(root, state["paths"])
            documents = self._render_for_root(root, state)
            state["document_hashes"] = {filename: _sha(payload) for filename, payload in documents.items()}
            stored = map_state_paths(state, layout.relative)
            payload = (_json(stored) + "\n").encode("utf-8")
        except (ValueError, TypeError) as error:
            raise ProjectError(f"项目包含无法写入的 JSON 数据：{error}") from error
        # 先生成完整临时文件；最后替换项目状态，作为本次多文件写入的提交记录。
        pending = []
        try:
            files = [(layout.documents[name], content) for name, content in documents.items()]
            files.append((layout.project_file, payload))
            for destination, content in files:
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
                with temporary.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                pending.append((temporary, destination))
            for temporary, destination in pending:
                os.replace(temporary, destination)
        except OSError as error:
            raise ProjectError(f"项目写入失败，请检查磁盘及项目文件；未自动覆盖或修复：{root}；{error}") from error
        return self._context(root, map_state_paths(stored, layout.resolve), _sha(payload))
