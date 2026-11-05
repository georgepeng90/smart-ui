"""验证当前视觉检查记录及 MCP 图片响应。"""

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
from test_review_workflow import PROGRAM, RULE, compare, project, project_session, record_sample_run


def _item(label="正常状态", **options):
    return {"component_id": "sample", "label": label, **options}


def _create(store, context, *items):
    return store.create_visual_check(context["project_root"], context["revision"],
                                     "sample-check", list(items), background="#123456", columns=2)


@pytest.mark.parametrize("redraw_id", ["first", "second"])
def test_saved_visual_check_replaces_current_images_after_redrawing(project, redraw_id):
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
        assert path.parent == Path(root) / "smart-ui" / "previews" / "checks" / first["id"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == image["sha256"]
    assert store.get_visual_check(root, first["id"])["visual_check"] == first

    later = record_sample_run(store, store.context(root), redraw_id, (20, 150, 20, 255))
    assert not store.get_visual_check(root, first["id"])["visual_check"]["is_current"]
    current = _create(store, later, _item("修改后", region=[1, 1, 3, 2]))["visual_check"]
    assert current["id"] == first["id"] and current["is_current"]
    assert current["items"][0]["is_current"]
    assert ProjectStore().get_visual_check(root, first["id"])["visual_check"] == current
    with Image.open(current["images"]["item-1"]["path"]) as crop:
        assert crop.size == (3, 2)
        assert crop.getpixel((0, 0)) == (20, 150, 20, 255)

    stored = store.context(root)["data"]
    assert list(stored["visual_checks"]) == [first["id"]]
    assert stored["visual_checks"][first["id"]]["items"][0]["label"] == "修改后"
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


def test_visual_check_rejects_stale_revision_and_components_without_current_artifacts(project):
    store, context = project
    root = Path(context["project_root"])
    spec = context["data"]["components"]["sample"]["spec"]
    latest = store.define_component(str(root), context["revision"], "pending", "未绘制样片", spec)
    before = (root / "smart-ui" / "project.json").read_bytes()
    with pytest.raises(ProjectError, match="revision"):
        _create(store, context, _item())
    with pytest.raises(ProjectError, match="成功产物"):
        _create(store, latest, _item(component_id="pending"))
    assert (root / "smart-ui" / "project.json").read_bytes() == before
    assert not list((root / "smart-ui" / "previews" / "checks").glob("*"))


def test_visual_check_rejects_ids_that_differ_only_in_case(project):
    store, context = project
    first = _create(store, context, _item())
    with pytest.raises(ProjectError, match="check_id.*仅大小写不同"):
        store.create_visual_check(context["project_root"], first["revision"], "SAMPLE-CHECK",
                                  [_item()], background="#123456")
    assert store.get_visual_check(context["project_root"], "sample-check") == first


@pytest.mark.parametrize("key", ["comparison", "visual_check"])
def test_failed_preview_record_write_restores_previous_current_files(project, monkeypatch, key):
    store, context = project
    root = context["project_root"]
    if key == "comparison":
        first = compare(store, context)
        fetch = store.get_comparison

        def replace():
            return store.compare_component(root, first["revision"], "sample", "guide", artifact_scale=2)
    else:
        first = _create(store, context, _item())
        fetch = store.get_visual_check

        def replace():
            return _create(store, first, _item(scale=2))

    saved = first[key]
    index = Path(root) / "smart-ui/project.json"
    before = index.read_bytes()
    originals = {Path(image["path"]): image["sha256"] for image in saved["images"].values()}

    def fail_write(*args):
        assert any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in originals.items())
        raise ProjectError("模拟预览的项目记录写入失败")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_write", fail_write)
        with pytest.raises(ProjectError, match="项目记录写入失败"):
            replace()
    assert index.read_bytes() == before
    assert fetch(root, saved["id"]) == first
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in originals} == originals
    assert not list((Path(root) / ".smartui/temp").iterdir())

    updated = replace()[key]
    assert updated["id"] == saved["id"]
    assert updated["images"] != saved["images"]


@pytest.mark.parametrize(("field", "exception", "message"), [
    ("preview", ValueError, "产物预览图.*SHA-256"),
    ("plan_path", ProjectError, "plan_path.*SHA-256"),
    ("style_snapshot_path", ProjectError, "style_snapshot_path.*SHA-256"),
])
def test_visual_check_rejects_changed_run_evidence(project, field, exception, message):
    store, context = project
    root = Path(context["project_root"])
    run = context["data"]["components"]["sample"]["latest_run"]
    before = (root / "smart-ui" / "project.json").read_bytes()
    Path(run[field]).write_bytes(b"changed run artifact")
    with pytest.raises(exception, match=message):
        _create(store, context, _item())
    assert (root / "smart-ui" / "project.json").read_bytes() == before
    assert not list((root / "smart-ui" / "previews" / "checks").glob("*"))


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
    missing_cli = tmp_path / "missing-photocraft.exe"
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "smartui_mcp"], cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(missing_cli)},
    )
    items = [_item(), _item(label="裁切后的按钮", region=[0, 0, 4, 3], scale=2)]
    arguments = {"project_root": root, "expected_revision": context["revision"], "check_id": "sample-check", "items": items,
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
    assert not missing_cli.exists()


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
