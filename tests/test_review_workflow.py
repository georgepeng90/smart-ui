"""验证持久化对照和偏差处理流程，使用合成绘制记录代替 PhotoCraft。"""

import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
import pytest

from smartui_mcp.pipeline import plan_component
from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256
from smartui_mcp.references import register_reference
from smartui_mcp.server import create_server


PROGRAM = '''def build(parameters, spec, component_id):
    return {
        "schema_version": 1, "width": spec["width"], "height": spec["height"],
        "nodes": [{"key": "outline", "name": "描边", "kind": "pixel", "parent": "root", "strokes": []}],
    }
'''
RULE = {"id": "outline-width", "description": "描边保持均匀", "reference_id": "guide"}
OBSERVATION = "左边描边偏厚"
SUGGESTION = "收窄左边描边"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return str(path)


def record_sample_run(store, context, run_id, color):
    """登记合成图和实际程序快照，仅用于验证项目记录。"""
    root = context["project_root"]
    directory = Path(root) / ".smartui" / "runs" / run_id
    directory.mkdir(parents=True)
    data = context["data"]
    spec = data["components"]["sample"]["spec"]
    program = store.get_program(root, spec["program_id"])
    plan = plan_component(root, "sample", store=store)["plan"]
    document, preview, snapshot = (directory / name for name in ("sample.pcraft", "preview.png", "program.py"))
    document.write_bytes(b"synthetic draw record")
    with Image.new("RGBA", (spec["width"], spec["height"]), color) as image:
        image.save(preview)
    snapshot.write_bytes(program["source"].encode("utf-8"))
    run = {
        "run_id": run_id, "status": "drawn", "style_version": data["style"]["version"], "spec_sha256": spec_sha256(spec),
        "document": str(document), "preview": str(preview), "program_id": program["id"],
        "program_version": program["version"], "program_sha256": program["sha256"], "program_snapshot_path": str(snapshot),
        "operations_path": write_json(directory / "operations.json", []),
        "plan_path": write_json(directory / "plan.json", plan),
        "style_snapshot_path": write_json(directory / "style.json", {
            "style": data["style"], "spec": spec, "component_id": "sample",
        }),
    }
    saved = directory / "output.pcraft"
    saved.write_bytes(document.read_bytes())
    run["source_snapshot_path"] = str(saved)
    run["backend"] = {"name": "photocraft", "version": "synthetic bookkeeping fixture", "sha256": "0" * 64,
                      "cli_path": str(directory / "photocraft-cli")}
    return store.record_run(root, context["revision"], "sample", run)


@pytest.fixture
def project(tmp_path):
    store = ProjectStore()
    root = str(tmp_path / "对照项目")
    context = store.create(root, "demo", "通用对照项目")
    source = tmp_path / "reference.png"
    with Image.new("RGBA", (12, 10), (30, 90, 120, 255)) as image:
        image.save(source)
    reference = register_reference(root, "guide", str(source))
    context = store.add_reference(root, context["revision"], reference)
    context = store.update_style(root, context["revision"], "均匀描边", [RULE], {"outline": 2}, ["guide"])
    context = store.save_program(root, context["revision"], "drawing", "描边程序", "绘制组件描边", PROGRAM)
    context = store.define_component(root, context["revision"], "sample", "样片", {
        "program_id": "drawing", "width": 8, "height": 6,
    })
    return store, record_sample_run(store, context, "first", (150, 20, 20, 255))


def compare(store, context, run_id="first"):
    return store.compare_component(
        context["project_root"], context["revision"], "sample", run_id, "guide",
        reference_region=[1, 1, 3, 2], reference_coordinate_size=[6, 5],
        artifact_region=[1, 0, 4, 3], reference_scale=2.0,
    )


def give_feedback(store, result):
    return store.record_feedback(result["project_root"], result["revision"], result["comparison"]["id"],
                                 OBSERVATION, SUGGESTION, ["outline"], [RULE["id"]])


def test_project_saves_comparison_and_retrieves_its_original_images_after_redrawing(project):
    store, context = project
    root = context["project_root"]
    first = compare(store, context)["comparison"]
    assert first["is_current"]
    assert first["reference"]["region_source_xywh"] == [2, 2, 6, 4]
    assert first["reference"]["display_size"] == [12, 8]
    assert first["artifact"]["region_source_xywh"] == [1, 0, 4, 3]
    assert first["artifact"]["display_size"] == [4, 3]
    assert first["node_keys"] == ["outline"]
    assert first["rule_ids"] == [RULE["id"]]
    state = json.loads((Path(root) / ".smartui" / "project.json").read_text(encoding="utf-8"))
    stored = state["comparisons"][first["id"]]
    assert stored["id"] == first["id"]
    for label, image in stored["images"].items():
        assert not Path(image["path"]).is_absolute()
        assert image["path"] == Path(first["images"][label]["path"]).relative_to(root).as_posix()
    assert store.get_comparison(root, first["id"])["comparison"] == first
    assert set(first["images"]) == {"reference", "artifact", "comparison"}
    for label, metadata in first["images"].items():
        path = Path(metadata["path"])
        assert path.is_absolute()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
        if label == "comparison":
            with Image.open(path) as image:
                assert list(image.size) == first["display"]["size"]

    later = record_sample_run(store, store.context(root), "second", (20, 150, 20, 255))
    second = compare(store, later, "second")["comparison"]
    original_preview = Path(context["data"]["components"]["sample"]["latest_run"]["preview"])
    original_preview.unlink()
    fetched = store.get_comparison(root, first["id"])["comparison"]
    assert fetched == first | {"is_current": False}
    assert second["images"]["comparison"]["sha256"] != first["images"]["comparison"]["sha256"]
    with Image.open(fetched["images"]["artifact"]["path"]) as image:
        assert image.size == (4, 3)
        assert image.getpixel((0, 0)) == (150, 20, 20, 255)


