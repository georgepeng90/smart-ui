"""核对登记所用的 Unity 文本预制体及其 .meta 文件。"""

from pathlib import Path, PurePosixPath
import re

from .layout import ProjectLayout
from .projects import ProjectError, _identifier, _sha


_GUID = r"[0-9a-fA-F]{32}"
_FILE_ID = r"-?[0-9]{1,19}"
_OBJECT = re.compile(rf"--- !u!([0-9]+) &({_FILE_ID})(?: stripped)?")
_OBJECT_MEMBER = re.compile(r"  [^\W\d][^\s:]*:(?:[ \t]|$)")
_SOURCE = re.compile(rf"  m_SourcePrefab: {{fileID: ({_FILE_ID}), guid: ({_GUID}), type: 3}}")
_HEADER = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:"]
_RECORD_FIELDS = ("path", "guid", "sha256", "meta_sha256", "slot_bindings", "source_guids")


def _guid(value, label):
    if not isinstance(value, str) or not re.fullmatch(_GUID, value):
        raise ProjectError(f"{label} 必须是 32 位十六进制 Unity GUID")
    return value.lower()


def _is_file_id(value):
    return type(value) is int and -(2**63) <= value < 2**63


def _prefab_path(value):
    if isinstance(value, str):
        path = PurePosixPath(value)
        if (path.as_posix() == value and path.parts[:1] == ("Assets",)
                and path.suffix == ".prefab" and not re.search(r'[<>:"\\|?*\x00-\x1f]', value)
                and all(part.lower() != ".smartui" and not part.endswith((".", " ")) for part in path.parts)):
            return
    raise ProjectError(f"预制体路径必须是 Assets/ 下使用正斜杠的规范 .prefab 相对路径：{value!r}")


def _read_text(layout, relative):
    try:
        path = layout.resolve(relative)
        if not path.is_file():
            raise ValueError("必须是现存文件")
        data = path.read_bytes()
        contents = data.decode("utf-8-sig")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", contents):
            raise ValueError("包含非文本控制字符")
    except (OSError, ValueError, RuntimeError) as error:
        raise ProjectError(f"无法读取 Unity 文件 {relative}：{error}") from error
    return data, contents.splitlines()


def _meta_guid(lines, relative):
    fields = [line for line in lines if re.match(r"guid\s*:", line)]
    if len(fields) != 1:
        raise ProjectError(f"预制体元数据 {relative} 必须有且仅有一个顶层 guid 字段")
    return _guid(fields[0].split(":", 1)[1].strip(), f"预制体元数据 {relative} 的 guid")


def _prefab_references(lines, relative):
    if lines[:2] != _HEADER:
        raise ProjectError(f"预制体 {relative} 缺少 Unity 文本序列化文件头")
    declarations = [index for index, line in enumerate(lines) if line.startswith("---")]
    if not declarations or declarations[0] != 2:
        raise ProjectError(f"预制体 {relative} 的文件头后缺少 Unity 对象声明")
    file_ids = set()
    source_guids = set()
    for start, end in zip(declarations, [*declarations[1:], len(lines)]):
        declaration = _OBJECT.fullmatch(lines[start])
        if declaration is None:
            raise ProjectError(f"预制体 {relative} 第 {start + 1} 行的 Unity 对象声明无效")
        class_id, file_id_text = declaration.groups()
        file_id = int(file_id_text)
        if not _is_file_id(file_id) or file_id in file_ids:
            raise ProjectError(f"预制体 {relative} 的对象 fileID {file_id} 重复或超出 64 位整数范围")
        file_ids.add(file_id)
        body = [line for line in lines[start + 1:end] if line.strip() and not line.lstrip().startswith("#")]
        if (len(body) < 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*:", body[0])
                or not _OBJECT_MEMBER.match(body[1])):
            raise ProjectError(f"预制体 {relative} 的对象 fileID {file_id} 缺少有效的对象正文")
        if class_id == "1001":
            if body[0] != "PrefabInstance:":
                raise ProjectError(f"预制体 {relative} 的对象 fileID {file_id} 的 class ID 为 1001，正文根键必须是 PrefabInstance")
            source_fields = [line for line in body if re.match(r"  m_SourcePrefab\s*:", line)]
            source = _SOURCE.fullmatch(source_fields[0]) if len(source_fields) == 1 else None
            if source is None or not _is_file_id(int(source[1])) or int(source[1]) == 0:
                raise ProjectError(f"预制体 {relative} 的对象 fileID {file_id} 缺少唯一且有效的 m_SourcePrefab")
            source_guids.add(source[2].lower())
    return file_ids, source_guids


def inspect_prefab(
    project_root: Path, path: str, expected_guid: str,
    slot_bindings: dict[str, int], expected_source_guids: list[str],
) -> dict:
    """按文本核对对象和源引用并生成登记记录，不验证完整 YAML 或 Unity 导入。"""
    _prefab_path(path)
    guid = _guid(expected_guid, f"预制体 {path} 的登记 GUID")
    if not isinstance(expected_source_guids, list):
        raise ProjectError(f"预制体 {path} 的 source_guids 必须是列表")
    sources = [_guid(value, f"预制体 {path} 的 source_guids") for value in expected_source_guids]
    if len(sources) != len(set(sources)):
        raise ProjectError(f"预制体 {path} 的 source_guids 包含重复的 GUID")
    if not isinstance(slot_bindings, dict):
        raise ProjectError(f"预制体 {path} 的 slot_bindings 必须是对象")
    for slot, file_id in slot_bindings.items():
        _identifier(slot, f"预制体 {path} 的槽位 {slot!r}")
        if not _is_file_id(file_id):
            raise ProjectError(f"预制体 {path} 的槽位 {slot!r} 的 fileID 必须是有符号 64 位整数")

    try:
        layout = ProjectLayout(project_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ProjectError(f"无法定位预制体 {path} 的工程根目录 {project_root}：{error}") from error
    data, lines = _read_text(layout, path)
    meta_path = path + ".meta"
    meta_data, meta_lines = _read_text(layout, meta_path)
    actual_guid = _meta_guid(meta_lines, meta_path)
    if guid != actual_guid:
        raise ProjectError(f"预制体元数据 {meta_path} 的 GUID 为 {actual_guid}，登记 GUID 为 {guid}")
    file_ids, actual_sources = _prefab_references(lines, path)
    for slot, file_id in slot_bindings.items():
        if file_id not in file_ids:
            raise ProjectError(f"预制体 {path} 的槽位 {slot!r} 引用了缺失的对象 fileID {file_id}")
    if set(sources) != actual_sources:
        raise ProjectError(f"预制体 {path} 的 m_SourcePrefab GUID 为 {sorted(actual_sources)}，登记为 {sorted(sources)}")
    return {
        "path": path, "guid": guid, "sha256": _sha(data), "meta_sha256": _sha(meta_data),
        "slot_bindings": dict(slot_bindings), "source_guids": sorted(sources),
    }


def verify_prefab(project_root: Path, record: dict) -> None:
    """用当前文件重新核对登记记录的字段与摘要。"""
    if not isinstance(record, dict) or set(record) != set(_RECORD_FIELDS):
        path = record.get("path") if isinstance(record, dict) else None
        raise ProjectError(f"预制体 {path!r} 的登记记录必须包含且仅包含 {', '.join(_RECORD_FIELDS)}")
    path = record["path"]
    current = inspect_prefab(project_root, path, record["guid"], record["slot_bindings"], record["source_guids"])
    for field in _RECORD_FIELDS:
        if record[field] != current[field]:
            raise ProjectError(f"预制体 {path} 的登记字段 {field} 与当前文件不一致")
