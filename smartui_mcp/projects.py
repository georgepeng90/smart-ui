"""保存项目事实，并从同一份状态生成风格和组件文档。"""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4


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
    def create(self, project_root: str, project_id: str, name: str) -> dict:
        root = self._root(project_root)
        _identifier(project_id, "project_id")
        _text(name, "name")
        root.mkdir(parents=True, exist_ok=True)
        with self._lock(root):
            for filename in ("project.json", "style.md", "components.md"):
                if (root / filename).exists():
                    raise ProjectError(f"创建项目不能覆盖已有文件：{root / filename}")
            state = {
                "schema_version": 2, "project_id": project_id, "name": name,
                "references": {}, "programs": {},
                "style": {"version": 0, "description": "", "rules": [], "parameters": {},
                          "reference_ids": [], "user_confirmation": None},
                "components": {},
            }
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
                relative = f"programs/{program_id}/{version}.py"
                path = self._program_path(root, relative)
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path = self._program_path(root, relative)
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
                payload = self._program_path(root, program["source_path"]).read_bytes()
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

    @staticmethod
    def _root(project_root):
        if not isinstance(project_root, str) or not project_root.strip() or not Path(project_root).is_absolute():
            raise ProjectError("project_root 必须是绝对路径")
        return Path(project_root).resolve()

    @staticmethod
    @contextmanager
    def _lock(root):
        path = root / ".smartui-project.lock"
        try:
            stream = path.open("x", encoding="utf-8")
        except FileExistsError as error:
            raise ProjectError(f"项目正在被其他操作使用；如进程已退出，请检查并移除锁文件：{path}") from error
        except OSError as error:
            raise ProjectError(f"无法获取项目锁：{path}；{error}") from error
        try:
            with stream:
                stream.write(str(os.getpid()))
                stream.flush()
                yield
        finally:
            path.unlink()

    def _read(self, root):
        path = root / "project.json"
        try:
            payload = path.read_bytes()
            def reject_constant(value):
                raise ValueError(f"非法 JSON 数值：{value}")
            state = json.loads(payload, parse_constant=reject_constant)
            if not isinstance(state, dict) or state.get("schema_version") != 2:
                raise ProjectError(f"不支持的项目结构：{path}")
            for field in ("project_id", "name", "references", "style", "components", "programs"):
                if field not in state:
                    raise ProjectError(f"项目缺少 {field}：{path}")
            hashes = state.get("document_hashes")
            if not isinstance(hashes, dict) or set(hashes) != {"style.md", "components.md"}:
                raise ProjectError(f"项目缺少受管文档摘要：{path}")
            for filename, expected in hashes.items():
                document = root / filename
                if _sha(document.read_bytes()) != expected:
                    raise ProjectError(f"受管文档已被手动修改，请先处理差异：{document}")
            self._references(state, state["style"]["reference_ids"], "style.reference_ids")
            self._rule_references(state, state["style"]["rules"])
            self._validate_programs(root, state["programs"])
            for component in state["components"].values():
                self._component_program(state, component["spec"])
            return state, _sha(payload)
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
    def _program_path(root, relative):
        path = Path(relative)
        if path.is_absolute() or not path.parts or path.parts[0] != "programs" or ".." in path.parts:
            raise ProjectError(f"程序源码路径必须位于项目 programs 目录：{relative}")
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root / "programs"):
            raise ProjectError(f"程序源码路径越出项目 programs 目录：{relative}")
        # 版本路径不得通过链接指向另一个位置，即使链接目标仍在项目目录内。
        for parent in (root / path, *(root / path).parents):
            if parent == root:
                break
            if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                raise ProjectError(f"程序源码路径不能包含符号链接或目录联接：{parent}")
        return resolved

    def _validate_programs(self, root, programs):
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
                expected_path = f"programs/{program_id}/{index}.py"
                if (not isinstance(item, dict) or type(item.get("version")) is not int or
                        item["version"] != index or item.get("source_path") != expected_path):
                    raise ProjectError(f"程序历史路径或版本无效：{program_id} 第 {index} 版")
                if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                    raise ProjectError(f"程序源码摘要无效：{expected_path}")
                path = self._program_path(root, expected_path)
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
        for component in data["components"].values():
            latest = component["latest_run"]
            program = data["programs"][component["spec"]["program_id"]]
            component["needs_review"] = (latest is None or
                bool(component["runs"] and component["runs"][-1]["status"] == "failed") or
                latest["style_version"] != data["style"]["version"] or
                latest["spec_sha256"] != spec_sha256(component["spec"]) or
                latest.get("program_id") != program["id"] or
                latest.get("program_version") != program["version"] or
                latest.get("program_sha256") != program["sha256"])
        return {"project_root": str(root), "revision": revision, "data": data}

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
                            "完整结构化记录保存在 project.json；本文档由工具生成。", ""])
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
        if not context["components"]:
            component_lines.extend(["尚未定义组件。", ""])
        component_lines.extend(["完整规格、产物摘要和检查数据保存在 project.json；本文档由工具生成。", ""])
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
            documents = self._render(state)
            state["document_hashes"] = {filename: _sha(payload) for filename, payload in documents.items()}
            payload = (_json(state) + "\n").encode("utf-8")
        except (ValueError, TypeError) as error:
            raise ProjectError(f"项目包含无法写入的 JSON 数据：{error}") from error
        # 先生成完整临时文件；最后替换 project.json，作为本次多文件写入的提交记录。
        pending = []
        try:
            for filename, content in {**documents, "project.json": payload}.items():
                temporary = root / f".{filename}.{uuid4().hex}.tmp"
                with temporary.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                pending.append((temporary, root / filename))
            for temporary, destination in pending:
                os.replace(temporary, destination)
        except OSError as error:
            raise ProjectError(f"项目写入失败，请检查磁盘及项目文件；未自动覆盖或修复：{root}；{error}") from error
        return self._context(root, state, _sha(payload))