def test_stale_revision_cannot_create_comparisons_or_feedback(project):
    store, context = project
    first = compare(store, context)
    root = Path(context["project_root"])
    before = (root / ".smartui" / "project.json").read_bytes()
    outputs = sorted((root / ".smartui" / "comparisons").rglob("*.png"))
    with pytest.raises(ProjectError, match="revision"):
        compare(store, context)
    with pytest.raises(ProjectError, match="revision"):
        give_feedback(store, first | {"revision": context["revision"]})
    assert (root / ".smartui" / "project.json").read_bytes() == before
    assert sorted((root / ".smartui" / "comparisons").rglob("*.png")) == outputs


@pytest.mark.parametrize("changed", ["program", "style"])
def test_feedback_stays_open_until_explicit_review_after_version_change(project, changed):
    store, context = project
    first = compare(store, context)
    root, original_id = first["project_root"], first["comparison"]["id"]
    added = give_feedback(store, first)
    feedback = added["feedback"]
    assert (feedback["comparison_id"], feedback["target_nodes"], feedback["rule_ids"]) == (original_id, ["outline"], [RULE["id"]])
    if changed == "program":
        context = store.save_program(root, added["revision"], "drawing", "描边程序", "调整绘制源码", PROGRAM + "\n# 复查版\n")
    else:
        context = store.update_style(root, added["revision"], "均匀描边", [RULE], {"outline": 1}, ["guide"])
    assert not store.get_comparison(root, original_id)["comparison"]["is_current"]
    assert context["data"]["components"]["sample"]["needs_review"]

    later = record_sample_run(store, context, "second", (30, 90, 120, 255))
    assert not later["data"]["components"]["sample"]["needs_review"]
    assert later["data"]["components"]["sample"]["open_feedback_count"] == 1
    assert later["data"]["feedback"][feedback["id"]]["history"] == []
    second = compare(store, later, "second")
    second_id = second["comparison"]["id"]
    assert second["comparison"]["is_current"]
    resolved = store.update_feedback(root, second["revision"], feedback["id"], "resolved", "复查后描边一致", second_id)
    assert store.context(root)["data"]["components"]["sample"]["open_feedback_count"] == 0
    reopened = store.update_feedback(root, resolved["revision"], feedback["id"], "open", "复查后仍需调整左边", second_id, "user")
    current = store.context(root)
    assert current["data"]["feedback"][feedback["id"]] == reopened["feedback"]
    assert current["data"]["components"]["sample"]["open_feedback_count"] == 1
    assert current["data"]["style"]["user_confirmation"] is None
    assert current["data"]["components"]["sample"]["latest_run"]["status"] == "drawn"
    assert store.get_comparison(root, second_id)["feedback"][feedback["id"]]["observation"] == OBSERVATION
    document = (Path(root) / "docs" / "ui" / "components.md").read_text(encoding="utf-8")
    for value in (original_id, second_id, OBSERVATION, SUGGESTION):
        assert value in document


@pytest.mark.parametrize(("field", "exception", "message"), [
    ("preview", ValueError, "产物预览图.*SHA-256"),
    ("plan_path", ProjectError, "plan_path.*SHA-256"),
    ("style_snapshot_path", ProjectError, "style_snapshot_path.*SHA-256"),
])
def test_comparison_rejects_modified_run_artifacts_before_recording(project, field, exception, message):
    store, context = project
    root = Path(context["project_root"])
    run = context["data"]["components"]["sample"]["latest_run"]
    before = (root / ".smartui" / "project.json").read_bytes()
    Path(run[field]).write_bytes(b"modified after recording")
    with pytest.raises(exception, match=message):
        compare(store, context)
    assert (root / ".smartui" / "project.json").read_bytes() == before
    assert not list((root / ".smartui" / "comparisons").rglob("*.png"))


