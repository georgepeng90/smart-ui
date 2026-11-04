"""验证视觉检查记录、历史快照及 MCP 图片响应。"""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from mcp import StdioServerParameters
from PIL import Image
import pytest

from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256
from smartui_mcp.server import create_server
from test_review_workflow import PROGRAM, RULE, project, project_session, record_sample_run


def _item(run_id="first", label="正常状态", **options):
    return {"component_id": "sample", "run_id": run_id, "label": label, **options}


def _create(store, context, *items):
    return store.create_visual_check(context["project_root"], context["revision"],
                                     list(items), background="#123456", columns=2)


def test_saved_visual_check_keeps_historical_run_and_original_pixels(project):
    store, context = project
    root = context["project_root"]
    spec = context["data"]["components"]["sample"]["spec"]
    run = context["data"]["components"]["sample"]["latest_run"]
    first = _create(store, context, _item(region=[1, 1, 3, 2], scale=2))["visual_check"]
    assert first["is_current"] and first["items"][0]["is_current"]
    entry = first["items"][0]
    assert entry["spec"] == spec
    assert entry["run"]["spec_sha256"] == spec_sha256(spec)
    assert entry["run"]["program_sha256"] == run["program_sha256"]
    assert entry["sha256"] == run["artifact_hashes"]["preview"]
    assert first["project_revision"] == context["revision"]
    for image in first["images"].values():
        path = Path(image["path"])
        assert path.parent == Path(root) / ".smartui" / "visual-checks" / first["id"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == image["sha256"]
    assert store.get_visual_check(root, first["id"])["visual_check"] == first

    later = record_sample_run(store, store.context(root), "second", (20, 150, 20, 255))
    mixed = _create(store, later, _item(label="修改前"), _item("second", "修改后"))["visual_check"]
    assert not mixed["is_current"]
    assert [item["is_current"] for item in mixed["items"]] == [False, True]
    Path(run["preview"]).unlink()
    historical = ProjectStore().get_visual_check(root, first["id"])["visual_check"]
    assert historical == first | {"is_current": False, "items": [entry | {"is_current": False}]}
    with Image.open(historical["images"]["item-1"]["path"]) as crop:
        assert crop.size == (3, 2)
        assert crop.getpixel((0, 0)) == (150, 20, 20, 255)

    document = (Path(root) / "docs" / "ui" / "components.md").read_text(encoding="utf-8")
    for value in (first["id"], mixed["id"], "正常状态", "修改前", "修改后", "overview.png"):
        assert value in document
    stored = store.context(root)["data"]
    assert stored["style"]["user_confirmation"] is None
    assert stored["components"]["sample"]["user_confirmation"] is None


@pytest.mark.parametrize("change", ["spec", "style", "program"])
def test_visual_check_tracks_current_run_when_component_requirements_change(project, change):
    store, context = project
    result = _create(store, context, _item())
    root, revision = result["project_root"], result["revision"]
    assert result["visual_check"]["is_current"]
    if change == "spec":
        spec = context["data"]["components"]["sample"]["spec"]
        store.define_component(root, revision, "sample", "样片", spec | {"width": 9})
    elif change == "style":
        store.update_style(root, revision, "均匀描边", [RULE], {"outline": 3}, ["guide"])
    else:
        store.save_program(root, revision, "drawing", "描边程序", "调整程序", PROGRAM.replace('"height": spec["height"]', '"height": spec["height"] + 1'))

    first = result["visual_check"]
    fetched = store.get_visual_check(root, first["id"])["visual_check"]
    assert fetched == first | {"is_current": False, "items": [first["items"][0] | {"is_current": False}]}


def test_visual_check_rejects_stale_revision_and_ineligible_runs_without_writing(project):
    store, context = project
    root = Path(context["project_root"])
    latest = store.record_run(str(root), context["revision"], "sample", {
        "run_id": "failed", "status": "failed", "error": "合成记录用于验证失败状态",
    })
    before = (root / ".smartui" / "project.json").read_bytes()
    with pytest.raises(ProjectError, match="revision"):
        _create(store, context, _item())
    for run_id in ("missing", "failed"):
        with pytest.raises(ProjectError, match="成功产物"):
            _create(store, latest, _item(run_id))
    assert (root / ".smartui" / "project.json").read_bytes() == before
    assert not list((root / ".smartui" / "visual-checks").glob("*"))


@pytest.mark.parametrize(("field", "exception", "message"), [
    ("preview", ValueError, "产物预览图.*SHA-256"),
    ("plan_path", ProjectError, "plan_path.*SHA-256"),
    ("style_snapshot_path", ProjectError, "style_snapshot_path.*SHA-256"),
])
def test_visual_check_rejects_changed_run_evidence(project, field, exception, message):
    store, context = project
    root = Path(context["project_root"])
    run = context["data"]["components"]["sample"]["latest_run"]
    before = (root / ".smartui" / "project.json").read_bytes()
    Path(run[field]).write_bytes(b"changed run artifact")
    with pytest.raises(exception, match=message):
        _create(store, context, _item())
    assert (root / ".smartui" / "project.json").read_bytes() == before
    assert not list((root / ".smartui" / "visual-checks").glob("*"))


@pytest.mark.parametrize("key", ["item-1", "overview"])
def test_visual_check_rejects_corrupted_saved_files(project, key):
    store, context = project
    result = _create(store, context, _item())
    record = result["visual_check"]
    Path(record["images"][key]["path"]).write_bytes(b"changed saved image")

    with pytest.raises(ProjectError, match="视觉检查图片 SHA-256 不符"):
        store.get_visual_check(result["project_root"], record["id"])


def _mcp_visual_check(response):
    assert not response.isError, response.content
    assert response.structuredContent is None
    assert len(response.content) == 3
    result = json.loads(response.content[0].text)
    images = result["visual_check"]["images"]
    for content, key in zip(response.content[1:], ("overview", "grayscale"), strict=True):
        assert content.mimeType == "image/png"
        payload = base64.b64decode(content.data)
        assert hashlib.sha256(payload).hexdigest() == images[key]["sha256"]
    return result


def test_stdio_visual_check_persists_and_returns_verified_overviews_across_processes(project, tmp_path):
    store, context = project
    root = context["project_root"]
    bridge_root = tmp_path / "unused-bridge"
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "smartui_mcp"], cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "SMARTUI_BRIDGE_DIR": str(bridge_root), "SMARTUI_BRIDGE_TIMEOUT": "0.1"},
    )
    items = [_item(), _item(label="裁切后的按钮", region=[0, 0, 4, 3], scale=2)]
    arguments = {"project_root": root, "expected_revision": context["revision"], "items": items,
                 "background": "#101820", "columns": 2}

    async def workflow():
        async with project_session(parameters) as session:
            for invalid in ({"items": [_item(scale=True)]}, {"items": [_item(region=[False, 0, 4, 3])]},
                            {"columns": True}, {"grayscale": "false"}):
                failure = await session.call_tool("project_create_visual_check", arguments | invalid)
                assert failure.isError, invalid
                assert store.context(root)["revision"] == context["revision"]
            created = _mcp_visual_check(await session.call_tool("project_create_visual_check", arguments))
            record = created["visual_check"]
            assert record["display"]["background"] == "#101820"
            assert record["items"][1]["display_size"] == [8, 6]

        async with project_session(parameters) as session:
            summary = await session.call_tool("project_context", {"project_root": root})
            assert not summary.isError, summary.content
            project_summary = json.loads(summary.content[0].text)
            assert project_summary["revision"] == created["revision"]
            compact = project_summary["visual_checks"][record["id"]]
            assert compact["is_current"]
            assert [item["label"] for item in compact["items"]] == [item["label"] for item in items]
            fetched = _mcp_visual_check(await session.call_tool("project_get_visual_check", {
                "project_root": root, "check_id": record["id"],
            }))
            assert fetched == created

    asyncio.run(workflow())
    assert not list((bridge_root / "requests").glob("*.json"))


def test_mcp_rechecks_bytes_after_project_store_verification(project, monkeypatch):
    store, context = project
    result = _create(store, context, _item())
    original_get = ProjectStore.get_visual_check

    def replace_image_after_validation(self, root, check_id):
        verified = original_get(self, root, check_id)
        Path(verified["visual_check"]["images"]["grayscale"]["path"]).write_bytes(b"changed before MCP read")
        return verified

    monkeypatch.setattr(ProjectStore, "get_visual_check", replace_image_after_validation)
    with pytest.raises(Exception, match="返回的视觉检查灰度图 SHA-256 不符"):
        asyncio.run(create_server().call_tool("project_get_visual_check", {
            "project_root": result["project_root"], "check_id": result["visual_check"]["id"],
        }))
