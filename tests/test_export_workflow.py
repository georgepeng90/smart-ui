"""正式导出与项目锁、历史读取及 MCP 的整合行为。"""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
from mcp import StdioServerParameters

from smartui_mcp.projects import ProjectError
from test_review_workflow import project, project_session, record_sample_run


def export_item():
    return {"asset_id": "button", "name": "按钮底板", "output_path": "Assets/UI/button.png",
            "region": {"x": 0, "y": 0, "width": 8, "height": 6}, "state": "normal", "purpose": "按钮背景",
            "geometry": {"mode": "nine_slice", "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4},
                         "borders": {"left": 2, "top": 2, "right": 2, "bottom": 2}}}


def test_export_repeat_versions_and_history_survive_move(project, tmp_path):
    store, context = project
    root = context["project_root"]
    first = store.export_assets(root, context["revision"], "sample", "first", [export_item()])
    record = first["export"]
    path = Path(root) / "Assets/UI/button.png"
    before = path.read_bytes(), path.stat().st_mtime_ns
    again = store.export_assets(root, first["revision"], "sample", "first", [export_item()])
    assert again["export"]["items"][0]["reused"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert len(store.context(root)["data"]["images"]["button"]["versions"]) == 1
    assert store.context(root)["data"]["images"]["button"]["selected_version"] is None
    later = record_sample_run(store, store.context(root), "second", (20, 140, 240, 255))
    updated = store.export_assets(root, later["revision"], "sample", "second", [export_item()])
    assert updated["export"]["items"][0]["version"] == 2
    assert path.read_bytes() != before[0]
    assert store.get_export(root, record["id"])["export"] == record
    moved = tmp_path / "移动后的项目"
    shutil.move(root, moved)
    restored = store.get_export(str(moved), record["id"])["export"]
    assert Path(restored["manifest"]["path"]).is_relative_to(moved)
    assert Path(restored["items"][0]["record"]["path"]).read_bytes() == before[0]
    raw = json.loads((moved / ".smartui/project.json").read_text(encoding="utf-8"))
    assert not Path(raw["exports"][record["id"]]["manifest"]["path"]).is_absolute()
    current = store.context(str(moved))
    repeat = store.export_assets(str(moved), current["revision"], "sample", "second", [export_item()])
    assert repeat["export"]["items"][0]["reused"]


def test_export_checks_revision_and_entire_batch_before_write(project):
    store, context = project
    root = Path(context["project_root"])
    definition = export_item()
    invalid = deepcopy(definition) | {"asset_id": "bad", "output_path": "Assets/UI/bad.png"}
    invalid["region"]["width"] = 500
    with pytest.raises(ValueError, match="region"):
        store.export_assets(str(root), context["revision"], "sample", "first", [definition, invalid])
    assert not (root / "Assets").exists()
    assert "button" not in store.context(str(root))["data"]["images"]
    first = store.export_assets(str(root), context["revision"], "sample", "first", [definition])
    before = (root / ".smartui/project.json").read_bytes()
    with pytest.raises(ProjectError, match="revision"):
        store.export_assets(str(root), context["revision"], "sample", "first", [definition])
    assert (root / ".smartui/project.json").read_bytes() == before
    manifest = Path(first["export"]["manifest"]["path"])
    manifest.write_bytes(b"changed")
    with pytest.raises(ProjectError, match="SHA-256"):
        store.get_export(str(root), first["export"]["id"])


def test_export_tools_work_over_stdio(project):
    store, context = project

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"],
                                           env={**os.environ, "PYTHONUTF8": "1"})
        async with project_session(parameters) as session:
            result = await session.call_tool("project_export_assets", {
                "project_root": context["project_root"], "expected_revision": context["revision"],
                "component_id": "sample", "run_id": "first", "exports": [export_item()],
            })
            assert not result.isError, result
            exported = json.loads(result.content[0].text)
            fetched = await session.call_tool("project_get_export", {
                "project_root": context["project_root"], "export_id": exported["export"]["id"],
            })
            assert not fetched.isError, fetched
            assert json.loads(fetched.content[0].text)["export"] == exported["export"]

    asyncio.run(scenario())