def test_modified_comparison_image_cannot_be_read_or_used_for_feedback(project):
    store, context = project
    first = compare(store, context)
    root = Path(context["project_root"])
    before = (root / ".smartui" / "project.json").read_bytes()
    Path(first["comparison"]["images"]["comparison"]["path"]).write_bytes(b"modified after comparison")
    with pytest.raises(ProjectError, match="对照图片 SHA-256"):
        store.get_comparison(str(root), first["comparison"]["id"])
    with pytest.raises(ProjectError, match="对照图片 SHA-256"):
        give_feedback(store, first)
    assert (root / ".smartui" / "project.json").read_bytes() == before


def test_mcp_validates_the_image_bytes_it_actually_returns(project, monkeypatch):
    store, context = project
    first = compare(store, context)
    original_get = ProjectStore.get_comparison

    def replace_after_validation(self, project_root, comparison_id):
        result = original_get(self, project_root, comparison_id)
        Path(result["comparison"]["images"]["comparison"]["path"]).write_bytes(b"changed before MCP response")
        return result

    monkeypatch.setattr(ProjectStore, "get_comparison", replace_after_validation)
    with pytest.raises(Exception, match="返回的对照图片 SHA-256"):
        asyncio.run(create_server().call_tool("project_get_comparison", {
            "project_root": context["project_root"], "comparison_id": first["comparison"]["id"],
        }))


@asynccontextmanager
async def project_session(parameters):
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(session, name, *, image=False, **arguments):
    result = await session.call_tool(name, arguments)
    assert not result.isError, result.content
    payload = json.loads(result.content[0].text)
    if image:
        assert result.structuredContent is None
        assert len(result.content) == 2
        assert result.content[1].mimeType == "image/png"
        png = base64.b64decode(result.content[1].data)
        assert hashlib.sha256(png).hexdigest() == payload["comparison"]["images"]["comparison"]["sha256"]
    return payload


def test_stdio_review_tools_persist_feedback_across_processes(project, tmp_path):
    store, context = project
    root = context["project_root"]
    missing_cli = tmp_path / "not-installed-photocraft.exe"
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "smartui_mcp"], cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(missing_cli)},
    )

    async def workflow():
        async with project_session(parameters) as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            required = {
                "project_compare_component": {"project_root", "expected_revision", "component_id", "run_id", "reference_id"},
                "project_get_comparison": {"project_root", "comparison_id"},
                "project_record_feedback": {"project_root", "expected_revision", "comparison_id", "observation", "suggested_change"},
                "project_update_feedback": {"project_root", "expected_revision", "feedback_id", "status", "comment", "comparison_id"},
            }
            for name, fields in required.items():
                assert set(tools[name].inputSchema["required"]) == fields
            for name in ("project_compare_component", "project_get_comparison"):
                assert tools[name].outputSchema is None

            arguments = {"project_root": root, "component_id": "sample", "reference_id": "guide"}
            for invalid in ({"reference_scale": True}, {"artifact_scale": "1.5"},
                            {"artifact_region": [False, "0", 2.0, 1]},
                            {"reference_coordinate_size": [6.0, 5]}, {"reference_region": [0, 0, True, 2]}):
                failure = await session.call_tool("project_compare_component", {
                    **arguments, "run_id": "first", "expected_revision": context["revision"], **invalid,
                })
                assert failure.isError, invalid
                assert store.context(root)["revision"] == context["revision"]
                assert not list((Path(root) / ".smartui" / "comparisons").rglob("*.png"))
            first = await call_tool(session, "project_compare_component", image=True, run_id="first",
                                    expected_revision=context["revision"], **arguments)
            feedback = await call_tool(session, "project_record_feedback", project_root=root,
                                       expected_revision=first["revision"], comparison_id=first["comparison"]["id"],
                                       observation=OBSERVATION, suggested_change=SUGGESTION)
            later = record_sample_run(store, store.context(root), "second", (30, 90, 120, 255))
            second = await call_tool(session, "project_compare_component", image=True, run_id="second",
                                     expected_revision=later["revision"], **arguments)
            feedback_id = feedback["feedback"]["id"]
            comparison_id = second["comparison"]["id"]
            resolved = await call_tool(session, "project_update_feedback", project_root=root,
                                       expected_revision=second["revision"], feedback_id=feedback_id,
                                       status="resolved", comment="已比较更新后的绘制结果", comparison_id=comparison_id)
            assert resolved["feedback"]["status"] == "resolved"

        async with project_session(parameters) as session:
            restored = await call_tool(session, "project_context", project_root=root)
            assert restored["revision"] == resolved["revision"]
            assert restored["feedback"][feedback_id]["latest_update"]["comparison_id"] == comparison_id
            assert restored["feedback"][feedback_id]["status"] == "resolved"
            assert restored["components"]["sample"]["open_feedback_count"] == 0
            fetched = await call_tool(session, "project_get_comparison", image=True, project_root=root, comparison_id=comparison_id)
            assert fetched["feedback"][feedback_id] == resolved["feedback"]
            assert fetched["comparison"] == second["comparison"]

    asyncio.run(workflow())
    assert not missing_cli.exists()
