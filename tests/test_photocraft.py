"""原生文件完整性、图层身份与真实 PhotoCraft 执行检查。"""
from copy import deepcopy
import io
import json
import zipfile

import blake3
from PIL import Image
import pytest
import zstandard

from smartui_mcp.client import PhotoCraftClient
from smartui_mcp.photocraft import NativeDocumentError, apply_plan, read_document
from smartui_mcp.programs import ProgramError


def sample_plan():
    return {"schema_version": 1, "width": 96, "height": 64, "nodes": [
        {"key": "group", "name": "按钮", "kind": "group", "parent": "root"},
        {"key": "base", "name": "底板", "kind": "shape", "parent": "group", "shape": {
            "kind": "roundedRect", "rect": [5, 5, 86, 54], "radii": 8,
            "fill": {"gradient": {"stops": [[0, "#aaffff"], [1, "#1188ff"]]}},
            "stroke": {"color": "#112233", "width": 2}},
         "effects": [{"kind": "dropShadow", "params": {"size": 2, "distance": 2, "opacity": 25}}]},
        {"key": "highlight", "name": "高光", "kind": "pixel", "parent": "group", "strokes": [
            {"color": "#ffffffaa", "size": 3, "opacity": .5, "points": [[12, 12, 1], [70, 12, 1]]}]},
        {"key": "top", "name": "顶层", "kind": "shape", "parent": "root", "shape": {
            "kind": "ellipse", "rect": [75, 40, 10, 10], "fill": "#ff8822"}}]}

@pytest.fixture
def client():
    client = PhotoCraftClient()
    try:
        client.status()
    except FileNotFoundError:
        pytest.skip("真实 PhotoCraft 集成测试需要安装受支持构建")
    return client


def test_real_roundtrip_and_local_edit_preserve_non_targets(tmp_path, client):
    plan = sample_plan()
    with client.session(tmp_path) as session:
        ids = apply_plan(session, plan)
        session.call("doc.save", {"path": "before.pcraft"})
        session.call("doc.render", {"path": "before.png", "maxSide": 0})
    before = read_document(tmp_path / "before.pcraft")
    assert len(before["nodes"]) == 4
    assert before["nodes"][0]["manifest"]["content"]["children"] == [ids["base"], ids["highlight"]]
    assert before["nodes"][-1]["id"] == ids["top"]
    assert before["objects"]
    with client.session(tmp_path) as session:
        session.call("doc.open", {"path": "before.pcraft"})
        session.call("doc.save", {"path": "reopened.pcraft"})
        session.call("doc.render", {"path": "reopened.png", "maxSide": 0})
        plan["nodes"][2]["strokes"][0]["color"] = "#ff3300"
        plan["nodes"][2]["strokes"][0]["points"] = [[14, 15, 1], [60, 15, 1]]
        assert apply_plan(session, plan, layer_ids=ids, selected=["highlight"]) == ids
        session.call("doc.save", {"path": "edited.pcraft"})
    reopened = read_document(tmp_path / "reopened.pcraft")
    edited = read_document(tmp_path / "edited.pcraft")
    assert before["document_state"] == reopened["document_state"] == edited["document_state"]
    assert [n["sha256"] for n in before["nodes"]] == [n["sha256"] for n in reopened["nodes"]]
    assert Image.open(tmp_path / "before.png").tobytes() == Image.open(tmp_path / "reopened.png").tobytes()
    a, b = ({n["id"]: n for n in d["nodes"]} for d in (before, edited))
    assert [i for i in a if a[i]["sha256"] != b[i]["sha256"]] == [ids["highlight"]]


def test_real_vector_mask_replaced_and_removed(tmp_path, client):
    plan = sample_plan()
    plan["nodes"][0]["mask"] = {"path": {"subpaths": [{"knots": [[0, 0], [60, 0], [60, 64], [0, 64]]}]}, "feather": 1}
    with client.session(tmp_path) as session:
        ids = apply_plan(session, plan)
        session.call("doc.save", {"path": "masked.pcraft"})
        plan["nodes"][0].pop("mask")
        apply_plan(session, plan, layer_ids=ids, selected=["group"])
        session.call("doc.save", {"path": "unmasked.pcraft"})
    assert read_document(tmp_path / "masked.pcraft")["nodes"][0]["manifest"]["vector_mask"] is not None
    assert read_document(tmp_path / "unmasked.pcraft")["nodes"][0]["manifest"]["vector_mask"] is None


