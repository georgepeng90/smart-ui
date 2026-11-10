"""验证固定目录、工程移动后的可用性及用户数据边界。"""

import json
from pathlib import Path
import shutil

import pytest

from smartui_mcp.image_assets import _replace_directory
from smartui_mcp.layout import ProjectLayout, map_state_paths
from smartui_mcp.projects import ProjectError, ProjectStore
from smartui_mcp.references import register_reference, inspect_reference
from PIL import Image
from test_export_workflow import export_item
from test_page_layouts import image_node, save
from test_review_workflow import PROGRAM, compare, record_sample_run


def test_fixed_layout_and_relative_links_survive_project_move_without_cache(tmp_path):
    store = ProjectStore()
    root = tmp_path / "original"
    context = store.create(str(root), "game", "项目")
    source = tmp_path / "reference.png"
    Image.new("RGB", (10, 10), "red").save(source)
    reference = register_reference(str(root), "guide", str(source))
    context = store.add_reference(str(root), context["revision"], reference)
    context = store.save_program(str(root), context["revision"], "drawing", "程序", "", "result = []\n")
    state_file = root / "smart-ui/project.json"
    payload = state_file.read_bytes()
    stored = json.loads(payload)
    assert stored["references"]["guide"]["source"] == "smart-ui/references/guide/reference.png"
    assert stored["programs"]["drawing"] == "smart-ui/programs/drawing.json"
    assert "(<../references/guide/reference.png>)" in (root / "smart-ui/docs/style.md").read_text(encoding="utf-8")
    assert "(<../programs/drawing.py>)" in (root / "smart-ui/docs/components.md").read_text(encoding="utf-8")

    moved = tmp_path / "moved"
    shutil.copytree(root / "smart-ui", moved / "smart-ui")
    source.unlink()
    reloaded = store.context(str(moved))
    assert reloaded["revision"] == context["revision"]
    assert (moved / "smart-ui/project.json").read_bytes() == payload
    reference = reloaded["data"]["references"]["guide"]
    assert Path(reference["source"]).is_relative_to(moved)
    inspected = inspect_reference(str(moved), reference)
    assert Path(inspected["preview"]).is_file()
    assert store.get_program(str(moved), "drawing")["source"] == "result = []\n"
    store.update_style(str(moved), reloaded["revision"], "继续制作", [], {}, ["guide"])


def test_path_conversion_does_not_rewrite_project_parameters_or_feedback():
    user_data = {"source": "model", "path": "../a-user-defined-value"}
    state = {"references": {}, "components": {}, "style": {"parameters": user_data},
             "feedback": {"item": user_data}}
    converted = map_state_paths(state, lambda value: "changed")
    assert converted == state
    assert converted is not state


def test_gitignore_keeps_shared_project_data_and_preserves_existing_rules(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    ignore = root / ".gitignore"
    ignore.write_text("my-local-notes.txt\n", encoding="utf-8")
    ProjectStore().create(str(root), "game", "项目")
    rules = ignore.read_text(encoding="utf-8").splitlines()
    assert "my-local-notes.txt" in rules
    assert "/.smartui/" in rules
    assert "smart-ui/" not in rules
    assert not (root / ".smartui/.gitignore").exists()


def test_incomplete_persisted_layout_does_not_silently_get_defaults(tmp_path):
    store = ProjectStore()
    store.create(str(tmp_path), "game", "项目")
    file = tmp_path / "smart-ui/project.json"
    state = json.loads(file.read_bytes())
    del state["paths"]["sources"]
    file.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ProjectError, match="固定结构"):
        store.context(str(tmp_path))


