"""验证工程目录的可迁移性、配置约束和用户数据边界。"""

import json
from pathlib import Path
import shutil

import pytest

from smartui_mcp.layout import DEFAULT_PATHS, ProjectLayout, map_state_paths
from smartui_mcp.projects import ProjectError, ProjectStore
from smartui_mcp.references import register_reference, inspect_reference
from PIL import Image


@pytest.mark.parametrize("paths", [
    {"unknown": "foo"}, {"sources": "../other"}, {"programs": "C:/other"},
    {"documents": ".smartui/docs"}, {"sources": "docs/ui/sources"},
    {"programs": ""}, {"references": "."},
])
def test_invalid_layout_is_rejected_before_project_creation(tmp_path, paths):
    root = tmp_path / "game"
    with pytest.raises(ProjectError, match="paths"):
        ProjectStore().create(str(root), "game", "项目", paths)
    assert not root.exists()


def test_custom_layout_and_relative_links_survive_project_move(tmp_path):
    store = ProjectStore()
    root = tmp_path / "original"
    paths = {"documents": "design/ui/docs", "references": "design/ui/refs",
             "programs": "design/ui/programs", "sources": "design/ui/krita"}
    context = store.create(str(root), "game", "项目", paths)
    source = tmp_path / "reference.png"
    Image.new("RGB", (10, 10), "red").save(source)
    reference = register_reference(str(root), "guide", str(source), paths=paths)
    context = store.add_reference(str(root), context["revision"], reference)
    context = store.save_program(str(root), context["revision"], "drawing", "程序", "", "result = []\n")
    state_file = root / ".smartui/project.json"
    payload = state_file.read_bytes()
    stored = json.loads(payload)
    assert stored["references"]["guide"]["source"] == "design/ui/refs/guide/reference.png"
    assert stored["programs"]["drawing"]["source_path"] == "design/ui/programs/drawing/1.py"
    assert "(<../refs/guide/reference.png>)" in (root / "design/ui/docs/style.md").read_text(encoding="utf-8")
    assert "(<../programs/drawing/1.py>)" in (root / "design/ui/docs/components.md").read_text(encoding="utf-8")
    moved = tmp_path / "moved"
    shutil.move(str(root), str(moved))
    source.unlink()
    context = store.context(str(moved))
    assert (moved / ".smartui/project.json").read_bytes() == payload
    reference = context["data"]["references"]["guide"]
    assert Path(reference["source"]).is_relative_to(moved)
    inspected = inspect_reference(str(moved), reference)
    assert Path(inspected["preview"]).is_file()
    assert store.get_program(str(moved), "drawing")["source"] == "result = []\n"
    store.update_style(str(moved), context["revision"], "继续制作", [], {}, ["guide"])


def test_path_conversion_does_not_rewrite_project_parameters_or_feedback(tmp_path):
    user_data = {"source": "model", "path": "../a-user-defined-value"}
    state = {"references": {}, "components": {}, "style": {"parameters": user_data},
             "feedback": {"item": user_data}}
    converted = map_state_paths(state, lambda value: "changed")
    assert converted == state
    assert converted is not state


def test_gitignore_keeps_shared_project_data_and_preserves_existing_rules(tmp_path):
    root = tmp_path / "game"
    (root / ".smartui").mkdir(parents=True)
    ignore = root / ".smartui/.gitignore"
    ignore.write_text("my-local-notes.txt\n", encoding="utf-8")
    ProjectStore().create(str(root), "game", "项目")
    rules = ignore.read_text(encoding="utf-8").splitlines()
    assert "my-local-notes.txt" in rules
    assert {"cache/", "project.lock", "migration-backups/"} <= set(rules)
    assert not {"*", "project.json", "runs/", "comparisons/", "visual-checks/"} & set(rules)


def test_incomplete_persisted_layout_does_not_silently_get_defaults(tmp_path):
    store = ProjectStore()
    store.create(str(tmp_path), "game", "项目")
    file = tmp_path / ".smartui/project.json"
    state = json.loads(file.read_bytes())
    del state["paths"]["sources"]
    file.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ProjectError, match="paths 必须完整声明"):
        store.context(str(tmp_path))
