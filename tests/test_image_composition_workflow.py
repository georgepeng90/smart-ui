"""验证图像选用、组件组合、历史快照和跨进程 MCP 调用。"""

import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from mcp import StdioServerParameters
from PIL import Image
import pytest

from smartui_mcp.projects import ProjectError, ProjectStore
from test_compositions import text_node
from test_review_workflow import project, project_session


GENERATED = {"kind": "generated", "tool": "合成测试", "prompt": "货币图标"}
EDITED = {"kind": "edited", "tool": "合成测试", "prompt": "调整图标颜色"}


def _source(tmp_path, name="icon", color=(20, 160, 40, 255)):
    path = tmp_path / f"{name}.png"
    with Image.new("RGBA", (4, 4), color) as image:
        image.save(path)
    return path


def _register(store, context, source, provenance=GENERATED):
    return store.register_image(context["project_root"], context["revision"], "coin", "货币", str(source), provenance)


def _nodes(version=1, x=12):
    return [{"id": "button", "kind": "component", "component_id": "sample", "run_id": "first",
             "x": 1, "y": 1, "width": 8, "height": 6},
            {"id": "coin", "kind": "image", "asset_id": "coin", "version": version,
             "x": x, "y": 1, "width": 4, "height": 4}]


def _compose(store, context, nodes=None):
    return store.create_composition(context["project_root"], context["revision"], 64, 48, "#123456",
                                    _nodes() if nodes is None else nodes)


def _state(root):
    return (Path(root) / ".smartui/project.json").read_bytes()


def test_revision_conflict_does_not_archive_a_candidate_or_publish_composition(project, tmp_path):
    store, context = project
    root = context["project_root"]
    source = _source(tmp_path)
    latest = _register(store, context, source)
    before = _state(root)
    files = sorted(path.relative_to(root).as_posix() for path in Path(root).rglob("*") if path.is_file())
    with pytest.raises(ProjectError, match="revision"):
        _register(store, context, source)
    with pytest.raises(ProjectError, match="revision"):
        _compose(store, context)
    assert _state(root) == before
    assert sorted(path.relative_to(root).as_posix() for path in Path(root).rglob("*") if path.is_file()) == files
    assert latest["image"]["selected_version"] is None
    assert latest["registered_version"] == 1


@pytest.mark.parametrize("missing", ["preview", "preview_hash"])
def test_component_missing_preview_evidence_reports_the_run_and_field(project, missing):
    store, context = project
    root = Path(context["project_root"])
    path = root / ".smartui/project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    component = data["components"]["sample"]
    for run in [*component["runs"], component["latest_run"]]:
        if missing == "preview":
            run.pop("preview")
        else:
            run["artifact_hashes"].pop("preview")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    current = store.context(str(root))
    with pytest.raises(ProjectError, match="first.*preview"):
        _compose(store, current, [_nodes()[0]])
    assert not list((root / ".smartui/compositions").glob("*"))


def test_candidate_selection_and_layout_edits_preserve_image_and_history(project, tmp_path):
    store, context = project
    root = context["project_root"]
    run_before = deepcopy(context["data"]["components"]["sample"]["runs"])
    registered = _register(store, context, _source(tmp_path))
    selected = store.select_image(root, registered["revision"], "coin", 1)
    first = _compose(store, selected)
    original = first["composition"]
    assert original["is_current"]
    assert original["nodes"][0]["run"]["run_id"] == "first"
    with Image.open(original["images"]["overview"]["path"]) as image:
        assert image.getpixel((1, 1)) == (150, 20, 20, 255)
        assert image.getpixel((12, 1)) == (20, 160, 40, 255)

    candidate = _register(store, first, _source(tmp_path, "edited", (40, 60, 220, 255)), EDITED)
    assert candidate["registered_version"] == 2
    assert candidate["image"]["selected_version"] == 1
    assert store.get_composition(root, original["id"])["composition"] == original
    selected = store.select_image(root, candidate["revision"], "coin", 2)
    historical = store.get_composition(root, original["id"])["composition"]
    assert not historical["is_current"]
    assert historical["nodes"][0]["is_current"]
    assert not historical["nodes"][1]["is_current"]
    assert historical["images"] == original["images"]
    assert historical["nodes"][1]["sha256"] == original["nodes"][1]["sha256"]

    updated = _compose(store, selected, _nodes(version=2))
    images_before_layout = deepcopy(store.context(root)["data"]["images"])
    moved = _compose(store, updated, _nodes(version=2, x=20))
    assert moved["composition"]["is_current"]
    assert moved["composition"]["images"]["overview"]["sha256"] != updated["composition"]["images"]["overview"]["sha256"]
    assert store.context(root)["data"]["images"] == images_before_layout
    assert store.context(root)["data"]["components"]["sample"]["runs"] == run_before
    assert moved["composition"]["nodes"][1]["sha256"] == updated["composition"]["nodes"][1]["sha256"]
    assert store.context(root)["data"]["style"]["user_confirmation"] is None


