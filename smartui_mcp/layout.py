"""工程内的制作目录，以及持久记录与运行时文件路径之间的转换。"""

from copy import deepcopy
from functools import cached_property
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath


_DIRECTORIES = {
    "documents": "docs",
    "references": "references",
    "programs": "programs",
    "sources": "sources",
}
_UNITY_PROJECT_FILE = "smartui.project.json"
_is_reserved_windows_name = getattr(ntpath, "isreserved", lambda name: PureWindowsPath(name).is_reserved())

IGNORE_RULES = "# SmartUI 本机缓存、临时文件和诊断\n/.smartui/\n"
SCHEMA_MIGRATION_BACKUPS = ".smartui/migrations"

RUN_PATH_FIELDS = (
    "document", "preview", "style_snapshot_path", "plan_path",
)


class ProjectLayout:
    def __init__(self, root: Path, paths: dict | None = None, data_root: str | None = None):
        self.root = Path(root).resolve()
        self._paths = paths
        self._requested_data_root = data_root
        if paths is not None and paths != self.paths:
            raise ValueError("项目目录必须使用制作资料的固定结构")

    @cached_property
    def is_unity(self):
        assets = self.root / "Assets"
        version = self.root / "ProjectSettings/ProjectVersion.txt"
        if not version.is_file():
            return False
        self._check_managed_path(assets)
        if not assets.is_dir():
            raise ValueError(f"Unity 项目缺少 Assets 目录：{assets}")
        return True

    @cached_property
    def data_root(self):
        requested = self._requested_data_root
        if not self.is_unity:
            if requested is not None and self._relative_directory(requested, "data_root") != "smart-ui":
                raise ValueError("非 Unity 项目的制作资料固定存放在 smart-ui/ 中")
            return "smart-ui"

        assets = self.root / "Assets"
        legacy = self.root / "smart-ui"
        if legacy.exists() or legacy.is_symlink() or legacy.is_junction():
            raise ValueError(f"Unity 项目发现旧 SmartUI 资料，请先迁移至 Assets 子目录：{legacy}")
        if requested is not None:
            requested = self._validate_unity_data_root(requested)
        if self._paths is not None and requested is None:
            documents = self._paths.get("documents") if isinstance(self._paths, dict) else None
            if not isinstance(documents, str) or not documents.endswith("/docs"):
                raise ValueError("项目目录必须使用制作资料的固定结构")
            return self._validate_unity_data_root(documents.removesuffix("/docs"))

        existing = []
        directories = [assets]
        while directories:
            directory = directories.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if os.path.normcase(entry.name) == _UNITY_PROJECT_FILE:
                        self._check_managed_path(directory / entry.name)
                        relative = directory.relative_to(self.root).as_posix()
                        existing.append(self._validate_unity_data_root(relative))
                    elif entry.is_dir(follow_symlinks=False) and not entry.is_junction():
                        directories.append(Path(entry.path))
        if len(existing) > 1:
            raise ValueError(f"Unity 项目存在多个 SmartUI 制作目录：{', '.join(sorted(existing))}")
        if existing:
            if requested is not None and Path(requested) != Path(existing[0]):
                raise ValueError(f"Unity 项目已有 SmartUI 制作目录 {existing[0]}，不能另建 {requested}")
            return existing[0]
        return requested if requested is not None else self._validate_unity_data_root("Assets/SmartUI")

    def _validate_unity_data_root(self, value):
        relative = self._relative_directory(value, "data_root")
        path = PurePosixPath(relative)
        if len(path.parts) < 2 or path.parts[0] != "Assets":
            raise ValueError(f"Unity 项目的 data_root 必须是 Assets 的子目录：{value}")
        if any(_is_reserved_windows_name(part) or part.endswith(".") or part.strip() != part or
               any(char in part for char in '<>:"|?*') for part in path.parts):
            raise ValueError(f"data_root 包含不受支持的目录名：{value}")
        self._check_managed_path(self.root / relative)
        return relative

    @cached_property
    def paths(self):
        return {key: f"{self.data_root}/{directory}" for key, directory in _DIRECTORIES.items()}

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
        return self.persistent(_DIRECTORIES[key])

    def persistent(self, value):
        relative = self._relative_directory(value, "制作资料路径")
        data_root = self.root / self.data_root
        path = data_root / relative
        self._check_managed_path(path, boundary=data_root)
        return path

    def persistent_relative(self, value):
        return self.persistent(value).relative_to(self.root).as_posix()

    def _check_managed_path(self, path, *, boundary=None):
        for parent in (path, *path.parents):
            if parent == self.root:
                break
            if parent.is_symlink() or parent.is_junction():
                raise ValueError(f"受管路径不能包含符号链接或目录联接：{parent}")
        if not path.resolve().is_relative_to(boundary if boundary is not None else self.root):
            if boundary is not None:
                raise ValueError(f"受管路径越出制作资料目录 {boundary}：{path}")
            raise ValueError(f"受管路径越出工程根目录：{path}")

    @property
    def project_file(self):
        return self.persistent(_UNITY_PROJECT_FILE if self.is_unity else "project.json")

    @property
    def documents(self):
        paths = {name: self.directory("documents") / name for name in ("style.md", "components.md", "typography.md")}
        for path in paths.values():
            self._check_managed_path(path)
        return paths

    def resolve(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError(f"文件路径必须是非空字符串：{value!r}")
        path = Path(value)
        if path.is_absolute():
            if path.is_relative_to(self.root):
                self._check_managed_path(path)
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
    if result.get("typography") is not None:
        for style in result["typography"]["styles"].values():
            fields(style, ("font_path",))
    for build in result.get("page_builds", {}).values():
        for node in build["nodes"]:
            fields(node, ("source", "font_path"))
        for item in build["files"].values():
            fields(item, ("path",))
    return result
