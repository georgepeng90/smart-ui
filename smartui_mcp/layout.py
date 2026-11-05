"""工程内的制作目录，以及持久记录与运行时文件路径之间的转换。"""

from copy import deepcopy
import os
from pathlib import Path, PurePosixPath, PureWindowsPath


DEFAULT_PATHS = {
    "documents": "smart-ui/docs",
    "references": "smart-ui/references",
    "programs": "smart-ui/programs",
    "sources": "smart-ui/sources",
}

IGNORE_RULES = "# SmartUI 本机缓存、临时文件和诊断\n/.smartui/\n"

RUN_PATH_FIELDS = (
    "document", "preview", "style_snapshot_path", "plan_path",
)


class ProjectLayout:
    def __init__(self, root: Path, paths: dict | None = None):
        self.root = Path(root).resolve()
        if paths is not None and (not isinstance(paths, dict) or paths.keys() - DEFAULT_PATHS.keys()):
            raise ValueError("paths 必须是对象，且只能配置 documents、references、programs、sources")
        self.paths = {**DEFAULT_PATHS, **(paths if paths is not None else {})}
        if self.paths != DEFAULT_PATHS:
            raise ValueError("制作目录固定在 smart-ui/ 中，不支持自定义目录")

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
        path = self.root / "smart-ui/project.json"
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

    def require_persistent_input(self, value, field):
        """校验工程内可在清理本机数据后继续读取的输入文件。"""
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"{field} 必须是工程内文件的绝对路径：{value!r}")
        path = Path(value)
        if not path.is_relative_to(self.root):
            raise ValueError(f"{field} 越出工程根目录：{path}")
        self._check_managed_path(path)
        if path.resolve().is_relative_to(self.root / ".smartui"):
            raise ValueError(f"{field} 不能依赖 .smartui/ 中的本机文件：{path}")
        if not path.is_file():
            raise ValueError(f"{field} 必须是现存文件：{path}")

    def document_link(self, value):
        path = self.resolve(str(value))
        if path.is_relative_to(self.root):
            return Path(os.path.relpath(path, self.directory("documents"))).as_posix()
        return path.as_posix()

    def ensure_gitignore(self):
        path = self.root / ".gitignore"
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

    for reference in result["references"].values():
        fields(reference, ("source",))
    for component in result["components"].values():
        if component["latest_run"] is not None:
            fields(component["latest_run"], RUN_PATH_FIELDS)
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
        fields(asset, ("path",))
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
        for node in page["definition"]["nodes"]:
            if node["kind"] == "text":
                fields(node, ("font_path",))
    for build in result.get("page_builds", {}).values():
        for node in build["nodes"]:
            fields(node, ("source", "font_path"))
        for item in build["files"].values():
            fields(item, ("path",))
    return result
