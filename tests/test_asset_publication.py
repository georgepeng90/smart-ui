"""验证当前资产、预览与项目记录在发布失败时一同恢复。"""

import os
from pathlib import Path

from PIL import Image
import pytest

from smartui_mcp.projects import ProjectError, ProjectStore
from test_export_workflow import export_item
from test_page_layouts import image_node, save
from test_publication import persistent_files
from test_review_workflow import project, record_sample_run


def _assert_rollback(store, root, monkeypatch, publish):
    before = persistent_files(Path(root))

    def fail_write(*args):
        assert persistent_files(Path(root)) != before
        raise ProjectError("模拟项目记录写入失败")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_write", fail_write)
        with pytest.raises(ProjectError, match="模拟项目记录写入失败"):
            publish()
    assert persistent_files(Path(root)) == before


def test_image_registration_restores_previous_format_when_project_write_fails(tmp_path, monkeypatch):
    store = ProjectStore()
    root = str(tmp_path)
    context = store.create(root, "fixture", "图像发布验证")
    provenance = {"kind": "imported", "description": "合成测试图"}
    png = tmp_path / "source.png"
    jpeg = tmp_path / "source.jpg"
    with Image.new("RGBA", (3, 2), "red") as image:
        image.save(png)
    with Image.new("RGB", (3, 2), "blue") as image:
        image.save(jpeg)
    first = store.register_image(root, context["revision"], "icon", "图标", str(png), provenance)
    _assert_rollback(store, root, monkeypatch, lambda: store.register_image(
        root, first["revision"], "icon", "图标", str(jpeg), provenance))
    assert store.get_image(root, "icon")["image"] == first["image"]


def test_export_restores_the_previous_png_and_manifest_when_index_publication_fails(project, monkeypatch):
    store, context = project
    root = context["project_root"]
    first = store.export_assets(root, context["revision"], "sample", [export_item()])
    latest = record_sample_run(store, store.context(root), "second", (20, 80, 160, 255))
    before = persistent_files(Path(root))
    png = Path(first["export"]["items"][0]["record"]["path"])
    manifest = Path(first["export"]["manifest"]["path"])
    index = Path(root) / "smart-ui/project.json"
    original_png, original_manifest = png.read_bytes(), manifest.read_bytes()
    replace = os.replace

    def fail_index(source, destination):
        if destination == index:
            assert png.read_bytes() != original_png
            assert manifest.read_bytes() != original_manifest
            raise OSError("模拟项目索引写入失败")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_index)
        with pytest.raises(ProjectError, match="模拟项目索引写入失败"):
            store.export_assets(root, latest["revision"], "sample", [export_item()])
    assert persistent_files(Path(root)) == before
    assert store.get_export(root, "sample")["export"] == first["export"]


def test_composition_restores_previous_preview_when_project_write_fails(project, monkeypatch):
    store, context = project
    root = context["project_root"]
    node = {"id": "button-instance", "kind": "component", "component_id": "sample",
            "x": 0, "y": 0, "width": 8, "height": 6}
    first = store.create_composition(root, context["revision"], "sample", 64, 48, "#123456", [node])
    _assert_rollback(store, root, monkeypatch, lambda: store.create_composition(
        root, first["revision"], "sample", 64, 48, "#123456", [{**node, "x": 20}]))
    assert store.get_composition(root, "sample")["composition"] == first["composition"]


def test_page_rebuild_restores_previous_preview_when_project_write_fails(project, monkeypatch):
    store, context = project
    root = context["project_root"]
    exported = store.export_assets(root, context["revision"], "sample", [export_item()])
    saved = save(store, exported, [image_node()])
    first = store.build_layout(root, saved["revision"], "screen")
    revised = save(store, first, [image_node(x=20)])
    _assert_rollback(store, root, monkeypatch, lambda: store.build_layout(root, revised["revision"], "screen"))
    restored = store.get_page_build(root, "screen")
    assert restored["page_build"] == first["page_build"]
    assert not restored["matches_layout"]
