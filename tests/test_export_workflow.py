"""验证当前正式导出、项目锁和 MCP 的整合行为。"""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from mcp import StdioServerParameters

from smartui_mcp.projects import ProjectError
from test_review_workflow import project, project_session, record_sample_run


def export_item():
    return {"asset_id": "button", "name": "按钮底板", "output_path": "smart-ui/exports/ui/button.png",
            "region": {"x": 0, "y": 0, "width": 8, "height": 6}, "state": "normal", "purpose": "按钮背景",
            "geometry": {"mode": "nine_slice", "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4},
                         "borders": {"left": 2, "top": 2, "right": 2, "bottom": 2}}}


def test_export_updates_current_asset_and_survives_project_move(project, tmp_path):
    store, context = project
    root = context["project_root"]
    first = store.export_assets(root, context["revision"], "sample", [export_item()])
    record = first["export"]
    path = Path(root) / export_item()["output_path"]
    before = path.read_bytes(), path.stat().st_mtime_ns
    again = store.export_assets(root, first["revision"], "sample", [export_item()])
    assert again["export"]["items"] == record["items"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    later = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    updated = store.export_assets(root, later["revision"], "sample", [export_item()])["export"]
    updated_bytes = path.read_bytes()
    assert updated_bytes != before[0]
    assert updated["id"] == record["id"]
    assert updated["manifest"]["path"] == record["manifest"]["path"]
    assert store.get_export(root, record["id"])["export"] == updated
    assert store.get_image(root, "button")["image"]["sha256"] == updated["items"][0]["record"]["sha256"]
    moved = tmp_path / "移动后的项目"
    shutil.move(root, moved)
    restored = store.get_export(str(moved), record["id"])["export"]
    assert Path(restored["manifest"]["path"]).is_relative_to(moved)
    assert Path(restored["items"][0]["record"]["path"]).read_bytes() == updated_bytes
    raw = json.loads((moved / "smart-ui/project.json").read_text(encoding="utf-8"))
    assert not Path(raw["exports"][record["id"]]["manifest"]["path"]).is_absolute()
    current = store.context(str(moved))
    repeat = store.export_assets(str(moved), current["revision"], "sample", [export_item()])
    assert repeat["export"]["items"] == restored["items"]


def test_export_checks_revision_and_entire_batch_before_write(project):
    store, context = project
    root = Path(context["project_root"])
    definition = export_item()
    invalid = deepcopy(definition) | {"asset_id": "bad", "output_path": "smart-ui/exports/ui/bad.png"}
    invalid["region"]["width"] = 500
    with pytest.raises(ValueError, match="region"):
        store.export_assets(str(root), context["revision"], "sample", [definition, invalid])
    assert not (root / "smart-ui/exports").exists()
    assert "button" not in store.context(str(root))["data"]["images"]
    first = store.export_assets(str(root), context["revision"], "sample", [definition])
    before = (root / "smart-ui/project.json").read_bytes()
    with pytest.raises(ProjectError, match="revision"):
        store.export_assets(str(root), context["revision"], "sample", [definition])
    assert (root / "smart-ui/project.json").read_bytes() == before
    manifest = Path(first["export"]["manifest"]["path"])
    manifest.write_bytes(b"changed")
    with pytest.raises(ProjectError, match="SHA-256"):
        store.get_export(str(root), first["export"]["id"])


def test_git_checkout_preserves_program_and_export_manifest_bytes(project):
    store, context = project
    root = Path(context["project_root"])
    attributes = root / "smart-ui/.gitattributes"
    assert attributes.read_bytes().splitlines()[-1] == b"* -text"
    existing = b"# Existing project rules\r\n*.py text eol=crlf"
    attributes.write_bytes(existing)
    root_attributes = root / ".gitattributes"
    root_rules = b"* text eol=crlf\n"
    root_attributes.write_bytes(root_rules)

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout

    git("init", "-q")
    git("config", "core.autocrlf", "true")
    git("config", "core.safecrlf", "false")
    source = "# 混合换行\r\ndef build(parameters, spec, component_id):\n    return {}\n"
    saved = store.save_program(str(root), context["revision"], "mixed", "混合换行程序", "", source)
    exported = store.export_assets(str(root), saved["revision"], "sample", [export_item()])
    manifest = Path(exported["export"]["manifest"]["path"])
    program = root / "smart-ui/programs/mixed.py"
    assert attributes.read_bytes().startswith(existing + b"\n")
    assert root_attributes.read_bytes() == root_rules

    expected = {path: path.read_bytes() for path in (program, manifest)}
    git("add", "--", "smart-ui")
    for path, payload in expected.items():
        assert git("show", ":" + path.relative_to(root).as_posix()) == payload
        path.unlink()
    git("checkout", "--", "smart-ui")
    assert all(path.read_bytes() == payload for path, payload in expected.items())
    assert store.get_program(str(root), "mixed")["source"] == source
    assert store.get_export(str(root), exported["export"]["id"]) == exported


def test_export_tools_work_over_stdio(project):
    store, context = project

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"],
                                           env={**os.environ, "PYTHONUTF8": "1"})
        async with project_session(parameters) as session:
            result = await session.call_tool("project_export_assets", {
                "project_root": context["project_root"], "expected_revision": context["revision"],
                "component_id": "sample", "exports": [export_item()],
            })
            assert not result.isError, result
            exported = json.loads(result.content[0].text)
            fetched = await session.call_tool("project_get_export", {
                "project_root": context["project_root"], "export_id": exported["export"]["id"],
            })
            assert not fetched.isError, fetched
            assert json.loads(fetched.content[0].text)["export"] == exported["export"]

    asyncio.run(scenario())
