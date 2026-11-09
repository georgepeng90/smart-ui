"""格式 4 转换预检、发布、重建及失败恢复。"""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from smartui_mcp.layout import DEFAULT_PATHS
from smartui_mcp.page_layouts import image_geometry
from smartui_mcp.projects import ProjectError, ProjectStore, _sha, spec_sha256
from smartui_mcp.slicing import image_patches
from smartui_mcp.typography_adoption import adopt_typography
from test_compositions import text_node


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return _sha(payload)


def _write_json(path, value):
    return _write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _files(root):
    return {file.relative_to(root).as_posix(): file.read_bytes() for file in root.rglob("*") if file.is_file()}


def _old_build(root, page, asset, font):
    identifier = page["id"]
    folder = f"smart-ui/previews/pages/{identifier}"
    nodes = deepcopy(page["definition"]["nodes"])
    files = {}
    for index, node in enumerate(nodes):
        field, hash_field = ("source", "sha256") if node["kind"] == "image" else ("font_path", "font_sha256")
        payload = (root / asset["path"]).read_bytes() if node["kind"] == "image" else font.read_bytes()
        filename = f"input-{index}.{'png' if node['kind'] == 'image' else 'ttf'}"
        path = f"{folder}/{filename}"
        digest = _write(root / path, payload)
        node.update({field: path, hash_field: digest, "snapshot_key": filename})
        if node["kind"] == "image":
            geometry = image_geometry(asset)
            node.update(geometry=geometry, patch_plan=image_patches(
                geometry, (asset["width"], asset["height"]), (node["width"], node["height"]),
            ))
        files[filename] = {"path": path, "sha256": digest}
    snapshot = dict(page["definition"], nodes=deepcopy(nodes))
    for node in snapshot["nodes"]:
        node["source" if node["kind"] == "image" else "font_path"] = node["snapshot_key"]
    for label, filename, payload in (
        ("html", "preview.html", f"<!doctype html><title>格式 4 {identifier}</title>".encode("utf-8")),
        ("overview", "overview.png", (root / asset["path"]).read_bytes()),
        ("inputs", "inputs.json", json.dumps(snapshot).encode("utf-8")),
    ):
        path = f"{folder}/{filename}"
        files[label] = {"path": path, "sha256": _write(root / path, payload)}
    return dict(page["definition"], nodes=nodes, id=identifier, layout_id=identifier,
                layout_sha256=page["sha256"], files=files, renderer={"engine": "chromium"})


@pytest.fixture
def legacy(tmp_path, text_node):
    root = tmp_path
    font = Path(text_node["font_path"])
    picture = {"id": "icon", "kind": "image", "asset_id": "coin", "x": 0, "y": 0, "width": 8, "height": 8}
    label = {"id": "label", "kind": "text", "x": 12, "y": 8, "width": 68, "height": 28, "text": "A A",
             "align": "center", "wrap": True, "overflow": "error",
             "font_path": font.relative_to(root).as_posix(), "font_sha256": _sha(font.read_bytes()),
             "font_size": 16, "line_height": 20, "color": "#FFFF00", "stroke_width": 0, "stroke_color": "#000000"}
    image_path = "smart-ui/sources/images/coin/image.png"
    (root / image_path).parent.mkdir(parents=True)
    with Image.new("RGB", (4, 4), "#00FF00") as image:
        image.save(root / image_path)
    asset = {"id": "coin", "name": "金币", "path": image_path, "sha256": _sha((root / image_path).read_bytes()),
             "width": 4, "height": 4, "mode": "RGB", "has_transparency": False,
             "provenance": {"kind": "imported", "description": "测试合成图片"}}
    state = {"schema_version": 4, "project_id": "legacy", "name": "旧工程", "paths": DEFAULT_PATHS,
             "references": {}, "programs": {}, "components": {}, "images": {"coin": asset}, "compositions": {},
             "comparisons": {}, "visual_checks": {}, "feedback": {}, "layouts": {}, "page_builds": {},
             "style": {"description": "", "parameters": {}, "rules": [], "reference_ids": [],
                       "user_confirmation": None}}
    for identifier, nodes in (("screen", [picture, label]), ("images-only", [picture])):
        definition = {"width": 96, "height": 64, "background": "#123456", "nodes": nodes}
        page = {"id": identifier, "name": identifier, "definition": definition, "sha256": spec_sha256(definition)}
        state["layouts"][identifier] = page
        state["page_builds"][identifier] = _old_build(root, page, asset, font)
    state["document_hashes"] = {
        name: _write(root / "smart-ui/docs" / name, f"# 旧受管文档 {name}\n".encode("utf-8"))
        for name in ("style.md", "components.md")
    }
    index = deepcopy(state)
    for collection, directory in (("images", "sources/images"), ("layouts", "layouts")):
        for key, record in state[collection].items():
            relative = f"smart-ui/{directory}/{key}.json"
            _write_json(root / relative, record)
            index[collection][key] = relative
    _write_json(root / "smart-ui/project.json", index)
    typography = {"description": "支持当前工程的按钮文字。", "design_size": {"width": 96, "height": 64},
                  "size_scale": {"regular": 16}, "styles": {"button": {
                      "name": "按钮标签", "usage": "显示按钮用途", "size_id": "regular", "font_path": str(font),
                      "line_height": 22, "letter_spacing": 0, "fill": {"color": "#FFFFFF", "opacity": 1},
                      "stroke": None, "shadow": None, "evidence": [],
                  }}}
    return root, state, typography, {"screen": {"label": "button"}}