def test_saved_composition_survives_source_deletion_and_detects_corruption(project, tmp_path, text_node):
    store, context = project
    root = context["project_root"]
    registered = _register(store, context, _source(tmp_path))
    selected = store.select_image(root, registered["revision"], "coin", 1)
    label = text_node | {"x": 2, "y": 16}
    result = _compose(store, selected, _nodes() + [label])
    record = result["composition"]
    Path(registered["image"]["versions"][0]["path"]).unlink()
    Path(context["data"]["components"]["sample"]["latest_run"]["preview"]).unlink()
    Path(label["font_path"]).unlink()
    assert store.get_composition(root, record["id"])["composition"] == record
    font = next(iter(record["fonts"].values()))
    Path(font["path"]).write_bytes(b"changed font snapshot")
    with pytest.raises(ProjectError, match="组合快照 SHA-256"):
        store.get_composition(root, record["id"])


def test_project_move_keeps_relative_image_composition_font_paths_and_document_links(project, tmp_path, text_node):
    store, context = project
    root = Path(context["project_root"])
    font = root / "art/ui/sources/fixture.ttf"
    font.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(text_node["font_path"], font)
    registered = _register(store, context, _source(tmp_path), {"kind": "imported", "description": "现有图像"})
    selected = store.select_image(str(root), registered["revision"], "coin", 1)
    result = _compose(store, selected, _nodes() + [text_node | {"font_path": str(font), "x": 2, "y": 16}])
    record = result["composition"]
    payload = _state(root)
    stored = json.loads(payload)
    assert not Path(stored["images"]["coin"]["versions"][0]["path"]).is_absolute()
    for info in [*stored["compositions"][record["id"]]["images"].values(),
                 *stored["compositions"][record["id"]]["fonts"].values()]:
        assert not Path(info["path"]).is_absolute()
    document = (root / "docs/ui/components.md").read_text(encoding="utf-8")
    assert str(root) not in document
    assert "../../art/ui/sources/images/coin/1/image.png" in document
    assert f"../../.smartui/compositions/{record['id']}/overview.png" in document
    moved_root = tmp_path / "moved-project"
    shutil.move(str(root), str(moved_root))
    reopened = ProjectStore()
    assert _state(moved_root) == payload
    image = reopened.get_image(str(moved_root), "coin", 1)["image_version"]
    assert Path(image["path"]).is_relative_to(moved_root)
    moved = reopened.get_composition(str(moved_root), record["id"])["composition"]
    for info in [*moved["images"].values(), *moved["fonts"].values()]:
        assert Path(info["path"]).is_relative_to(moved_root)
        assert hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest() == info["sha256"]
    assert Path(moved["nodes"][-1]["font_path"]).is_relative_to(moved_root)


def _response(response):
    assert not response.isError, response.content
    return json.loads(response.content[0].text)


def _image_response(response, expected_hash):
    assert not response.isError, response.content
    assert len(response.content) == 2
    assert response.content[1].mimeType == "image/png"
    assert hashlib.sha256(base64.b64decode(response.content[1].data)).hexdigest() == expected_hash
    return _response(response)


def test_stdio_registration_selection_composition_and_strict_parameters(project, tmp_path):
    store, context = project
    root = context["project_root"]
    source = _source(tmp_path)
    missing_cli = tmp_path / "missing-photocraft.exe"
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "smartui_mcp"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "SMARTUI_PHOTOCRAFT_CLI": str(missing_cli)})

    async def workflow():
        async with project_session(parameters) as session:
            registered = _response(await session.call_tool("project_register_image", {
                "project_root": root, "expected_revision": context["revision"], "asset_id": "coin", "name": "货币",
                "source_path": str(source), "provenance": GENERATED}))
            assert registered["image"]["selected_version"] is None
            failure = await session.call_tool("project_select_image", {
                "project_root": root, "expected_revision": registered["revision"], "asset_id": "coin", "version": True})
            assert failure.isError
            assert store.context(root)["revision"] == registered["revision"]
            selected = _response(await session.call_tool("project_select_image", {
                "project_root": root, "expected_revision": registered["revision"], "asset_id": "coin", "version": 1}))
            arguments = {"project_root": root, "expected_revision": selected["revision"], "width": 64,
                         "height": 48, "background": "#123456", "nodes": _nodes()}
            for invalid in ({"width": True}, {"nodes": [_nodes()[1] | {"version": True}]},
                            {"nodes": [_nodes()[1] | {"extra": "unexpected"}]},
                            {"nodes": [_nodes()[1] | {"x": "12"}]}):
                failure = await session.call_tool("project_create_composition", arguments | invalid)
                assert failure.isError, invalid
                assert store.context(root)["revision"] == selected["revision"]
            created_response = await session.call_tool("project_create_composition", arguments)
            created = _response(created_response)
            record = created["composition"]
            _image_response(created_response, record["images"]["overview"]["sha256"])
        async with project_session(parameters) as session:
            fetched = await session.call_tool("project_get_composition", {"project_root": root, "composition_id": record["id"]})
            assert _image_response(fetched, record["images"]["overview"]["sha256"]) == created
            image = await session.call_tool("project_get_image", {"project_root": root, "asset_id": "coin", "version": 1})
            assert _image_response(image, registered["image"]["versions"][0]["sha256"])["selected_version"] == 1
    asyncio.run(workflow())
    assert not missing_cli.exists()
