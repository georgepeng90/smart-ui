"""显式把格式 4 项目转换为使用文字角色样式的格式 5 项目。"""

import json

from .layout import SCHEMA_MIGRATION_BACKUPS, ProjectLayout, map_state_paths
from .page_builds import validate_build_record
from .page_layouts import resolve_definition, validate_definition
from .projects import ProjectError, ProjectStore, _identifier, _json, _sha, spec_sha256
from .typography import _digest, validate_font, validate_typography


_OLD_TEXT_APPEARANCE = {
    "font_path", "font_sha256", "font_size", "line_height", "color", "stroke_width", "stroke_color",
}
_PREVIEW_NAMES = {"html": "preview.html", "overview": "overview.png"}


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 包含重复字段：{key}")
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=_unique_keys)
    except (OSError, ValueError) as error:
        raise ProjectError(f"无法读取 JSON 文件：{path}；{error}") from error


def _read_legacy(store, layout):
    project_file = layout.project_file
    project_bytes = project_file.read_bytes()
    state = json.loads(project_bytes, object_pairs_hook=_unique_keys)
    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state["schema_version"] != 4:
        raise ProjectError(f"文字规范转换需要格式 4 项目：{project_file}")
    if "typography" in state or state["paths"] != layout.paths:
        raise ProjectError(f"旧项目索引必须使用格式 4 的固定目录结构：{project_file}")
    for path in (layout.persistent("typography.json"), layout.documents["typography.md"]):
        if path.exists():
            raise ProjectError(f"转换不能覆盖已存在的文字规范文件：{path}")

    layout_bytes = {}
    for collection, directory in store._collections().items():
        records = {}
        for key, relative in state[collection].items():
            _identifier(key, collection)
            store._check_id_collision(records, key, collection)
            expected = layout.persistent_relative(f"{directory}/{key}.json")
            if relative != expected:
                raise ProjectError(f"{collection}.{key} 路径必须为 {expected}")
            path = layout.resolve(relative)
            payload = path.read_bytes()
            records[key] = json.loads(payload, object_pairs_hook=_unique_keys)
            if collection == "layouts":
                layout_bytes[key] = payload
        state[collection] = records

    documents = {"style.md", "components.md"}
    if set(state["document_hashes"]) != documents:
        raise ProjectError("格式 4 项目必须包含 style.md 和 components.md 的受管文档摘要")
    for name in sorted(documents):
        path = layout.documents[name]
        if not path.is_file() or _sha(path.read_bytes()) != state["document_hashes"][name]:
            raise ProjectError(f"受管文档已被修改或缺失：{path}")
    store._references(state, state["style"]["reference_ids"], "style.reference_ids")
    store._rule_references(state, state["style"]["rules"])
    store._validate_programs(layout, state["programs"])
    for component in state["components"].values():
        store._component_program(state, component["spec"])
    for key, page in state["layouts"].items():
        if page["id"] != key or page["sha256"] != spec_sha256(page["definition"]):
            raise ProjectError(f"旧页面布局标识或定义摘要不一致：{key}")
    return state, project_bytes, layout_bytes


def _check_mapping(pages, layout_styles):
    if not isinstance(layout_styles, dict):
        raise ProjectError("layout_styles 必须是按页面及文字节点 ID 组织的对象")
    expected = {}
    for layout_id, page in pages.items():
        text_ids = {node["id"] for node in page["definition"]["nodes"] if node["kind"] == "text"}
        if text_ids:
            expected[layout_id] = text_ids
    unknown = layout_styles.keys() - expected.keys()
    missing = expected.keys() - layout_styles.keys()
    if missing or unknown:
        raise ProjectError(f"layout_styles 页面映射缺少 {sorted(missing)}，多余 {sorted(unknown)}")
    for layout_id, text_ids in expected.items():
        mapping = layout_styles[layout_id]
        if not isinstance(mapping, dict):
            raise ProjectError(f"layout_styles.{layout_id} 必须是对象")
        unknown, missing = mapping.keys() - text_ids, text_ids - mapping.keys()
        if missing or unknown:
            raise ProjectError(f"layout_styles.{layout_id} 文字节点映射缺少 {sorted(missing)}，多余 {sorted(unknown)}")


def _convert_layouts(state, layout, layout_styles):
    _check_mapping(state["layouts"], layout_styles)
    for layout_id, page in state["layouts"].items():
        definition = page["definition"]
        try:
            for node in definition["nodes"]:
                if node["kind"] != "text":
                    continue
                label = f"页面 {layout_id} 的旧文字节点 {node['id']}"
                missing = _OLD_TEXT_APPEARANCE - node.keys()
                if missing:
                    raise ValueError(f"{label} 缺少字体或外观字段：{sorted(missing)}")
                path = layout.resolve(layout._relative_directory(node["font_path"], f"{label}.font_path"))
                layout.require_persistent_input(str(path), f"{label}.font_path")
                digest = _digest(node["font_sha256"], f"{label}.font_sha256")
                validate_font(path, digest, text=node["text"], label=label)
                for field in _OLD_TEXT_APPEARANCE:
                    del node[field]
                node["text_style_id"] = layout_styles[layout_id][node["id"]]
            checked = validate_definition(**definition)
            if layout_id in layout_styles:
                resolve_definition(state, checked)
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ProjectError(f"无法转换页面 {layout_id}；{error}") from error
        if layout_id in layout_styles:
            page["definition"] = checked
            page["sha256"] = spec_sha256(checked)


