"""显式复制 schema 2 数据目录到 schema 3 工程布局；始终保留原目录。"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .projects import ProjectError, ProjectStore, _identifier


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


@dataclass
class MigrationPlan:
    source_root: Path
    project_root: Path
    files: dict[Path, bytes] = field(repr=False)
    source_hashes: dict[Path, str] = field(repr=False)
    state: dict = field(repr=False)

    def report(self):
        return {
            "source_root": str(self.source_root), "project_root": str(self.project_root),
            "schema_version": 3, "applied": False, "source_retained": True,
            "files": [{"path": str(path), "sha256": _sha(payload), "bytes": len(payload)}
                      for path, payload in sorted(self.files.items())],
        }


def _plain_path(path):
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ProjectError(f"迁移路径不能包含符号链接或目录联接：{parent}")


def _inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        _plain_path(path)
        if path.is_file():
            result[path] = path.read_bytes()
    return result


def _target(source, source_root, project_root):
    relative = source.relative_to(source_root)
    head, *tail = relative.parts
    if relative.as_posix() == "project.json":
        return project_root / ".smartui" / "project.json"
    directories = {"programs": "art/ui/programs", "assets": "art/ui/sources",
                   ".smartui": ".smartui"}
    if head in directories:
        return project_root / directories[head] / Path(*tail)
    return project_root / "docs/ui" / relative


def _check_destinations(plan):
    for path in plan.files:
        _plain_path(path)
        if path.is_relative_to(plan.source_root) or plan.source_root.is_relative_to(path):
            raise ProjectError(f"迁移目标不能与旧数据目录重叠：{path}")
        if path.exists():
            raise ProjectError(f"迁移不能覆盖已有目标：{path}")
        for parent in path.parents:
            if parent.exists() and not parent.is_dir():
                raise ProjectError(f"迁移目标的父路径不是目录：{parent}")
            if parent == plan.project_root:
                break


def _markdown_links(text, old_document, new_document, mapping, source_root, project_root):
    def destination(raw):
        angle = raw.startswith("<") and raw.endswith(">")
        value = raw[1:-1] if angle else raw
        parts = urlsplit(value)
        local = re.split(r"[?#]", value, maxsplit=1)[0]
        absolute = Path(local).is_absolute()
        if absolute:
            old = Path(local).resolve()
            if old not in mapping:
                return raw
            suffix = value[len(local):]
        elif parts.scheme or parts.netloc or not parts.path:
            return raw
        else:
            old = (old_document.parent / unquote(parts.path)).resolve()
            suffix = ("?" + parts.query if parts.query else "") + ("#" + parts.fragment if parts.fragment else "")
        target = mapping.get(old)
        if target is None:
            target = _target(old, source_root, project_root) if old.is_relative_to(source_root) else old
        relative = Path(os.path.relpath(target, new_document.parent)).as_posix()
        result = relative + suffix
        return "<" + result + ">" if angle or " " in result else result

    # 行内链接和图片，以及 Markdown 的引用式链接定义；保留可选标题。
    text = re.sub(r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^\s()]+)([^\n)]*\))",
                  lambda match: match[1] + destination(match[2]) + match[3], text)
    return re.sub(r"(?m)^(\s{0,3}\[[^\]\n]+\]:\s*)(<[^>\n]+>|\S+)(.*)$",
                  lambda match: match[1] + destination(match[2]) + match[3], text)


def plan_layout_migration(source_root, project_root):
    """完整预检并生成内存计划；不创建目录、备份或文件。"""
    try:
        return _plan_layout_migration(source_root, project_root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ProjectError(f"无法迁移旧项目，配置或资源无效：{source_root}；{error}") from error


def _plan_layout_migration(source_root, project_root):
    from .layout import DEFAULT_PATHS, IGNORE_RULES, map_state_paths

    source, root = Path(source_root), Path(project_root)
    if not source.is_absolute() or not root.is_absolute():
        raise ProjectError("source_root 和 project_root 必须是绝对路径")
    _plain_path(source)
    _plain_path(root)
    source, root = source.resolve(strict=True), root.resolve()
    if not source.is_dir() or source == root or root.is_relative_to(source):
        raise ProjectError("旧数据根必须是独立目录，不能包含目标工程根")
    if (source / ".smartui-project.lock").exists():
        raise ProjectError(f"旧项目正在使用或遗留锁文件：{source / '.smartui-project.lock'}")
    originals = _inventory(source)
    try:
        state = json.loads(originals[source / "project.json"])
    except (KeyError, ValueError) as error:
        raise ProjectError(f"旧项目缺少有效的 project.json：{source}；{error}") from error
    if not isinstance(state, dict) or state.get("schema_version") != 2:
        raise ProjectError("此迁移仅支持 schema_version=2 的旧数据目录")
    for name in ("references", "programs", "style", "components"):
        if not isinstance(state.get(name), dict):
            raise ProjectError(f"旧项目缺少有效的 {name} 对象：{source / 'project.json'}")
    for name in ("name", "project_id"):
        if not isinstance(state.get(name), str) or not state[name].strip():
            raise ProjectError(f"旧项目缺少有效的 {name}：{source / 'project.json'}")
    hashes = state.get("document_hashes")
    if not isinstance(hashes, dict) or set(hashes) != {"style.md", "components.md"}:
        raise ProjectError("旧项目缺少完整的受管文档摘要")
    for name, expected in hashes.items():
        if source / name not in originals or _sha(originals[source / name]) != expected:
            raise ProjectError(f"受管文档缺失或已被手动修改：{source / name}")
    mapping = {path: _target(path, source, root) for path in originals}

    # 原图和已登记预览可能存于旧目录之外；复制它们，不移动用户原文件。
    for identifier, reference in state["references"].items():
        _identifier(identifier, "reference_id")
        for key in ("source", "preview"):
            if not reference.get(key):
                continue
            path = Path(reference[key])
            path = path if path.is_absolute() else source / path
            _plain_path(path)
            path = path.resolve()
            if not path.is_file():
                raise ProjectError(f"参考资料文件缺失：{path}")
            if key == "source" and reference.get("sha256") != _sha(path.read_bytes()):
                raise ProjectError(f"参考资料摘要不符：{path}")
            if path not in mapping:
                originals[path] = path.read_bytes()
                mapping[path] = (root / "docs/ui/references" / identifier / path.name if key == "source"
                                 else root / ".smartui/cache" / f"{identifier}-{path.name}")

    def transform(value):
        path = Path(value)
        old = path.resolve() if path.is_absolute() else (source / path).resolve()
        if old in mapping:
            return str(mapping[old])
        if old.is_relative_to(source):
            raise ProjectError(f"项目记录引用的文件缺失：{old}")
        return str(old)

    def verify(value, expected):
        path = Path(value)
        path = path if path.is_absolute() else source / path
        _plain_path(path)
        if not path.is_file() or _sha(path.read_bytes()) != expected:
            raise ProjectError(f"已登记文件缺失或摘要不符：{path}")

    for component in state["components"].values():
        for run in component["runs"]:
            for key, expected in run.get("artifact_hashes", {}).items():
                # 多次修改共用 document；历史摘要对应当时版本，不能拿当前文件校验。
                if key != "document":
                    verify(run[key], expected)
        latest = component["latest_run"]
        if latest is not None and "document" in latest.get("artifact_hashes", {}):
            verify(latest["document"], latest["artifact_hashes"]["document"])
    for records in (state.get("comparisons", {}), state.get("visual_checks", {})):
        for record in records.values():
            for image in record["images"].values():
                verify(image["path"], image["sha256"])

    migrated = map_state_paths(state, transform)
    migrated["schema_version"] = 3
    migrated["paths"] = dict(DEFAULT_PATHS)
    for identifier, program in migrated["programs"].items():
        _identifier(identifier, "program_id")
        version, history = program.get("version"), program.get("history")
        if (program.get("id") != identifier or not isinstance(program.get("name"), str) or not program["name"].strip()
                or not isinstance(program.get("description"), str) or type(version) is not int or version < 1
                or not isinstance(history, list) or len(history) != version):
            raise ProjectError(f"旧项目程序元数据无效：{identifier}")
        if any(program.get(key) != history[-1].get(key) for key in ("version", "sha256", "source_path")):
            raise ProjectError(f"当前程序与最新历史记录不一致：{identifier}")
        for index, item in enumerate(program["history"], 1):
            if item["version"] != index or item["source_path"] != f"programs/{identifier}/{index}.py":
                raise ProjectError(f"旧程序历史路径无效：{item['source_path']}")
        for item in [program, *program["history"]]:
            old = (source / item["source_path"]).resolve()
            if old not in originals or _sha(originals[old]) != item["sha256"]:
                raise ProjectError(f"程序源码缺失或摘要不符：{old}")
            item["source_path"] = mapping[old].relative_to(root).as_posix()
    store = ProjectStore()
    store._references(migrated, migrated["style"]["reference_ids"], "style.reference_ids")
    store._rule_references(migrated, migrated["style"]["rules"])
    for component in migrated["components"].values():
        store._component_program(migrated, component["spec"])

    def prose(value):
        if isinstance(value, str):
            return _markdown_links(value, source / "style.md", root / "docs/ui/style.md", mapping, source, root)
        if isinstance(value, list):
            return [prose(item) for item in value]
        if isinstance(value, dict):
            return {key: prose(item) for key, item in value.items()}
        return value

    migrated["style"]["description"] = prose(migrated["style"]["description"])
    migrated["style"]["rules"] = prose(migrated["style"]["rules"])

    files = {}
    for old, target in mapping.items():
        if target in files:
            raise ProjectError(f"多个源文件对应同一迁移目标：{target}")
        payload = originals[old]
        if old.suffix.lower() == ".md" and old not in {source / "style.md", source / "components.md"}:
            payload = _markdown_links(payload.decode("utf-8"), old, target, mapping, source, root).encode("utf-8")
        files[target] = payload
    ignore_path = root / ".smartui/.gitignore"
    existing = files.get(ignore_path, b"").decode("utf-8")
    missing = [rule for rule in IGNORE_RULES.splitlines() if rule not in existing.splitlines()]
    files[ignore_path] = (existing + ("\n" if existing and not existing.endswith("\n") else "")
                          + "\n".join(missing) + ("\n" if missing else "")).encode("utf-8")
    documents = ProjectStore()._render_for_root(root, migrated)
    for filename, payload in documents.items():
        files[root / "docs/ui" / filename] = payload
    migrated["document_hashes"] = {name: _sha(payload) for name, payload in documents.items()}

    def relative(value):
        path = Path(value)
        return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)

    encoded = map_state_paths(migrated, relative)
    files[root / ".smartui/project.json"] = _json(encoded)
    plan = MigrationPlan(source, root, files, {path: _sha(payload) for path, payload in originals.items()}, migrated)
    _check_destinations(plan)
    return plan


def apply_layout_migration(plan):
    """先校验备份，再创建新布局；不删除或修改任何旧文件。"""
    _check_destinations(plan)
    lock = plan.source_root / ".smartui-project.lock"
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise ProjectError(f"旧项目正在使用：{lock}") from error
    try:
        with stream, ProjectStore()._lock(plan.project_root):
            return _apply_locked(plan, lock)
    finally:
        lock.unlink()


def _apply_locked(plan, source_lock):
    _check_destinations(plan)
    if {path for path in _inventory(plan.source_root) if path != source_lock} != {
            path for path in plan.source_hashes if path.is_relative_to(plan.source_root)}:
        raise ProjectError("预检后旧数据目录的文件清单已变化，请重新生成迁移计划")
    for path, expected in plan.source_hashes.items():
        if _sha(path.read_bytes()) != expected:
            raise ProjectError(f"预检后源文件已变化，请重新生成迁移计划：{path}")
    backup = plan.project_root / ".smartui/migration-backups" / uuid4().hex
    _plain_path(backup)
    backup.mkdir(parents=True)
    backup_hashes = {}
    for path, expected in plan.source_hashes.items():
        relative = Path("source") / path.relative_to(plan.source_root) if path.is_relative_to(plan.source_root) else Path("external") / _sha(str(path).encode()) / path.name
        target = backup / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(path.read_bytes())
        if _sha(target.read_bytes()) != expected:
            raise ProjectError(f"迁移备份摘要不符：{target}")
        backup_hashes[relative.as_posix()] = expected
    (backup / "backup-manifest.json").write_bytes(_json(backup_hashes))
    # project.json 最后创建；发生写入错误时保留备份与现场，不尝试覆盖或删除。
    project_file = plan.project_root / ".smartui/project.json"
    ordered = [path for path in plan.files if path != project_file] + [project_file]
    for path in ordered:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(plan.files[path])
        if _sha(path.read_bytes()) != _sha(plan.files[path]):
            raise ProjectError(f"迁移目标摘要不符：{path}")
    _, revision = ProjectStore()._read(plan.project_root)
    report = {**plan.report(), "applied": True, "backup": str(backup), "revision": revision,
              "historical_call_records_preserved": True}
    (backup / "migration-report.json").write_bytes(_json(report))
    return report


def migrate_project_layout(source_root, project_root, *, apply=False):
    plan = plan_layout_migration(source_root, project_root)
    return apply_layout_migration(plan) if apply else plan.report()