@pytest.fixture
def unity_root(tmp_path):
    root = tmp_path / "Unity Project"
    (root / "Assets").mkdir(parents=True)
    settings = root / "ProjectSettings"
    settings.mkdir()
    (settings / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.0f1\n", encoding="utf-8")
    return root


def test_unity_default_data_root(unity_root):
    store = ProjectStore()
    context = store.create(str(unity_root), "game", "项目")

    assert context["data"]["paths"]["documents"] == "Assets/SmartUI/docs"
    assert (unity_root / "Assets/SmartUI/smartui.project.json").is_file()
    assert not (unity_root / "smart-ui").exists()
    assert {path.name for path in (unity_root / "ProjectSettings").iterdir()} == {"ProjectVersion.txt"}
    assert "/.smartui/" in (unity_root / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_unity_custom_data_root_holds_current_assets_and_reopens_without_cache(unity_root, tmp_path):
    store = ProjectStore()
    root = str(unity_root)
    relative = "Assets/Game UI/Design"
    data_root = unity_root / relative
    context = store.create(root, "game", "项目", data_root=relative.replace("/", "\\"))
    source = tmp_path / "guide.png"
    with Image.new("RGBA", (12, 10), "red") as image:
        image.save(source)
    reference = register_reference(root, "guide", str(source))
    context = store.add_reference(root, context["revision"], reference)
    registered = store.register_image(root, context["revision"], "icon", "图标", str(source),
                                      {"kind": "imported", "description": "合成图像"})
    context = store.save_program(root, registered["revision"], "drawing", "绘制程序", "", PROGRAM)
    context = store.define_component(root, context["revision"], "sample", "样片",
                                     {"program_id": "drawing", "width": 8, "height": 6})
    context = record_sample_run(store, context, "first", (150, 20, 20, 255))
    comparison = compare(store, context)
    visual = store.create_visual_check(root, comparison["revision"], "review", [{"component_id": "sample", "label": "样片"}],
                                       background="#123456")
    item = export_item() | {"output_path": f"{relative}/exports/ui/button.png"}
    exported = store.export_assets(root, visual["revision"], "sample", [item])
    composition = store.create_composition(root, exported["revision"], "screen", 64, 48, "#123456", [image_node()])
    page = save(store, composition, [image_node()])
    built = store.build_layout(root, page["revision"], "screen")

    index = json.loads((data_root / "smartui.project.json").read_bytes())
    assert index["references"]["guide"]["source"] == f"{relative}/references/guide/guide.png"
    assert index["programs"]["drawing"] == f"{relative}/programs/drawing.json"
    assert index["components"]["sample"] == f"{relative}/components/sample.json"
    assert index["images"]["icon"] == f"{relative}/sources/images/icon.json"
    assert index["layouts"]["screen"] == f"{relative}/layouts/screen.json"
    assert index["exports"]["sample"]["manifest"]["path"] == f"{relative}/exports/manifests/sample.json"
    assert index["page_builds"]["screen"]["files"]["html"]["path"] == f"{relative}/previews/pages/screen/preview.html"
    for record in (index["comparisons"][comparison["comparison"]["id"]], index["visual_checks"]["review"],
                   index["compositions"]["screen"]):
        assert all((unity_root / image["path"]).is_file() and image["path"].startswith(f"{relative}/previews/")
                   for image in record["images"].values())
    assert "(<../programs/drawing.py>)" in (data_root / "docs/components.md").read_text(encoding="utf-8")
    assert not (unity_root / "smart-ui").exists()
    assert not (unity_root / "Assets/SmartUI").exists()

    shutil.rmtree(unity_root / ".smartui")
    source.unlink()
    reopened = ProjectStore().context(root)
    assert reopened["revision"] == built["revision"]
    assert store.get_program(root, "drawing")["source"] == PROGRAM
    assert Path(store.get_image(root, "icon")["image"]["path"]).is_relative_to(data_root)
    assert store.get_page_build(root, "screen")["page_build"] == built["page_build"]
    updated = store.update_style(root, reopened["revision"], "继续制作", [], {}, ["guide"])
    assert updated["data"]["style"]["description"] == "继续制作"


def test_unity_image_replacement_preserves_meta_for_the_same_file(unity_root, tmp_path):
    store = ProjectStore()
    root = str(unity_root)
    context = store.create(root, "game", "项目")
    source = tmp_path / "icon.png"
    with Image.new("RGBA", (6, 4), "red") as image:
        image.save(source)
    provenance = {"kind": "imported", "description": "合成图像"}
    original = store.register_image(root, context["revision"], "icon", "图标", str(source), provenance)
    path = Path(original["image"]["path"])
    meta = path.with_suffix(".png.meta")
    meta.write_bytes(b"fileFormatVersion: 2\nguid: 9f2b8c8fc3e14747880096b173a48938\n")
    original_meta = meta.read_bytes()
    with Image.new("RGBA", (6, 4), "blue") as image:
        image.save(source)
    replacement = store.register_image(root, original["revision"], "icon", "图标", str(source), provenance)

    assert replacement["image"]["path"] == original["image"]["path"]
    assert replacement["image"]["sha256"] != original["image"]["sha256"]
    assert meta.read_bytes() == original_meta

    jpeg = tmp_path / "icon.jpg"
    with Image.new("RGB", (6, 4), "green") as image:
        image.save(jpeg)
    changed = store.register_image(root, replacement["revision"], "icon", "图标", str(jpeg), provenance)
    new_path = Path(changed["image"]["path"])
    assert new_path == path.with_suffix(".jpg")
    assert set(path.parent.iterdir()) == {new_path}


def test_unity_preview_replacement_preserves_nested_asset_and_folder_meta(unity_root):
    ProjectStore().create(str(unity_root), "game", "项目")
    layout = ProjectLayout(unity_root)
    destination = layout.persistent("previews/pages/screen")
    retained = {
        "images.meta": b"guid: images",
        "images/icons.meta": b"guid: icons",
        "images/icons/keep.png.meta": b"guid: keep",
    }
    old = retained | {
        "images/icons/keep.png": b"old pixels",
        "images/icons/removed.png": b"removed pixels",
        "images/icons/removed.png.meta": b"guid: removed",
        "images/icons/new.png.meta": b"guid: orphan",
        "fonts/removed.font": b"removed font",
        "fonts/removed.font.meta": b"guid: removed font",
        "fonts.meta": b"guid: fonts",
    }
    staging = unity_root / ".smartui/temp/test-transaction/current"
    new = {"images/icons/keep.png": b"new pixels", "images/icons/new.png": b"new image"}
    for directory, contents in ((destination, old), (staging, new)):
        for relative, payload in contents.items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    _replace_directory(layout, staging, destination)

    assert {path.relative_to(destination).as_posix(): path.read_bytes()
            for path in destination.rglob("*") if path.is_file()} == new | retained


def test_unity_project_without_assets_does_not_create_a_drawing_project(unity_root):
    (unity_root / "Assets").rmdir()
    with pytest.raises(ProjectError, match="Unity 项目缺少 Assets 目录"):
        ProjectStore().create(str(unity_root), "game", "项目")
    assert not (unity_root / "smart-ui").exists()


@pytest.mark.parametrize("relative", ["Assets", "GameUI", "AssetsOther/UI", "Assets/../UI", "C:/Assets/UI",
                                       "Assets/UI:Stream", "Assets/UI.", "Assets/UI/ Tools"])
def test_unity_rejects_data_root_outside_assets(unity_root, relative):
    with pytest.raises(ProjectError, match="data_root"):
        ProjectStore().create(str(unity_root), "game", "项目", data_root=relative)


def test_unity_rejects_another_data_root_and_multiple_state_files(unity_root):
    store = ProjectStore()
    root = str(unity_root)
    store.create(root, "game", "项目", data_root="Assets/Existing")
    with pytest.raises(ProjectError, match="不能另建"):
        store.create(root, "game", "项目", data_root="Assets/Other")
    assert not (unity_root / "Assets/Other").exists()

    duplicate = unity_root / "Assets/Other"
    duplicate.mkdir()
    shutil.copyfile(unity_root / "Assets/Existing/smartui.project.json", duplicate / "smartui.project.json")
    with pytest.raises(ProjectError, match="多个 SmartUI 制作目录"):
        store.context(root)


def test_unity_rejects_legacy_directory_without_modifying_it(unity_root):
    legacy = unity_root / "smart-ui/project.json"
    legacy.parent.mkdir()
    legacy.write_text("原项目资料", encoding="utf-8")
    with pytest.raises(ProjectError, match="迁移至 Assets 子目录"):
        ProjectStore().create(str(unity_root), "game", "项目")
    assert legacy.read_text(encoding="utf-8") == "原项目资料"
    assert not (unity_root / "Assets/SmartUI").exists()


def test_unity_rejects_linked_data_root_and_managed_children(unity_root, monkeypatch):
    data_root = unity_root / "Assets/Linked"
    real_is_junction = Path.is_junction
    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_junction", lambda path: path == data_root or real_is_junction(path))
        with pytest.raises(ProjectError, match="目录联接"):
            ProjectStore().create(str(unity_root), "game", "项目", data_root="Assets/Linked")
    assert not data_root.exists()

    store = ProjectStore()
    root = str(unity_root)
    context = store.create(root, "game", "项目", data_root="Assets/Linked")
    linked = data_root / "programs"
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or real_is_symlink(path))
    with pytest.raises(ProjectError, match="符号链接"):
        store.save_program(root, context["revision"], "drawing", "绘制程序", "", PROGRAM)
    assert not linked.exists()
    assert ProjectLayout(unity_root).resolve("smart-ui/programs/drawing.py") == unity_root / "smart-ui/programs/drawing.py"