def native_file(tmp_path, mutate=None, *, missing=False, corrupt=False):
    payload = bytes(256 * 256 * 4)
    digest = blake3.blake3(payload).hexdigest()
    layer = {"id": 2, "name": "像素", "content": {"kind": "raster", "surface": {
        "format": {"mode": "Rgb", "sample": "U8", "alpha": True}, "default": "00000000", "tiles": [{"tx": 0, "ty": 0, "hash": digest}]}}}
    manifest = {"format_version": 1, "document": {"id": 1, "name": "原生", "size": {"width": 32, "height": 32}, "layers": [layer]}}
    if mutate:
        mutate(manifest)
    path = tmp_path / "test.pcraft"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        if not missing:
            archive.writestr(f"tiles/{digest}.zst", zstandard.ZstdCompressor().compress(b"corrupt" if corrupt else payload))
    return path

@pytest.mark.parametrize("kwargs,reason", [({"missing": True}, "引用缺失"), ({"corrupt": True}, "BLAKE3"),
    ({"mutate": lambda m: m["document"]["layers"].append(deepcopy(m["document"]["layers"][0]))}, "ID"),
    ({"mutate": lambda m: m.update(format_version=2)}, "版本"),
    ({"mutate": lambda m: m["document"]["layers"][0]["content"]["surface"].update(default="ff")}, "默认像素")])
def test_native_file_rejects_invalid_structure_and_references(tmp_path, kwargs, reason):
    with pytest.raises(NativeDocumentError, match=reason):
        read_document(native_file(tmp_path, **kwargs))


def test_preflight_rejects_old_plan_before_call():
    class NoSession:
        def call(self, *args):
            pytest.fail("非法计划不得进入 PhotoCraft")
    with pytest.raises(ProgramError):
        apply_plan(NoSession(), {"width": 10, "height": 10, "nodes": [], "paint": []})

def test_real_path_edit_keeps_native_id(tmp_path, client):
    plan = {"schema_version": 1, "width": 40, "height": 30, "nodes": [{"key": "path", "name": "路径", "kind": "shape", "parent": "root", "shape": {"kind": "path", "path": {"subpaths": [{"knots": [[0, 0], [30, 0], [30, 20]]}]}, "fill": "#ffaa00"}}]}
    with client.session(tmp_path) as session:
        ids = apply_plan(session, plan)
        plan["nodes"][0]["shape"]["path"]["subpaths"][0]["knots"][1] = [20, 0]
        assert apply_plan(session, plan, layer_ids=ids, selected=["path"]) == ids
        session.call("doc.save", {"path": "path.pcraft"})
    assert read_document(tmp_path / "path.pcraft")["nodes"][0]["id"] == ids["path"]

def test_real_all_shapes_effects_and_hidden_parent_update(tmp_path, client):
    from smartui_mcp.programs import EFFECT_DEFAULTS
    geometries = [
        {"kind": "rect", "rect": [2, 2, 20, 16]},
        {"kind": "roundedRect", "rect": [2, 2, 20, 16], "radii": [2, 4, 3, 1]},
        {"kind": "ellipse", "rect": [2, 2, 20, 16]},
        {"kind": "polygon", "rect": [2, 2, 20, 16], "sides": 3},
        {"kind": "star", "rect": [2, 2, 20, 16], "sides": 5},
        {"kind": "line", "from": [2, 2], "to": [25, 20]},
        {"kind": "path", "path": {"subpaths": [{"knots": [[2, 2], [22, 2], [22, 16]]}]}},
    ]
    plan = {"schema_version": 1, "width": 40, "height": 30, "nodes": [
        {"key": "group", "name": "隐藏父组", "kind": "group", "parent": "root", "properties": {"visible": False}}]}
    for i, geometry in enumerate(geometries):
        plan["nodes"].append({"key": f"shape{i}", "name": f"形状{i}", "kind": "shape", "parent": "group",
                              "shape": {**geometry, "fill": "#0088ff", "stroke": {"width": 1, "color": "#ffffff"}},
                              "effects": [{"kind": kind, "params": {}} for kind in EFFECT_DEFAULTS] if i == 0 else []})
    with client.session(tmp_path) as session:
        ids = apply_plan(session, plan)
        session.call("doc.save", {"path": "all-before.pcraft"})
        apply_plan(session, plan, layer_ids=ids)
        session.call("doc.save", {"path": "all-same.pcraft"})
        plan["nodes"][1]["effects"] = []
        apply_plan(session, plan, layer_ids=ids, selected=["shape0"])
        session.call("doc.save", {"path": "all-after.pcraft"})
    a, same, b = [read_document(tmp_path / name) for name in ("all-before.pcraft", "all-same.pcraft", "all-after.pcraft")]
    assert [n["sha256"] for n in a["nodes"]] == [n["sha256"] for n in same["nodes"]]
    changed = [x["id"] for x, y in zip(a["nodes"], b["nodes"]) if x["sha256"] != y["sha256"]]
    assert changed == [ids["shape0"]]
    assert b["nodes"][0]["manifest"]["visible"] is False
    assert b["nodes"][1]["manifest"]["effects"]["items"] == []
