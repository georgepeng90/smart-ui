"""从同一正式布局生成不同文案、组件状态和画布尺寸的本地预览。"""

from copy import deepcopy
from html import escape
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .browser_preview import render_page
from .image_assets import _replace_directory
from .layout import ProjectLayout
from .layout_constraints import validate_insets
from .page_builds import snapshot_key
from .page_dependencies import asset_source_issues, require_current_page_sources
from .page_layouts import iter_layout_nodes, resolve_definition, validate_definition
from .projects import ProjectError, _identifier
from .references import _temporary_directory


_WINDOWS_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL",
                         *(f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10))}


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def _apply_node_overrides(nodes, overrides, field, kind, target):
    name = "文字" if kind == "text" else "图像"
    if not isinstance(overrides, dict):
        raise ValueError(f"{field} 必须是按{name}节点 ID 组织的对象")
    for node_id, value in overrides.items():
        node = nodes.get(node_id)
        if node is None or node["kind"] != kind:
            raise ValueError(f"{field}.{node_id} 必须引用正式布局中的{name}节点")
        if target == "text":
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field}.{node_id} 必须是非空字符串")
        else:
            _identifier(value, f"{field}.{node_id}")
        node[target] = value


def _variant_definition(base, variant, identifiers):
    required = {"id", "width", "height", "texts"}
    if not isinstance(variant, dict):
        raise ValueError("预览场景必须是对象")
    missing = required - variant.keys()
    extra = variant.keys() - required - {"safe_area", "images", "text_styles"}
    if missing or extra:
        raise ValueError(f"场景字段不完整或包含未知字段，缺少 {sorted(missing)}，多余 {sorted(extra, key=str)}")
    identifier = variant["id"]
    _identifier(identifier, "variant.id")
    if identifier.endswith(".") or identifier.partition(".")[0].upper() in _WINDOWS_DEVICE_NAMES:
        raise ValueError(f"variant.id 不能用作目录名：{identifier}")
    folded = identifier.casefold()
    if folded in identifiers:
        raise ValueError(f"场景 ID {identifier} 与 {identifiers[folded]} 重复（不区分大小写）")
    identifiers[folded] = identifier

    definition = deepcopy(base)
    definition.update(width=variant["width"], height=variant["height"])
    if "safe_area" in variant:
        validate_insets(variant["safe_area"], "safe_area")
        definition["safe_area"] = variant["safe_area"]
    nodes = {node["id"]: node for node in iter_layout_nodes(definition["nodes"])}
    _apply_node_overrides(nodes, variant["texts"], "texts", "text", "text")
    if "text_styles" in variant:
        _apply_node_overrides(nodes, variant["text_styles"], "text_styles", "text", "text_style_id")
    if "images" in variant:
        _apply_node_overrides(nodes, variant["images"], "images", "image", "asset_id")
    return validate_definition(**definition)


def _scene_resources(root, resolved):
    resources = {}
    for node in resolved["nodes"]:
        image = node["kind"] == "image"
        source = Path(node["source" if image else "font_path"]).resolve()
        key = snapshot_key(root, node, source)
        node["snapshot_key"] = key
        resources[key] = {"path": source.relative_to(root).as_posix(),
                          "sha256": node["sha256" if image else "font_sha256"]}
    return resources


