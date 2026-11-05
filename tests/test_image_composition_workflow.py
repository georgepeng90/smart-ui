"""验证当前图像资产、组件组合和跨进程 MCP 调用。"""

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


def _nodes(x=12):
    return [{"id": "button", "kind": "component", "component_id": "sample",
             "x": 1, "y": 1, "width": 8, "height": 6},
            {"id": "coin", "kind": "image", "asset_id": "coin",
             "x": x, "y": 1, "width": 4, "height": 4}]


def _compose(store, context, nodes=None):
    return store.create_composition(context["project_root"], context["revision"], "sample-composition", 64, 48, "#123456",
                                    _nodes() if nodes is None else nodes)


def _state(root):
    return (Path(root) / "smart-ui/project.json").read_bytes()


def test_revision_conflict_does_not_change_current_asset_or_publish_composition(project, tmp_path):
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
    assert latest["image"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize("missing", ["preview", "preview_hash"])
def test_component_missing_preview_evidence_reports_the_run_and_field(project, missing):
    store, context = project
    root = Path(context["project_root"])
    path = root / "smart-ui/components/sample.json"
    component = json.loads(path.read_text(encoding="utf-8"))
    run = component["latest_run"]
    if missing == "preview":
        run.pop("preview")
    else:
        run["artifact_hashes"].pop("preview")
    path.write_text(json.dumps(component, ensure_ascii=False), encoding="utf-8")
    current = store.context(str(root))
    with pytest.raises(ProjectError, match="first.*preview"):
        _compose(store, current, [_nodes()[0]])
    assert not list((root / "smart-ui/previews/compositions").glob("*"))


def test_image_and_composition_updates_keep_current_paths_and_detect_staleness(project, tmp_path):
    store, context = project
    root = context["project_root"]
    run_before = deepcopy(context["data"]["components"]["sample"]["latest_run"])
    registered = _register(store, context, _source(tmp_path))
    first = _compose(store, registered)
    original = first["composition"]
    assert original["is_current"]
    assert original["nodes"][0]["run"]["run_id"] == "first"
    with Image.open(original["images"]["overview"]["path"]) as image:
        assert image.getpixel((1, 1)) == (150, 20, 20, 255)
        assert image.getpixel((12, 1)) == (20, 160, 40, 255)

    replaced = _register(store, first, _source(tmp_path, "edited", (40, 60, 220, 255)), EDITED)
    assert replaced["image"]["path"] == registered["image"]["path"]
    assert replaced["image"]["sha256"] != registered["image"]["sha256"]
    stale = store.get_composition(root, original["id"])["composition"]
    assert not stale["is_current"]
    assert stale["nodes"][0]["is_current"]
    assert not stale["nodes"][1]["is_current"]

    updated = _compose(store, replaced)
    images_before_layout = deepcopy(store.context(root)["data"]["images"])
    moved = _compose(store, updated, _nodes(x=20))
    assert moved["composition"]["id"] == original["id"]
    assert moved["composition"]["images"]["overview"]["path"] == original["images"]["overview"]["path"]
    assert moved["composition"]["is_current"]
    assert moved["composition"]["images"]["overview"]["sha256"] != updated["composition"]["images"]["overview"]["sha256"]
    assert store.context(root)["data"]["images"] == images_before_layout
    assert store.context(root)["data"]["components"]["sample"]["latest_run"] == run_before
    assert moved["composition"]["nodes"][1]["sha256"] == updated["composition"]["nodes"][1]["sha256"]
    assert store.context(root)["data"]["style"]["user_confirmation"] is None


def test_current_composition_detects_corrupt_font_snapshot(project, tmp_path, text_node):
    store, context = project
    root = context["project_root"]
    registered = _register(store, context, _source(tmp_path))
    font_source = Path(root) / "smart-ui/sources/fixture.ttf"
    shutil.copyfile(text_node["font_path"], font_source)
    label = text_node | {"font_path": str(font_source), "x": 2, "y": 16}
    result = _compose(store, registered, _nodes() + [label])
    record = result["composition"]
    assert store.get_composition(root, record["id"])["composition"] == record
    font = next(iter(record["fonts"].values()))
    Path(font["path"]).write_bytes(b"changed font snapshot")
    with pytest.raises(ProjectError, match="组合快照 SHA-256"):
        store.get_composition(root, record["id"])


def test_project_move_keeps_relative_image_composition_and_font_paths(project, tmp_path, text_node):
    store, context = project
    root = Path(context["project_root"])
    font = root / "smart-ui/sources/fixture.ttf"
    font.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(text_node["font_path"], font)
    registered = _register(store, context, _source(tmp_path), {"kind": "imported", "description": "现有图像"})
    result = _compose(store, registered, _nodes() + [text_node | {"font_path": str(font), "x": 2, "y": 16}])
    record = result["composition"]
    payload = _state(root)
    stored = json.loads(payload)
    asset_path = root / stored["images"]["coin"]
    asset = json.loads(asset_path.read_text(encoding="utf-8"))
    assert not Path(asset["path"]).is_absolute()
    for info in [*stored["compositions"][record["id"]]["images"].values(),
                 *stored["compositions"][record["id"]]["fonts"].values()]:
        assert not Path(info["path"]).is_absolute()
    moved_root = tmp_path / "moved-project"
    shutil.move(str(root), str(moved_root))
    reopened = ProjectStore()
    assert _state(moved_root) == payload
    image = reopened.get_image(str(moved_root), "coin")["image"]
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


def test_stdio_registration_composition_and_strict_parameters(project, tmp_path):
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
            arguments = {"project_root": root, "expected_revision": registered["revision"], "width": 64,
                         "height": 48, "background": "#123456", "nodes": _nodes(), "composition_id": "sample-composition"}
            for invalid in ({"width": True},
                            {"nodes": [_nodes()[1] | {"extra": "unexpected"}]},
                            {"nodes": [_nodes()[1] | {"x": "12"}]}):
                failure = await session.call_tool("project_create_composition", arguments | invalid)
                assert failure.isError, invalid
                assert store.context(root)["revision"] == registered["revision"]
            created_response = await session.call_tool("project_create_composition", arguments)
            created = _response(created_response)
            record = created["composition"]
            _image_response(created_response, record["images"]["overview"]["sha256"])
        async with project_session(parameters) as session:
            fetched = await session.call_tool("project_get_composition", {"project_root": root, "composition_id": record["id"]})
            assert _image_response(fetched, record["images"]["overview"]["sha256"]) == created
            image = await session.call_tool("project_get_image", {"project_root": root, "asset_id": "coin"})
            assert _image_response(image, registered["image"]["sha256"])["image"] == registered["image"]
    asyncio.run(workflow())
    assert not missing_cli.exists()