def test_preview_and_apply_preserve_content_and_allow_rebuild(legacy):
    root, state, typography, styles = legacy
    revision = spec_sha256(state)
    historical = root / "smart-ui/adoptions/typography" / revision
    _write(historical / "old.txt", b"historical backup")
    original = _files(root)
    dry = adopt_typography(str(root), typography, styles)
    assert not dry["applied"]
    assert dry["revision"] == revision
    assert dry["backup_directory"] == f".smartui/migrations/4-to-5/{revision}"
    assert dry["converted_layouts"] == dry["retired_builds"] == ["screen"]
    assert _files(root) == original
    assert not (root / ".smartui").exists()

    applied = adopt_typography(str(root), typography, styles, apply=True, expected_revision=dry["revision"])
    assert applied["backup_directory"] == dry["backup_directory"]
    assert _files(historical) == {"old.txt": b"historical backup"}
    backup = root / applied["backup_directory"]
    store = ProjectStore()
    context = store.context(str(root))
    assert applied["applied"] and applied["current_revision"] == context["revision"]
    assert context["data"]["schema_version"] == 5
    assert context["data"]["typography"]["styles"]["button"]["font_path"] == typography["styles"]["button"]["font_path"]
    nodes = context["data"]["layouts"]["screen"]["definition"]["nodes"]
    assert nodes[0] == state["layouts"]["screen"]["definition"]["nodes"][0]
    old = state["layouts"]["screen"]["definition"]["nodes"][1]
    assert nodes[1] == {key: old[key] for key in ("id", "kind", "x", "y", "width", "height", "text", "align",
                                                "wrap", "overflow")} | {"text_style_id": "button"}
    assert set(context["data"]["page_builds"]) == {"images-only"}
    assert store.get_page_build(str(root), "images-only")["matches_layout"]
    assert (backup / "project.json").read_bytes() == original["smart-ui/project.json"]
    assert (backup / "layouts/screen.json").read_bytes() == original["smart-ui/layouts/screen.json"]
    assert not (backup / "layouts/images-only.json").exists()
    assert json.loads((backup / "page-builds/screen.json").read_bytes()) == state["page_builds"]["screen"]
    manifest = json.loads((backup / "manifest.json").read_bytes())
    archived = manifest["page_builds"]["screen"]["previews"]
    for evidence in archived.values():
        assert (root / evidence["original"]).read_bytes() == original[evidence["original"]]
        assert (backup / evidence["path"]).read_bytes() == original[evidence["original"]]
    assert set(_files(backup)) == {
        "project.json", "manifest.json", "layouts/screen.json", "page-builds/screen.json",
        "page-builds/images-only.json", "previews/screen/preview.html", "previews/screen/overview.png",
    }

    rebuilt = store.build_layout(str(root), context["revision"], "screen")
    assert rebuilt["matches_layout"] and rebuilt["matches_bindings"]
    assert rebuilt["page_build"]["nodes"][1]["text_style_id"] == "button"
    assert (root / archived["html"]["original"]).read_bytes() != original[archived["html"]["original"]]
    for evidence in archived.values():
        assert (backup / evidence["path"]).read_bytes() == original[evidence["original"]]