def _index_html(layout_id, scenes):
    cards = []
    for item in scenes:
        identifier = escape(item["id"])
        scene = f"scenes/{identifier}"
        cards.append(f"""<article><h2>{identifier} · {item['width']} × {item['height']}</h2>
<p><a href="{scene}/preview.html">打开离线预览</a> · <a href="{scene}/report.json">查看检查报告</a></p>
<a href="{scene}/overview.png"><img loading="lazy" src="{scene}/overview.png" alt="{identifier} 页面截图"></a></article>""")
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SmartUI 页面预览场景</title>
<style>body{font:16px system-ui,sans-serif;margin:24px;background:#17191e;color:#f2f2f2}
a{color:#a9d5ff} main{display:flex;gap:20px;flex-wrap:wrap} article{width:min(100%,420px);padding:12px;
background:#242832} h2{font-size:18px;margin:0} img{display:block;max-width:100%;max-height:560px}</style></head>
<body><h1>""" + escape(layout_id) + """ 的页面预览场景</h1>
<p>此批次使用报告中记录的项目版本。<a href="report.json">查看批次报告</a></p><main>""" + "\n".join(cards) + "</main></body></html>\n"


def preview_layout_variants(store, project_root, expected_revision, layout_id, variants):
    """预检并发布整批本地预览，不改动正式布局、资源和构建记录。"""
    _identifier(layout_id, "layout_id")
    if not isinstance(variants, list) or not variants:
        raise ProjectError("variants 必须是非空场景列表")
    root = store._root(project_root)
    with store._lock(root):
        state, revision = store._read(root)
        store._revision(root, expected_revision, revision)
        page = state["layouts"].get(layout_id)
        if page is None:
            raise ProjectError(f"页面布局不存在：{layout_id}")
        layout = ProjectLayout(root, state["paths"])
        destination = root / ".smartui" / "previews" / "variants" / layout_id
        layout._check_managed_path(destination)
        work = TemporaryDirectory(prefix="layout-variants-", dir=_temporary_directory(root), delete=False)
        diagnostic = Path(work.name)
        staging = diagnostic / "current"
        base = {"project_revision": revision, "layout_id": layout_id, "layout_sha256": page["sha256"]}
        phase, label = "预检", "共享输入"
        try:
            source_issues = asset_source_issues(state)
            prepared, identifiers = [], {}
            for index, variant in enumerate(variants):
                label = f"variants[{index}]"
                if isinstance(variant, dict) and isinstance(variant.get("id"), str):
                    label += f" ({variant['id']})"
                definition = _variant_definition(page["definition"], variant, identifiers)
                resolved = resolve_definition(state, definition)
                require_current_page_sources(state, {"id": layout_id, "definition": definition}, source_issues)
                prepared.append((variant["id"], definition, resolved, _scene_resources(root, resolved)))

            phase = "渲染"
            scenes, results = [], []
            for identifier, definition, resolved, resources in prepared:
                label = identifier
                relative = Path("scenes") / identifier
                output = staging / relative
                rendered = render_page(output, resolved)
                files = {kind: {**rendered[kind], "path": Path(rendered[kind]["path"]).name}
                         for kind in ("html", "overview")}
                _write_json(output / "report.json", {**base, "id": identifier, "definition": definition,
                                                     "resources": resources, "renderer": rendered["renderer"],
                                                     "files": files})
                scene = {"id": identifier, "width": definition["width"], "height": definition["height"],
                         "report": (relative / "report.json").as_posix(),
                         **{kind: (relative / item["path"]).as_posix() for kind, item in files.items()}}
                scenes.append(scene)
                results.append({**scene, **{key: str(destination / scene[key])
                                            for key in ("report", "html", "overview")}})

            phase, label = "发布", layout_id
            _write_json(staging / "report.json", {**base, "scenes": scenes})
            (staging / "index.html").write_text(_index_html(layout_id, scenes), encoding="utf-8", newline="\n")
            _replace_directory(layout, staging, destination)
        except (OSError, RuntimeError, ValueError) as error:
            if not diagnostic.exists():
                diagnostic = diagnostic.with_name(diagnostic.name + "-failed")
            try:
                _write_json(diagnostic / "failure.json", {**base, "phase": phase, "scene": label, "error": str(error)})
            except OSError as report_error:
                raise ProjectError(f"{phase}场景 {label} 失败：{error}；诊断目录：{diagnostic}；"
                                   f"写入失败报告时出错：{report_error}") from error
            raise ProjectError(f"{phase}场景 {label} 失败：{error}；诊断目录：{diagnostic}") from error

        work.cleanup()
        return {**base, "project_root": str(root), "index": str(destination / "index.html"),
                "report": str(destination / "report.json"), "scenes": results}
