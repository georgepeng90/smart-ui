"""验证固定目录、工程移动后的可用性及用户数据边界。"""

import json
from pathlib import Path
import shutil

import pytest

from smartui_mcp.layout import map_state_paths
from smartui_mcp.projects import ProjectError, ProjectStore
from smartui_mcp.references import register_reference, inspect_reference
from PIL import Image


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