def _backup_builds(state, layout, backup):
    artifacts, builds, retired = {}, {}, []
    for build_id, record in state.get("page_builds", {}).items():
        _identifier(build_id, "page_builds")
        ProjectStore._check_id_collision(builds, build_id, "page_builds")
        if record["id"] != build_id or record["layout_id"] != build_id:
            raise ProjectError(f"旧页面构建标识不一致：{build_id}")
        nodes = record["nodes"]
        if not isinstance(nodes, list) or not nodes or any(node["kind"] not in ("text", "image") for node in nodes):
            raise ProjectError(f"旧页面构建缺少有效的节点列表：{build_id}")
        has_text = any(node["kind"] == "text" for node in nodes)
        record_path = f"page-builds/{build_id}.json"
        artifacts[backup / record_path] = (_json(record) + "\n").encode("utf-8")
        builds[build_id] = {"record": record_path, "requires_rebuild": has_text}
        if not has_text:
            validate_build_record(record)
            continue
        retired.append(build_id)
        previews = {}
        for label, name in _PREVIEW_NAMES.items():
            entry = record["files"][label]
            expected = layout.persistent_relative(f"previews/pages/{build_id}/{name}")
            if entry["path"] != expected:
                raise ProjectError(f"旧页面构建 {build_id} 的 {label} 路径必须为 {expected}")
            path = layout.resolve(expected)
            payload = path.read_bytes()
            if _sha(payload) != entry["sha256"]:
                raise ProjectError(f"旧页面预览摘要不一致：{path}")
            copied = f"previews/{build_id}/{name}"
            artifacts[backup / copied] = payload
            previews[label] = {"path": copied, "original": expected, "sha256": entry["sha256"]}
        builds[build_id]["previews"] = previews
    for build_id in retired:
        del state["page_builds"][build_id]
    return artifacts, builds, sorted(retired)


def _prepare(store, layout, typography, layout_styles, *, expected_revision=None):
    old, project_bytes, layout_bytes = _read_legacy(store, layout)
    revision = spec_sha256(old)
    if expected_revision is not None:
        store._revision(layout.root, expected_revision, revision)
    target_schema_version = 5
    transition = f"{old['schema_version']}-to-{target_schema_version}"
    backup = layout.root / SCHEMA_MIGRATION_BACKUPS / transition / revision
    layout._check_managed_path(backup)
    if backup.exists():
        raise ProjectError(f"转换备份已存在，不能覆盖：{backup}")
    for parent in backup.parents:
        if parent == layout.root:
            break
        if parent.exists() and not parent.is_dir():
            raise ProjectError(f"转换备份的目录已被文件占用：{parent}")
    artifacts, builds, retired = _backup_builds(old, layout, backup)

    state = map_state_paths(old, layout.resolve)
    state["schema_version"] = target_schema_version
    state["typography"] = validate_typography(typography, layout.root, state["references"])
    _convert_layouts(state, layout, layout_styles)
    converted = sorted(layout_styles)
    manifest = {"previous_schema_version": old["schema_version"], "revision": revision, "layout_styles": layout_styles,
                "converted_layouts": converted, "page_builds": builds}
    artifacts[backup / "project.json"] = project_bytes
    artifacts[backup / "manifest.json"] = (_json(manifest) + "\n").encode("utf-8")
    for layout_id in converted:
        artifacts[backup / "layouts" / f"{layout_id}.json"] = layout_bytes[layout_id]
    result = {"revision": revision, "converted_layouts": converted, "retired_builds": retired,
              "backup_directory": backup.relative_to(layout.root).as_posix(), "applied": False}
    return state, artifacts, result


def adopt_typography(project_root, typography, layout_styles, *, apply=False, expected_revision=None):
    """预检旧项目，或在确认旧 revision 后一次发布转换和备份。"""
    if apply and (not isinstance(expected_revision, str) or not expected_revision):
        raise ProjectError("应用转换前必须预检，并提供预检返回的 --expected-revision")
    if not apply and expected_revision is not None:
        raise ProjectError("--expected-revision 仅用于应用转换")
    store = ProjectStore()
    root = store._root(project_root)
    layout = ProjectLayout(root)
    try:
        if not apply:
            state, _, result = _prepare(store, layout, typography, layout_styles)
            store._render_for_root(root, state)
            return result
        with store._lock(root):
            state, artifacts, result = _prepare(store, layout, typography, layout_styles,
                                                expected_revision=expected_revision)
            saved = store._write(root, state, artifacts)
            _, reopened_revision = store._read(root)
            store._revision(root, saved["revision"], reopened_revision)
            return result | {"applied": True, "current_revision": reopened_revision}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ProjectError(f"无法转换项目文字规范：{root}；{error}") from error
