"""工程内的制作目录，以及持久记录与运行时文件路径之间的转换。"""

from copy import deepcopy
import os
from pathlib import Path, PurePosixPath, PureWindowsPath


DEFAULT_PATHS = {
    "documents": "docs/ui",
    "references": "docs/ui/references",
    "programs": "art/ui/programs",
    "sources": "art/ui/sources",
}

IGNORE_RULES = "# SmartUI 可重建缓存、本机锁和迁移备份\ncache/\nproject.lock\n.*.tmp\nmigration-backups/\n"

RUN_PATH_FIELDS = (
    "document", "preview", "operations_path", "style_snapshot_path",
    "program_snapshot_path", "plan_path", "source_snapshot_path",
)


class ProjectLayout:
    def __init__(self, root: Path, paths: dict | None = None):
        self.root = Path(root).resolve()
        if paths is not None and (not isinstance(paths, dict) or paths.keys() - DEFAULT_PATHS.keys()):
            raise ValueError("paths 必须是对象，且只能配置 documents、references、programs、sources")
        self.paths = {**DEFAULT_PATHS, **(paths if paths is not None else {})}
        for key, value in self.paths.items():
            self.paths[key] = self._relative_directory(value, f"paths.{key}")
        directories = {key: self.root / value for key, value in self.paths.items()}
        for key, path in directories.items():
            if path.is_relative_to(self.root / ".smartui"):
                raise ValueError(f"paths.{key} 不能放在工具记录目录 .smartui 中：{path}")
            for other, other_path in directories.items():
                if key == other or (key == "references" and other == "documents"):
                    continue
                if path.is_relative_to(other_path):
                    raise ValueError(f"paths.{key} 与 paths.{other} 的目录重叠：{path}")

    @staticmethod
    def _relative_directory(value, field):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 必须是工程内的非空相对路径")
        path = PurePosixPath(value.replace("\\", "/"))
        if (path.is_absolute() or PureWindowsPath(value).drive or
                not path.parts or ".." in path.parts):
            raise ValueError(f"{field} 必须是工程内相对路径，不能包含 ..：{value}")
        return path.as_posix()

    def directory(self, key):
        path = self.root / self.paths[key]
        self._check_managed_path(path)
        return path

    def _check_managed_path(self, path):
        for parent in (path, *path.parents):
            if parent == self.root:
                break
            if parent.is_symlink() or parent.is_junction():
                raise ValueError(f"受管路径不能包含符号链接或目录联接：{parent}")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError(f"受管路径越出工程根目录：{path}")

    @property
    def project_file(self):
        path = self.root / ".smartui/project.json"
        self._check_managed_path(path)
        return path

    @property
    def documents(self):
        paths = {name: self.directory("documents") / name for name in ("style.md", "components.md")}
        for path in paths.values():
            self._check_managed_path(path)
        return paths

    def resolve(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError(f"文件路径必须是非空字符串：{value!r}")
        path = Path(value)
        if path.is_absolute():
            return path.resolve()
        relative = self._relative_directory(value, "项目文件路径")
        path = self.root / relative
        self._check_managed_path(path)
        return path.resolve()

    def relative(self, value):
        path = self.resolve(str(value))
        return path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)

    def document_link(self, value):
        path = self.resolve(str(value))
        if path.is_relative_to(self.root):
            return Path(os.path.relpath(path, self.directory("documents"))).as_posix()
        return path.as_posix()

    def ensure_gitignore(self):
        path = self.project_file.parent / ".gitignore"
        self._check_managed_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        missing = [rule for rule in IGNORE_RULES.splitlines() if rule not in existing.splitlines()]
        if missing:
            prefix = existing + ("\n" if existing and not existing.endswith("\n") else "")
            path.write_text(prefix + "\n".join(missing) + "\n", encoding="utf-8")


def map_state_paths(state, transform):
    """只转换协议定义的文件字段，不改变项目程序、规格、参数或反馈正文。"""
    result = deepcopy(state)

    def fields(record, names):
        for name in names:
            if name in record and record[name] is not None:
                record[name] = str(transform(record[name]))

    mapped_runs = set()

    def run_paths(run):
        if id(run) in mapped_runs:
            return
        mapped_runs.add(id(run))
        fields(run, RUN_PATH_FIELDS)
        for inspection in run.get("inspection", {}).values():
            if isinstance(inspection, dict):
                fields(inspection, ("path",))

    for reference in result["references"].values():
        fields(reference, ("source", "preview"))
    for component in result["components"].values():
        for run in component["runs"]:
            run_paths(run)
        if component["latest_run"] is not None:
            run_paths(component["latest_run"])
    for comparison in result.get("comparisons", {}).values():
        fields(comparison["reference"], ("source",))
        fields(comparison["artifact"], ("source",))
        for image in comparison["images"].values():
            fields(image, ("path",))
    for check in result.get("visual_checks", {}).values():
        for item in check["items"]:
            fields(item, ("source",))
        for image in check["images"].values():
            fields(image, ("path",))
    for asset in result.get("images", {}).values():
        for version in asset["versions"]:
            fields(version, ("path",))
    for composition in result.get("compositions", {}).values():
        for node in composition["nodes"]:
            fields(node, ("source", "font_path"))
        for item in [*composition["images"].values(), *composition["fonts"].values()]:
            fields(item, ("path",))
    for export in result.get("exports", {}).values():
        fields(export["manifest"], ("path",))
        for item in export["items"]:
            fields(item["record"], ("path",))
    for page in result.get("layouts", {}).values():
        for version in page["versions"]:
            for node in version["definition"]["nodes"]:
                if node["kind"] == "text":
                    fields(node, ("font_path",))
    for build in result.get("page_builds", {}).values():
        for node in build["nodes"]:
            fields(node, ("source", "font_path"))
        for item in build["files"].values():
            fields(item, ("path",))
    return result
