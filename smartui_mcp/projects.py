"""保存项目事实，并从同一份状态生成风格和组件文档。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

from .layout import SCHEMA_MIGRATION_BACKUPS, ProjectLayout, map_state_paths


class ProjectError(RuntimeError):
    """项目内容、版本或磁盘状态不符合约定。"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)


def spec_sha256(spec: dict) -> str:
    """计算组件规格的稳定摘要，供绘制流程标记实际使用的规格。"""
    payload = json.dumps(spec, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def style_sha256(style: dict) -> str:
    """计算风格规则、共用参数及参考引用的稳定摘要。"""
    try:
        inputs = {field: style[field] for field in ("rules", "parameters", "reference_ids")}
    except KeyError as error:
        raise ProjectError(f"风格缺少绘制依据字段：{error.args[0]}") from error
    return spec_sha256(inputs)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identifier(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or value in (".", ".."):
        raise ProjectError(f"{field} 必须是以字母或数字开头、仅包含字母、数字、点、下划线和连字符的标识")


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"{field} 必须是非空字符串")


def _validate_ui_settings(settings):
    if not isinstance(settings, dict) or settings.keys() - {"reference_size", "scroll"}:
        raise ProjectError("ui_settings 仅支持 reference_size 和 scroll")
    if "reference_size" in settings:
        size = settings["reference_size"]
        if (not isinstance(size, dict) or size.keys() != {"width", "height"}
                or any(type(value) is not int or value <= 0 for value in size.values())):
            raise ProjectError("reference_size 必须包含正整数 width 和 height")
    if "scroll" in settings:
        scroll = settings["scroll"]
        if (not isinstance(scroll, dict) or scroll.keys() != {"movement", "drag_short_content"}
                or scroll["movement"] not in ("Elastic", "Clamped")
                or type(scroll["drag_short_content"]) is not bool):
            raise ProjectError("scroll 必须包含 Elastic 或 Clamped movement 和布尔值 drag_short_content")


class ProjectStore:
    def create(self, project_root: str, project_id: str, name: str, data_root: str | None = None) -> dict:
        root = self._root(project_root)
        _identifier(project_id, "project_id")
        _text(name, "name")
        root.mkdir(parents=True, exist_ok=True)
        with self._lock(root):
            try:
                layout = ProjectLayout(root, data_root=data_root)
                for key in layout.paths:
                    layout.directory(key)
                managed = (layout.project_file, layout.persistent("typography.json"), *layout.documents.values())
            except (OSError, ValueError) as error:
                raise ProjectError(str(error)) from error
            for path in managed:
                if path.exists():
                    raise ProjectError(f"创建项目不能覆盖已有文件：{path}")
            state = {
                "schema_version": 5, "project_id": project_id, "name": name, "paths": layout.paths,
                "references": {}, "programs": {},
                "style": { "description": "", "rules": [], "parameters": {},
                          "reference_ids": [], "user_confirmation": None},
                "components": {}, "comparisons": {}, "feedback": {}, "visual_checks": {},
                "images": {}, "compositions": {}, "typography": None,
            }
            layout.ensure_gitignore()
            return self._write(root, state)

    def context(self, project_root: str) -> dict:
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            return self._context(root, state, revision)

    def update_ui_settings(self, project_root: str, expected_revision: str, settings: dict) -> dict:
        _validate_ui_settings(settings)

        def change(state):
            state["ui_settings"] = deepcopy(settings)

        return self._update(project_root, expected_revision, change)

    def set_public_images(self, project_root, expected_revision, asset_ids):
        def change(state):
            if len(asset_ids) != len(set(asset_ids)) or set(asset_ids) - state["images"].keys():
                raise ProjectError("公共图素必须使用无重复的已登记资产 ID")
            state["public_image_ids"] = sorted(asset_ids)

        return self._update(project_root, expected_revision, change)

    def add_reference(self, project_root: str, expected_revision: str, reference: dict) -> dict:
        def change(state):
            if not isinstance(reference, dict):
                raise ProjectError("reference 必须是 JSON 对象")
            reference_id = reference.get("id")
            _identifier(reference_id, "reference.id")
            self._check_id_collision(state["references"], reference_id, "reference_id")
            if reference_id in state["references"]:
                raise ProjectError(f"参考标识已存在：{reference_id}")
            state["references"][reference_id] = deepcopy(reference)
        return self._update(project_root, expected_revision, change)

    def save_program(self, project_root, expected_revision, program_id, name, description, source):
        """更新唯一的当前程序；Git 负责保存历史。"""
        from .programs import validate_source
        _identifier(program_id, "program_id")
        _text(name, "name")
        if not isinstance(description, str):
            raise ProjectError("description 必须是字符串")
        try:
            validate_source(source)
        except ValueError as error:
            raise ProjectError(f"程序 {program_id} 源码无效：{error}") from error
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            self._check_id_collision(state["programs"], program_id, "program_id")
            layout = ProjectLayout(root, state["paths"])
            relative = f"{layout.paths['programs']}/{program_id}.py"
            path = self._program_path(layout, relative)
            if program_id not in state["programs"] and path.exists():
                raise ProjectError(f"未登记的程序文件已存在，不能覆盖：{path}")
            payload = source.encode("utf-8")
            state["programs"][program_id] = {"id": program_id, "name": name, "description": description,
                                            "sha256": _sha(payload), "source_path": relative}
            return self._write(root, state, {path: payload})

    def get_program(self, project_root: str, program_id: str) -> dict:
        _identifier(program_id, "program_id")
        root = self._root(project_root)
        with self._lock(root):
            state, _ = self._read(root)
            if program_id not in state["programs"]:
                raise ProjectError(f"程序不存在：{program_id}")
            program = deepcopy(state["programs"][program_id])
            try:
                layout = ProjectLayout(root, state["paths"])
                payload = self._program_path(layout, program["source_path"]).read_bytes()
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
            style.update(description=description, rules=deepcopy(rules), parameters=deepcopy(parameters),
                         reference_ids=deepcopy(reference_ids), user_confirmation=None)
        return self._update(project_root, expected_revision, change)

    def update_typography(self, project_root: str, expected_revision: str, typography: dict) -> dict:
        """保存当前文字规范，拒绝悬空引用；只使使用变化样式的页面构建过期。"""
        from .page_layouts import iter_layout_nodes, iter_page_preview_nodes
        from .typography import resolve_style, validate_font, validate_typography
        from .unity import REGISTRY, UnityStore

        root = self._root(project_root)
        def change(state):
            current = validate_typography(typography, root, state["references"])
            for layout_id, page in state["layouts"].items():
                for node in iter_page_preview_nodes(state, page["definition"]["nodes"]):
                    if node["kind"] != "text":
                        continue
                    style_id = node["text_style_id"]
                    if style_id not in current["styles"]:
                        raise ProjectError(f"不能移除文字样式 {style_id}：页面 {layout_id} 的节点 {node['id']} 仍在引用")
                    style = resolve_style(current, style_id)
                    validate_font(style["font_path"], style["font_sha256"], text=node["text"],
                                  label=f"页面 {layout_id} 节点 {node['id']} 的文字样式 {style_id}")
            previous = state["typography"]
            removed = previous["styles"].keys() - current["styles"].keys() if previous is not None else set()
            if removed:
                for control_id, recipe in state["unity_controls"].items():
                    for node in iter_layout_nodes(recipe["nodes"]):
                        if node["kind"] == "text" and node["text_style_id"] in removed:
                            raise ProjectError(f"不能移除文字样式 {node['text_style_id']}："
                                               f"Unity 控件 {control_id} 的节点 {node['id']} 仍在引用")
            if removed and ProjectLayout(root, state["paths"]).persistent(REGISTRY).exists():
                _, _, registry, _ = UnityStore(self)._read(root)
                for control_id, record in sorted(registry["controls"].items()):
                    for style_id in record["definition"]["text_style_ids"]:
                        if style_id in removed:
                            raise ProjectError(f"不能移除文字样式 {style_id}：Unity 控件 {control_id} 仍在引用")
            state["typography"] = current
        return self._update(project_root, expected_revision, change)

    def define_component(self, project_root: str, expected_revision: str,
                         component_id: str, name: str, spec: dict) -> dict:
        from .component_usage import validate_usage

        _identifier(component_id, "component_id")
        _text(name, "name")
        if not isinstance(spec, dict):
            raise ProjectError("spec 必须是 JSON 对象")
        if "usage" in spec:
            try:
                validate_usage(spec["usage"])
            except ValueError as error:
                raise ProjectError(f"组件 {component_id}：{error}") from error
        def change(state):
            self._component_program(state, spec)
            self._check_id_collision(state["components"], component_id, "component_id")
            component = state["components"].setdefault(component_id, {"latest_run": None})
            component.update(name=name, spec=deepcopy(spec),
                             user_confirmation=None)
        return self._update(project_root, expected_revision, change)

    def record_run(self, project_root: str, expected_revision: str, component_id: str, run: dict) -> dict:
        return self._update(project_root, expected_revision,
                            lambda state: self._record_run(state, component_id, run))

    def _record_run(self, state, component_id, run, document_artifact=None):
        if component_id not in state["components"]:
            raise ProjectError(f"组件不存在：{component_id}")
        if not isinstance(run, dict):
            raise ProjectError("run 必须是 JSON 对象")
        record = deepcopy(run)
        _identifier(record.get("run_id"), "run.run_id")
        component = state["components"][component_id]
        status = record.get("status")
        if status not in ("drawn", "verified"):
            raise ProjectError("持久组件记录只接受 drawn 或 verified 成功产物")
        if record.get("style_sha256") != style_sha256(state["style"]):
            raise ProjectError("run.style_sha256 必须等于当前风格摘要")
        if record.get("spec_sha256") != spec_sha256(component["spec"]):
            raise ProjectError("run.spec_sha256 与当前组件规格不一致")
        program = self._component_program(state, component["spec"])
        backend = record.get("backend")
        if not isinstance(backend, dict) or backend.get("name") != "photocraft":
            raise ProjectError("成功运行必须记录 PhotoCraft backend")
        _text(backend.get("version"), "run.backend.version")
        if not isinstance(backend.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", backend["sha256"]):
            raise ProjectError("run.backend.sha256 必须是实际可执行文件的 SHA-256")
        if not isinstance(backend.get("cli_path"), str) or not Path(backend["cli_path"]).is_absolute():
            raise ProjectError("run.backend.cli_path 必须是实际 CLI 的绝对路径")
        for field, expected in (("program_id", program["id"]),
                                ("program_sha256", program["sha256"])):
            if record.get(field) != expected:
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
        for field, suffix in (("document", ".pcraft"), ("preview", ".png"),
                              ("style_snapshot_path", ".json"), ("plan_path", ".json")):
            path_value = record.get(field)
            _text(path_value, f"run.{field}")
            path = Path(document_artifact if field == "document" and document_artifact is not None else path_value)
            if not path.is_absolute() or not path.is_file():
                raise ProjectError(f"run.{field} 必须是现存文件的绝对路径：{path_value}")
            if suffix and path.suffix.lower() != suffix:
                raise ProjectError(f"run.{field} 文件必须使用 {suffix} 扩展名：{path}")
            record["artifact_hashes"][field] = _sha(path.read_bytes())
        record["user_confirmation"] = None
        component["latest_run"] = record
        component["user_confirmation"] = None

    def publish_run(self, project_root, expected_revision, component_id, run):
        """在项目锁内校验候选并覆盖当前源文件和配套制作数据。"""
        _identifier(component_id, "component_id")
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            component = state["components"][component_id]
            layout = ProjectLayout(root, state["paths"])
            document = layout.persistent(f"sources/{component_id}.pcraft")
            if Path(run["document"]) != document:
                raise ProjectError("运行源文件路径与组件路径不同")
            latest = component["latest_run"]
            if latest:
                if not document.is_file() or _sha(document.read_bytes()) != latest["artifact_hashes"]["document"]:
                    raise ProjectError("制作期间源文件被外部修改；原源文件未覆盖")
            elif document.exists():
                raise ProjectError(f"未登记源文件已存在：{document}")
            candidate = Path(run["source_snapshot_path"])
            if run["status"] != "verified":
                raise ProjectError("只有通过保存重开核验的产物才能发布")
            self._record_run(state, component_id, run, document_artifact=candidate)
            current = component["latest_run"]
            destinations = {"document": document,
                "preview": layout.persistent(f"previews/components/{component_id}.png"),
                "plan_path": layout.persistent(f"components/{component_id}/plan.json"),
                "style_snapshot_path": layout.persistent(f"components/{component_id}/inputs.json")}
            artifacts = {}
            for field, destination in destinations.items():
                source = candidate if field == "document" else Path(run[field])
                payload = source.read_bytes()
                if _sha(payload) != current["artifact_hashes"][field]:
                    raise ProjectError(f"候选产物在发布前发生变化：{source}")
                artifacts[destination] = payload
                current[field] = str(destination)
            for field in ("operations_path", "program_snapshot_path", "source_snapshot_path"):
                current.pop(field, None)
            current["backend"].pop("cli_path")
            return self._write(root, state, artifacts)

    def compare_component(self, project_root: str, expected_revision: str, component_id: str,
                          reference_id: str, reference_region: list[int] | None = None,
                          reference_coordinate_size: list[int] | None = None,
                          artifact_region: list[int] | None = None, reference_scale: float = 1.0,
                          artifact_scale: float = 1.0) -> dict:
        from .comparisons import build_comparison

        root = self._root(project_root)
        for value, field in ((component_id, "component_id"), (reference_id, "reference_id")):
            _identifier(value, field)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            _, run, plan, snapshot = self._review_run(state, component_id)
            if reference_id not in state["references"]:
                raise ProjectError(f"未登记参考：{reference_id}")
            nodes, rules = plan["nodes"], snapshot["style"]["rules"]
            comparison_id = "comparison-" + spec_sha256([component_id, reference_id])
            def commit(record):
                record.update(component_id=component_id,
                              created_at=datetime.now(timezone.utc).isoformat(),
                              project_revision=revision,
                              run={key: run[key] for key in ("run_id", "status", "style_sha256", "spec_sha256",
                                                            "program_id", "program_sha256")},
                              node_keys=[node["key"] for node in nodes],
                              rule_ids=[rule["id"] for rule in rules if isinstance(rule.get("id"), str)])
                state["comparisons"][comparison_id] = record
                return self._write(root, state)

            updated = build_comparison(str(root), comparison_id, state["references"][reference_id], run,
                                       reference_region=reference_region,
                                       reference_coordinate_size=reference_coordinate_size,
                                       artifact_region=artifact_region, reference_scale=reference_scale,
                                       artifact_scale=artifact_scale, paths=state["paths"], commit=commit)
            return self._comparison_result(updated, comparison_id)

    def create_visual_check(self, project_root: str, expected_revision: str, check_id: str, items: list[dict],
                            background: str, columns: int = 3, grayscale: bool = True) -> dict:
        """用当前成功产物保存检查图。"""
        from .visual_checks import MAX_ITEMS, build_visual_check

        root = self._root(project_root)
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            raise ProjectError(f"items 必须包含 1 到 {MAX_ITEMS} 项")
        _identifier(check_id, "check_id")
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            self._check_id_collision(state["visual_checks"], check_id, "check_id")
            resolved, specs = [], []
            for index, item in enumerate(items):
                required = {"component_id", "label"}
                if (not isinstance(item, dict) or not required <= item.keys()
                        or item.keys() - (required | {"region", "scale"})):
                    raise ProjectError(f"items[{index}] 必须包含 component_id、label；仅可附加 region、scale")
                _identifier(item["component_id"], f"items[{index}].component_id")
                _text(item["label"], f"items[{index}].label")
                _, run, _, snapshot = self._review_run(state, item["component_id"])
                resolved.append({"component_id": item["component_id"], "run": run, "label": item["label"],
                                 "region": item.get("region"), "scale": item.get("scale", 1.0)})
                specs.append(snapshot["spec"])
            def commit(record):
                for entry, spec in zip(record["items"], specs, strict=True):
                    entry["spec"] = spec
                record.update(created_at=datetime.now(timezone.utc).isoformat(), project_revision=revision)
                state["visual_checks"][check_id] = record
                return self._write(root, state)

            updated = build_visual_check(str(root), check_id, resolved, background=background,
                                         columns=columns, grayscale=grayscale, paths=state["paths"], commit=commit)
            return self._visual_check_result(updated, check_id)

    def get_visual_check(self, project_root: str, check_id: str) -> dict:
        _identifier(check_id, "check_id")
        context = self.context(project_root)
        result = self._visual_check_result(context, check_id)
        # 校验当前检查图。
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

    def register_image(self, project_root, expected_revision, asset_id, name, source_path, provenance):
        from .image_assets import register_image_file, verify_image_record
        root = self._root(project_root)
        _identifier(asset_id, "asset_id")
        _text(name, "name")
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            self._check_id_collision(state["images"], asset_id, "asset_id")
            previous = state["images"].get(asset_id)
            if previous:
                verify_image_record(previous)
                if previous["provenance"]["kind"] == "component_export":
                    raise ProjectError("正式导出资产应通过导出工具更新")
            def commit(record):
                state["images"][asset_id] = record
                return self._write(root, state)

            context = register_image_file(str(root), state["paths"], asset_id, name, source_path, provenance, commit=commit)
            return {"project_root": str(root), "revision": context["revision"], "image": context["data"]["images"][asset_id]}



    def export_assets(self, project_root, expected_revision, component_id, exports):
        from .exports import prepare_exports, publish_exports
        _identifier(component_id, "component_id")
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            self._review_run(state, component_id)
            plan = prepare_exports(str(root), state["paths"], state, component_id, exports)
            asset_ids = set(state["images"])
            for item in plan["items"]:
                asset_id = item["asset_id"]
                self._check_id_collision(asset_ids, asset_id, "asset_id")
                asset_ids.add(asset_id)
            def commit(record):
                record.update(created_at=datetime.now(timezone.utc).isoformat())
                for item in record["items"]:
                    state["images"][item["asset_id"]] = deepcopy(item["record"])
                state.setdefault("exports", {})[component_id] = record
                return self._write(root, state)

            context = publish_exports(plan, commit=commit)
            return {"project_root": str(root), "revision": context["revision"], "export": context["data"]["exports"][component_id]}

    def get_export(self, project_root: str, export_id: str) -> dict:
        """校验当前导出清单和资产。"""
        from .image_assets import verify_image_record

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
            verify_image_record(item["record"])
        return {"project_root": context["project_root"], "revision": context["revision"], "export": record}

    def get_image(self, project_root, asset_id):
        from .image_assets import verify_image_record
        _identifier(asset_id, "asset_id")
        context = self.context(project_root)
        record = context["data"]["images"].get(asset_id)
        if record is None:
            raise ProjectError(f"图像资产不存在：{asset_id}")
        verify_image_record(record)
        return {"project_root": context["project_root"], "revision": context["revision"], "asset_id": asset_id, "image": record}

    def create_composition(self, project_root: str, expected_revision: str, composition_id: str, width: int,
                           height: int, background: str, nodes: list[dict]) -> dict:
        """以当前资产和明确坐标更新组合预览。"""
        from .compositions import build_composition
        from .image_assets import verify_image_record
        from .visual_checks import RUN_FIELDS

        root = self._root(project_root)
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 64:
            raise ProjectError("nodes 必须包含 1 到 64 项")
        _identifier(composition_id, "composition_id")
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            layout = ProjectLayout(root, state["paths"])
            self._check_id_collision(state["compositions"], composition_id, "composition_id")
            resolved = []
            for index, node in enumerate(nodes):
                if not isinstance(node, dict):
                    raise ProjectError(f"nodes[{index}] 必须是对象")
                kind = node.get("kind")
                common = {"id", "kind", "x", "y"}
                if kind == "component":
                    required = common | {"component_id", "width", "height"}
                    optional = {"region"}
                elif kind == "image":
                    required = common | {"asset_id", "width", "height"}
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
                    _, run, _, snapshot = self._review_run(state, node["component_id"])
                    missing = set(RUN_FIELDS) - run.keys()
                    if missing:
                        raise ProjectError(f"组件运行 {node['component_id']} 缺少字段：{', '.join(sorted(missing))}")
                    self._run_artifact(run, "preview")
                    entry.update(source=run["preview"], sha256=run["artifact_hashes"]["preview"],
                                 run={key: run[key] for key in RUN_FIELDS}, spec=snapshot["spec"])
                elif kind == "image":
                    _identifier(node["asset_id"], f"nodes[{index}].asset_id")
                    record = state["images"].get(node["asset_id"])
                    if record is None:
                        raise ProjectError(f"nodes[{index}] 引用不存在的图像资产：{node['asset_id']}")
                    verify_image_record(record)
                    entry.update(source=record["path"], sha256=record["sha256"])
                else:
                    layout.require_persistent_input(node["font_path"], f"nodes[{index}].font_path")
                if kind in ("component", "image"):
                    entry.setdefault("region", None)
                resolved.append(entry)
            def commit(record):
                record.update(created_at=datetime.now(timezone.utc).isoformat(), project_revision=revision)
                state.setdefault("compositions", {})[composition_id] = record
                return self._write(root, state)

            updated = build_composition(str(root), composition_id, width, height, background, resolved,
                                        paths=state["paths"], commit=commit)
            return self._composition_result(updated, composition_id)

    def save_layout(self, project_root, expected_revision, layout_id, name, width, height, background, nodes,
                    safe_area=None, checks=None, unity_adaptation=None):
        from .page_layouts import validate_definition, resolve_definition
        _identifier(layout_id, "layout_id")
        _text(name, "name")
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            self._check_id_collision(state["layouts"], layout_id, "layout_id")
            definition = validate_definition(width, height, background, nodes, safe_area=safe_area,
                                             checks=checks, unity_adaptation=unity_adaptation)
            resolve_definition(state, definition)
            digest = spec_sha256(definition)
            state.setdefault("layouts", {})[layout_id] = {"id": layout_id, "name": name, "definition": definition, "sha256": digest}
            return self._layout_result(self._write(root, state), layout_id)

    def get_layout(self, project_root, layout_id):
        _identifier(layout_id, "layout_id")
        return self._layout_result(self.context(project_root), layout_id)

    def build_layout(self, project_root, expected_revision, layout_id, *, annotate=True, annotation_device=None):
        from .page_dependencies import asset_source_issues, require_current_page_sources

        _identifier(layout_id, "layout_id")
        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            page = state["layouts"].get(layout_id)
            if page is None:
                raise ProjectError(f"页面布局不存在：{layout_id}")
            require_current_page_sources(state, page, asset_source_issues(state))
            return self._build_layout_locked(root, state, page, annotate=annotate, annotation_device=annotation_device)

    def _build_layout_locked(self, root, state, page, *, annotate=False, annotation_device=None):
        from .page_builds import build_page

        layout_id = page["id"]

        def commit(record):
            record.update(created_at=datetime.now(timezone.utc).isoformat())
            state.setdefault("page_builds", {})[layout_id] = record
            return self._write(root, state)

        context = build_page(root, layout_id, layout_id, page["sha256"], page["definition"], state,
                             commit=commit, annotate=annotate, annotation_device=annotation_device)
        return self._page_build_result(context, layout_id)

    def page_dependencies(self, project_root):
        from .page_dependencies import page_dependencies

        return page_dependencies(self.context(project_root))

    def rebuild_pages(self, project_root, expected_revision, layout_ids):
        from .page_dependencies import asset_source_issues, require_current_page_sources
        from .page_layouts import resolve_definition

        if not isinstance(layout_ids, list) or not layout_ids:
            raise ProjectError("layout_ids 必须是非空的页面布局 ID 列表")
        for layout_id in layout_ids:
            _identifier(layout_id, "layout_ids 中的 ID")
        if len(set(layout_ids)) != len(layout_ids):
            raise ProjectError("layout_ids 不能包含重复的页面布局 ID")

        root = self._root(project_root)
        with self._lock(root):
            state, revision = self._read(root)
            self._revision(root, expected_revision, revision)
            missing = [layout_id for layout_id in layout_ids if layout_id not in state["layouts"]]
            if missing:
                raise ProjectError(f"页面布局不存在：{', '.join(missing)}")
            asset_issues = asset_source_issues(state)
            for layout_id in layout_ids:
                page = state["layouts"][layout_id]
                require_current_page_sources(state, page, asset_issues)
                try:
                    resolve_definition(state, page["definition"])
                except (ValueError, OSError) as error:
                    raise ProjectError(f"页面 {layout_id} 的构建输入未通过预检：{error}") from error

            completed = []
            for layout_id in layout_ids:
                try:
                    result = self._build_layout_locked(root, state, state["layouts"][layout_id])
                except (ValueError, OSError, RuntimeError) as error:
                    raise ProjectError(f"重建页面 {layout_id} 失败；已完成 layout_ids：{completed}；"
                                       f"当前 revision：{revision}；{error}") from error
                completed.append(layout_id)
                revision = result["revision"]
            return {"project_root": str(root), "revision": revision, "completed_layout_ids": completed}

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
        return {"project_root": context["project_root"], "revision": context["revision"], "page_build": deepcopy(record),
                **state["page_status"][build_id]}

    @staticmethod
    def _page_status(state, record, asset_issues):
        from .page_dependencies import matches_component_usage, matches_unity_controls, page_source_issues
        from .page_layouts import image_geometry
        from .typography import style_digest

        def matches_image(node):
            image = state["images"].get(node["asset_id"])
            return (image is not None and image["sha256"] == node["sha256"]
                    and image_geometry(image) == node["geometry"])

        typography = state["typography"]
        def matches_text(node):
            return (typography is not None and node["text_style_id"] in typography["styles"] and
                    style_digest(typography, node["text_style_id"]) == node["text_style_sha256"])

        images = all(matches_image(node) for node in record["nodes"] if node["kind"] == "image")
        text = all(matches_text(node) for node in record["nodes"] if node["kind"] == "text")
        matches_layout = state["layouts"][record["layout_id"]]["sha256"] == record["layout_sha256"]
        issues = page_source_issues(state, record["nodes"], asset_issues)
        usage = matches_component_usage(state, record, asset_issues)
        controls = matches_unity_controls(state, record)
        return {"matches_layout": matches_layout, "matches_images": images, "matches_typography": text,
                "matches_bindings": images and text, "matches_sources": not issues, "source_issues": issues,
                "matches_component_usage": usage, "matches_unity_controls": controls,
                "is_current": matches_layout and images and text and not issues and usage and controls}

    @staticmethod
    def _layout_result(context, layout_id):
        page = context["data"].get("layouts", {}).get(layout_id)
        if page is None:
            raise ProjectError(f"页面布局不存在：{layout_id}")
        return {"project_root": context["project_root"], "revision": context["revision"], "layout": deepcopy(page)}

    def get_composition(self, project_root: str, composition_id: str) -> dict:
        _identifier(composition_id, "composition_id")
        result = self._composition_result(self.context(project_root), composition_id)
        # 校验当前组合图及其输入。
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

    def _review_run(self, state, component_id):
        component = state["components"].get(component_id)
        if component is None:
            raise ProjectError(f"组件不存在：{component_id}")
        run = component["latest_run"]
        if run is None or run["status"] not in ("drawn", "verified"):
            raise ProjectError(f"组件必须有已保存的当前成功产物：{component_id}")
        try:
            plan = json.loads(self._run_artifact(run, "plan_path"))
            snapshot = json.loads(self._run_artifact(run, "style_snapshot_path"))
        except (ValueError, UnicodeError) as error:
            raise ProjectError(f"运行计划或风格快照不是有效 JSON：{component_id}；{error}") from error
        if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("style"), dict)
                or snapshot.get("component_id") != component_id
                or style_sha256(snapshot["style"]) != run["style_sha256"]
                or spec_sha256(snapshot.get("spec")) != run["spec_sha256"]):
            raise ProjectError(f"运行风格快照与记录的风格摘要或组件规格不一致：{component_id}")
        nodes = plan.get("nodes") if isinstance(plan, dict) else None
        rules = snapshot["style"].get("rules")
        if (not isinstance(nodes, list) or any(not isinstance(node, dict) or not isinstance(node.get("key"), str) for node in nodes)
                or not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules)):
            raise ProjectError(f"运行快照缺少有效的节点或风格规则：{component_id}")
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
            self._require_current_comparison(state, comparison_id)
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
            self._require_current_comparison(state, comparison_id)
            update_feedback(state, feedback_id, status, comment, comparison_id, source)
        context = self._update(project_root, expected_revision, change)
        return {"project_root": context["project_root"], "revision": context["revision"],
                "feedback": context["data"]["feedback"][feedback_id]}

    def _require_current_comparison(self, state, comparison_id):
        from .references import _sha256

        comparison = self._context(Path("."), state, "")["data"]["comparisons"][comparison_id]
        if not comparison["is_current"]:
            raise ProjectError("对照已不匹配当前组件或参考，请重新对照后记录反馈")
        self._run_artifact(state["components"][comparison["component_id"]]["latest_run"], "preview")
        reference = state["references"][comparison["reference"]["id"]]
        source = Path(reference["source"])
        try:
            digest = _sha256(source)
        except OSError as error:
            raise ProjectError(f"无法读取当前参考图：{source}；{error}") from error
        if digest != reference["sha256"]:
            raise ProjectError(f"当前参考图 SHA-256 不符：{source}")

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
                    if value["comparison_id"] == comparison_id}
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
        layout = ProjectLayout(root)
        try:
            state = json.loads(layout.project_file.read_bytes())
            if not isinstance(state, dict) or state.get("schema_version") != 5:
                raise ProjectError(f"项目状态必须是 schema_version=5 的对象：{layout.project_file}；已有项目请先显式转换文字规范与页面样式引用")
            if state["paths"] != layout.paths:
                raise ProjectError("项目目录必须使用制作资料的固定结构")
            if "ui_settings" in state:
                _validate_ui_settings(state["ui_settings"])
            for collection, directory in self._collections().items():
                records = {}
                for key, relative in state[collection].items():
                    _identifier(key, collection)
                    self._check_id_collision(records, key, collection)
                    expected = layout.persistent_relative(f"{directory}/{key}.json")
                    if relative != expected:
                        raise ProjectError(f"{collection}.{key} 路径必须为 {expected}")
                    records[key] = json.loads(layout.resolve(relative).read_bytes())
                state[collection] = records
            if "public_image_ids" in state:
                public_ids = state["public_image_ids"]
                if (not isinstance(public_ids, list) or any(not isinstance(key, str) for key in public_ids)
                        or len(public_ids) != len(set(public_ids)) or set(public_ids) - state["images"].keys()):
                    raise ProjectError("公共图素必须使用无重复的已登记资产 ID")
            typography_path = layout.persistent("typography.json")
            if state["typography"] is not None:
                expected = typography_path.relative_to(root).as_posix()
                if state["typography"] != expected:
                    raise ProjectError(f"typography 路径必须为 {expected}")
                state["typography"] = json.loads(typography_path.read_bytes())
            expected_typography_sha = spec_sha256(state["typography"]) if state["typography"] is not None else None
            if state["typography_sha256"] != expected_typography_sha:
                raise ProjectError(f"文字规范摘要不一致：{typography_path}；请通过文字规范工具更新")
            for name, digest in state["document_hashes"].items():
                if name not in layout.documents or _sha(layout.documents[name].read_bytes()) != digest:
                    raise ProjectError(f"受管文档已被修改或缺失：{name}")
            if set(state["document_hashes"]) != set(layout.documents):
                raise ProjectError("项目缺少受管文档摘要")
            self._references(state, state["style"]["reference_ids"], "style.reference_ids")
            self._rule_references(state, state["style"]["rules"])
            self._validate_programs(layout, state["programs"])
            from .component_usage import validate_usage
            for component_id, component in state["components"].items():
                self._component_program(state, component["spec"])
                if "usage" in component["spec"]:
                    try:
                        validate_usage(component["spec"]["usage"])
                    except ValueError as error:
                        raise ProjectError(f"组件 {component_id}：{error}") from error
            for key, page in state["layouts"].items():
                if page["sha256"] != spec_sha256(page["definition"]):
                    raise ProjectError(f"页面布局定义摘要不一致：{key}")
            resolved = map_state_paths(state, layout.resolve)
            if resolved["typography"] is not None:
                from .typography import validate_typography
                validate_typography(resolved["typography"], root, resolved["references"], verify_fonts=False)
            from .page_layouts import iter_layout_nodes, validate_definition
            for key, page in resolved["layouts"].items():
                validate_definition(**page["definition"])
                for node in iter_layout_nodes(page["definition"]["nodes"]):
                    if node["kind"] == "text" and (resolved["typography"] is None or
                            node["text_style_id"] not in resolved["typography"]["styles"]):
                        raise ProjectError(f"页面 {key} 节点 {node['id']} 引用不存在的文字样式：{node['text_style_id']}")
            from .page_builds import validate_build_record
            for record in resolved.get("page_builds", {}).values():
                validate_build_record(record)
            resolved["unity_controls"] = self._read_unity_controls(layout, resolved)
            return resolved, spec_sha256(state)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProjectError(f"无法读取项目状态：{root}；{error}") from error

    @staticmethod
    def _read_unity_controls(layout, state):
        from .unity import REGISTRY, _fields
        from .unity_builds import read_construction
        from .unity_recipes import resolve_recipes

        construction, _ = read_construction(layout.root, layout=layout)
        recipes = construction["recipes"]
        if not recipes:
            return {}
        registry = json.loads(layout.persistent(REGISTRY).read_bytes())
        _fields(registry, {"schema_version", "controls", "handoffs", "document_sha256"}, REGISTRY)
        if type(registry["schema_version"]) is not int or registry["schema_version"] != 1:
            raise ProjectError(f"{REGISTRY} 只接受 schema_version=1")
        if not isinstance(registry["controls"], dict):
            raise ProjectError(f"{REGISTRY}.controls 必须是对象")
        definitions = {}
        for control_id, record in registry["controls"].items():
            _fields(record, {"definition", "prefab"}, f"控件 {control_id}")
            definitions[control_id] = record["definition"]
        return resolve_recipes(definitions, recipes, state)

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
    def _program_path(layout, relative):
        try:
            path = layout.resolve(relative)
            directory = layout.directory("programs")
            if Path(relative).is_absolute() or not path.is_relative_to(directory):
                raise ProjectError(f"程序源码路径必须位于项目 programs 目录：{relative}")
            return path
        except ValueError as error:
            raise ProjectError(str(error)) from error

    def _validate_programs(self, layout, programs):
        for key, program in programs.items():
            _identifier(key, "program_id")
            expected = f"{layout.paths['programs']}/{key}.py"
            if program.get("id") != key or program.get("source_path") != expected:
                raise ProjectError(f"程序路径或标识不一致：{key}")
            if _sha(self._program_path(layout, expected).read_bytes()) != program["sha256"]:
                raise ProjectError(f"程序源码已被手动修改，摘要不一致：{expected}")

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
        from .page_dependencies import asset_source_issues, component_needs_review
        from .page_layouts import iter_page_preview_nodes

        data = deepcopy(state)
        current_style_sha256 = style_sha256(data["style"])

        def matches_current_preview(component_id, run_id, sha256):
            component = data["components"][component_id]
            latest = component["latest_run"]
            return bool(latest and not component["needs_review"] and latest["run_id"] == run_id
                        and latest["artifact_hashes"].get("preview") == sha256)

        for component_id, component in data["components"].items():
            component["needs_review"] = component_needs_review(data, component, current_style_sha256)
            component["open_feedback_count"] = sum(
                item["status"] == "open" and data["comparisons"][item["comparison_id"]]["component_id"] == component_id
                for item in data.get("feedback", {}).values())
        for comparison in data.get("comparisons", {}).values():
            reference, artifact = comparison["reference"], comparison["artifact"]
            current_reference = data["references"].get(reference["id"])
            comparison["is_current"] = bool(current_reference and current_reference["sha256"] == reference["sha256"]
                and matches_current_preview(comparison["component_id"], artifact["run_id"], artifact["sha256"]))
        for check in data.get("visual_checks", {}).values():
            for item in check["items"]:
                item["is_current"] = matches_current_preview(item["component_id"], item["run"]["run_id"], item["sha256"])
            check["is_current"] = all(item["is_current"] for item in check["items"])
        for feedback in data.get("feedback", {}).values():
            comparison = data["comparisons"][feedback["comparison_id"]]
            reference = comparison["reference"]
            feedback["applies_to_current_run"] = (
                comparison["is_current"] and feedback["reviewed_artifact_sha256"] == comparison["artifact"]["sha256"]
                and feedback["reference_sha256"] == reference["sha256"]
                and feedback["reference_region_source_xywh"] == reference["region_source_xywh"])
        for composition in data.get("compositions", {}).values():
            for node in composition["nodes"]:
                if node["kind"] == "image":
                    asset = data["images"][node["asset_id"]]
                    node["is_current"] = node["sha256"] == asset["sha256"]
                elif node["kind"] == "component":
                    node["is_current"] = matches_current_preview(node["component_id"], node["run"]["run_id"], node["sha256"])
            composition["is_current"] = all(node["is_current"] for node in composition["nodes"] if node["kind"] != "text")
        usage = {key: [] for key in data["typography"]["styles"]} if data["typography"] is not None else {}
        for layout_id, page in data.get("layouts", {}).items():
            for node in iter_page_preview_nodes(data, page["definition"]["nodes"]):
                if node["kind"] == "text":
                    usage[node["text_style_id"]].append({"layout_id": layout_id, "node_id": node["id"]})
        data["typography_usage"] = usage
        asset_issues = asset_source_issues(state)
        data["page_status"] = {key: ProjectStore._page_status(state, record, asset_issues)
                               for key, record in data.get("page_builds", {}).items()}
        return {"project_root": str(root), "revision": revision, "data": data}

    def _render_for_root(self, root, state):
        layout = ProjectLayout(root, state["paths"])
        display = map_state_paths(state, layout.document_link)
        for program in display["programs"].values():
            program["source_path"] = layout.document_link(program["source_path"])
        return self._render(display)

    def _render(self, state):
        from .typography import render_typography

        style = state["style"]
        lines = [f"# {state['name']}：当前风格规范", "", style["description"] or "尚未填写风格说明。", "",
                 "## 共用参数", "", "```json", _json(style["parameters"]), "```", "", "## 风格规则", ""]
        for rule in style["rules"]:
            lines.extend([f"### {rule.get('id', '规则')}", "", self._describe(rule), ""])
        lines.extend(["## 参考", ""])
        for key, reference in state["references"].items():
            lines.extend([f"- {key}：{self._link('原图', reference['source'])}"])
        lines.extend(["", "本文由当前项目状态生成；技术检查不表示用户美术确认。", ""])
        components = [f"# {state['name']}：当前组件与资产", "", "历史由 Git 管理。本文由工具生成。", ""]
        for key, component in self._context(Path('.'), state, '')["data"]["components"].items():
            components.extend([f"## {component['name']}（{key}）", "",
                "需要复查：" + ("是" if component["needs_review"] else "否") + "。用户美术确认按项目记录处理。", "",
                "```json", _json(component["spec"]), "```", ""])
            if component["latest_run"]:
                run = component["latest_run"]
                components.extend([self._link("可编辑源文件", run["document"]), "", self._link("当前预览", run["preview"]), ""])
        for key, program in state["programs"].items():
            components.extend([f"- 程序 {key}：{self._link(program['name'], program['source_path'])}"])
        for feedback in state.get("feedback", {}).values():
            components.extend(["", f"- 偏差 {feedback['id']}（{feedback['status']}）：{feedback['observation']}；{feedback['suggested_change']}"])
        return {"style.md": ("\n".join(lines) + "\n").encode("utf-8"),
                "components.md": ("\n".join(components) + "\n").encode("utf-8"),
                "typography.md": render_typography(state["name"], state["typography"])}

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

    @staticmethod
    def _check_id_collision(records, identifier, field):
        for existing in records:
            if existing != identifier and existing.casefold() == identifier.casefold():
                raise ProjectError(f"{field} {identifier} 与已有标识 {existing} 仅大小写不同")

    def _write(self, root, state, artifacts=None):
        layout = ProjectLayout(root, state["paths"])
        persistent = {key: value for key, value in state.items() if key != "unity_controls"}
        try:
            _json(persistent)
        except (ValueError, TypeError) as error:
            raise ProjectError(f"项目包含无法写入的 JSON 数据：{error}") from error
        stored = map_state_paths(persistent, layout.relative)
        stored["typography_sha256"] = spec_sha256(stored["typography"]) if stored["typography"] is not None else None
        documents = self._render_for_root(root, state)
        stored["document_hashes"] = {name: _sha(payload) for name, payload in documents.items()}
        index = deepcopy(stored)
        files = dict(artifacts or {})
        if stored["typography"] is not None:
            path = layout.persistent("typography.json")
            index["typography"] = path.relative_to(root).as_posix()
            files[path] = (_json(stored["typography"]) + "\n").encode("utf-8")
        attributes = layout.persistent(".gitattributes")
        existing = attributes.read_bytes() if attributes.is_file() else b""
        if existing.rstrip().splitlines()[-1:] != [b"* -text"]:
            separator = b"" if not existing or existing.endswith(b"\n") else b"\n"
            rule = "# 保留 SmartUI 校验摘要所使用的文件字节\n* -text\n".encode("utf-8")
            files[attributes] = existing + separator + rule
        for collection, directory in self._collections().items():
            index[collection] = {}
            for key, record in stored.get(collection, {}).items():
                path = layout.persistent(f"{directory}/{key}.json")
                index[collection][key] = path.relative_to(root).as_posix()
                files[path] = (_json(record) + "\n").encode("utf-8")
            stored.setdefault(collection, {})
        for name, payload in documents.items():
            files[layout.documents[name]] = payload
        files[layout.project_file] = (_json(index) + "\n").encode("utf-8")
        resolved = map_state_paths(stored, layout.resolve)
        resolved["unity_controls"] = state.get("unity_controls", {})
        context = self._context(root, resolved, spec_sha256(stored))
        self._publish_batch(layout, files)
        return context

    @staticmethod
    def _collections():
        return {"components": "components", "layouts": "layouts", "images": "sources/images", "programs": "programs"}

    @staticmethod
    def _publish_batch(layout, files):
        """先校验并备齐整批文件，发布失败后恢复已替换的文件。"""
        root = layout.root
        persistent = root / layout.data_root
        migration_backups = root / SCHEMA_MIGRATION_BACKUPS
        temporary = root / ".smartui/temp"
        pending = {}
        try:
            for destination, content in files.items():
                layout._check_managed_path(destination)
                if not any(directory in destination.parents for directory in (persistent, migration_backups)):
                    raise ProjectError(f"项目发布路径必须位于 {layout.data_root}/ 或 {SCHEMA_MIGRATION_BACKUPS}/：{destination}")
                if destination.exists() and not destination.is_file():
                    raise ProjectError(f"项目发布路径已存在但不是文件：{destination}")
                for parent in destination.parents:
                    if parent == root:
                        break
                    if parent.exists() and not parent.is_dir():
                        raise ProjectError(f"项目发布目录被文件占用：{parent}")
                if not destination.is_file() or destination.read_bytes() != content:
                    pending[destination] = content
            if not pending:
                return
            layout._check_managed_path(temporary)
            temporary.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=temporary) as transaction:
                staged = {}
                for index, (destination, content) in enumerate(pending.items()):
                    prepared = Path(transaction) / f"{index}.new"
                    backup = None
                    if destination.is_file():
                        backup = Path(transaction) / f"{index}.old"
                        shutil.copyfile(destination, backup)
                    with prepared.open("xb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    staged[destination] = (prepared, backup)

                published = []
                directories = []
                try:
                    for destination, (prepared, _) in staged.items():
                        missing = []
                        parent = destination.parent
                        while not parent.exists():
                            missing.append(parent)
                            parent = parent.parent
                        for directory in reversed(missing):
                            directory.mkdir()
                            directories.append(directory)
                        os.replace(prepared, destination)
                        published.append(destination)
                except OSError as error:
                    failures = []
                    for target in reversed(published):
                        _, backup = staged[target]
                        try:
                            if backup is None:
                                target.unlink()
                            else:
                                os.replace(backup, target)
                        except OSError as recovery_error:
                            failures.append(f"{target}：{recovery_error}")
                    for directory in reversed(directories):
                        try:
                            directory.rmdir()
                        except OSError as recovery_error:
                            failures.append(f"{directory}：{recovery_error}")
                    if failures:
                        details = "；".join(failures)
                        raise ProjectError(f"项目文件写入失败：{destination}；{error}。恢复失败：{details}") from error
                    raise ProjectError(f"项目文件写入失败：{destination}；{error}。已恢复先前状态") from error
        except (OSError, ValueError) as error:
            raise ProjectError(f"项目文件写入失败：{error}") from error