@pytest.mark.parametrize(("styles", "message"), [
    ({}, "缺少.*screen"),
    ({"screen": {}}, "缺少.*label"),
    ({"screen": {"label": "button", "icon": "button"}}, "多余.*icon"),
    ({"screen": {"label": "button"}, "images-only": {}}, "多余.*images-only"),
    ({"screen": {"label": "missing"}}, "引用不存在的文字样式"),
])
def test_mapping_must_cover_only_existing_text_nodes(legacy, styles, message):
    root, state, typography, _ = legacy
    before = _files(root)
    with pytest.raises(ProjectError, match=message):
        adopt_typography(str(root), typography, styles, apply=True, expected_revision=spec_sha256(state))
    assert _files(root) == before


@pytest.mark.parametrize(("modified_path", "message"), [
    ("fixture.ttf", "旧文字节点 label.*SHA-256 不符"),
    ("smart-ui/docs/style.md", "受管文档已被修改"),
    ("smart-ui/layouts/screen.json", "定义摘要不一致"),
    ("smart-ui/docs/typography.md", "不能覆盖已存在"),
    ("smart-ui/typography.json", "不能覆盖已存在"),
    ("smart-ui/previews/pages/screen/preview.html", "预览摘要不一致"),
])
def test_changed_sources_and_existing_typography_fail_before_publication(legacy, modified_path, message):
    root, state, typography, styles = legacy
    if modified_path == "fixture.ttf":
        replacement = root / "new-font.ttf"
        replacement.write_bytes((root / modified_path).read_bytes())
        typography["styles"]["button"]["font_path"] = str(replacement)
    if modified_path == "smart-ui/layouts/screen.json":
        changed = deepcopy(state["layouts"]["screen"])
        changed["definition"]["background"] = "#111111"
        _write_json(root / modified_path, changed)
    else:
        (root / modified_path).write_bytes(b"manual modification")
    before = _files(root)
    with pytest.raises(ProjectError, match=message):
        adopt_typography(str(root), typography, styles, apply=True, expected_revision=spec_sha256(state))
    assert _files(root) == before


def test_apply_requires_revision_and_never_overwrites_backup(legacy):
    root, _, typography, styles = legacy
    result = adopt_typography(str(root), typography, styles)
    before = _files(root)
    with pytest.raises(ProjectError, match="--expected-revision"):
        adopt_typography(str(root), typography, styles, apply=True)
    with pytest.raises(ProjectError, match="revision 冲突"):
        adopt_typography(str(root), typography, styles, apply=True, expected_revision="outdated")
    assert _files(root) == before
    backup = root / ".smartui/migrations/4-to-5" / result["revision"]
    _write(backup / "old.txt", b"earlier backup")
    before = _files(root)
    with pytest.raises(ProjectError, match="备份已存在"):
        adopt_typography(str(root), typography, styles, apply=True, expected_revision=result["revision"])
    assert _files(root) == before


def test_publication_failure_restores_legacy_project_and_backups(legacy, monkeypatch):
    root, state, typography, styles = legacy
    before = _files(root)
    replace = os.replace

    def fail_index_write(source, destination):
        if destination == root / "smart-ui/project.json":
            assert (root / "smart-ui/typography.json").is_file()
            assert (root / ".smartui/migrations/4-to-5" / spec_sha256(state) / "manifest.json").is_file()
            raise OSError("模拟转换发布中断")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_index_write)
        with pytest.raises(ProjectError, match="模拟转换发布中断.*已恢复先前状态"):
            adopt_typography(str(root), typography, styles, apply=True, expected_revision=spec_sha256(state))
    assert _files(root) == before
    assert adopt_typography(str(root), typography, styles)["revision"] == spec_sha256(state)


def test_cli_preflight_and_apply(legacy):
    root, _, typography, styles = legacy
    _write_json(root / "new-typography.json", typography)
    _write_json(root / "layout-styles.json", styles)
    arguments = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/adopt_typography.py"),
                 "--project-root", str(root), "--typography", str(root / "new-typography.json"),
                 "--layout-styles", str(root / "layout-styles.json")]
    dry = subprocess.run(arguments, capture_output=True, check=True)
    preflight = json.loads(dry.stdout)
    revision = preflight["revision"]
    assert preflight["backup_directory"] == f".smartui/migrations/4-to-5/{revision}"
    applied = subprocess.run([*arguments, "--apply", "--expected-revision", revision],
                             capture_output=True, check=True)
    result = json.loads(applied.stdout)
    assert result["applied"]
    assert result["backup_directory"] == preflight["backup_directory"]
