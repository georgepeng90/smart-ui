"""多场景预览复用正式布局，且失败批次不会发布。"""

import hashlib
import json
from pathlib import Path
import re
import shutil

from PIL import Image
import pytest

from smartui_mcp.page_variants import preview_layout_variants
from smartui_mcp.projects import ProjectError
from test_export_workflow import export_item
from test_page_layouts import (
    exported_project, image_node, label, project, project_bytes, project_font, save, text_node,
    typography_project, typography_spec,
)
from test_review_workflow import record_sample_run


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _scene(identifier, width=96, height=60, **changes):
    return {"id": identifier, "width": width, "height": height, "texts": {}, **changes}


def test_variants_reflow_real_text_and_safe_region_across_viewports(typography_project, label):
    store, context = typography_project
    caption = {**label, "width": "content", "text": "A", "wrap": False}
    icon = image_node("icon")
    elastic = image_node("elastic", width="fill")
    for child in (caption, icon, elastic):
        del child["x"], child["y"]
    groups = [
        {"id": "background", "kind": "group", "region": "canvas", "layout": {"direction": "overlay"},
         "children": [{"id": "art", "kind": "image", "asset_id": "button", "width": "fill", "height": "fill"}]},
        {"id": "action", "kind": "group", "region": "safe",
         "layout": {"direction": "row", "gap": 3, "align": "center"}, "children": [caption, icon, elastic]},
    ]
    base_safe = {"left": 4, "right": 5, "top": 3, "bottom": 2}
    new_safe = {"left": 11, "right": 7, "top": 6, "bottom": 4}
    saved = save(store, context, groups, width=96, height=60, safe_area=base_safe)
    root = saved["project_root"]
    before = project_bytes(saved)
    result = preview_layout_variants(store, root, saved["revision"], "screen", [
        _scene("short"), _scene("long", 132, 70, texts={"label": "AAA"}, safe_area=new_safe),
    ])
    assert result["project_revision"] == saved["revision"]
    assert result["layout_sha256"] == saved["layout"]["sha256"]
    assert Path(result["index"]).is_relative_to(Path(root) / ".smartui/previews/variants/screen")
    assert "scenes/long/overview.png" in Path(result["index"]).read_text(encoding="utf-8")
    short, long = result["scenes"]
    reports = [_read(scene["report"]) for scene in (short, long)]
    assert reports[0]["definition"]["safe_area"] == base_safe
    assert reports[1]["definition"]["safe_area"] == new_safe
    boxes = [{item["id"]: item["layout_box"] for item in report["renderer"]["nodes"]} for report in reports]
    assert boxes[1]["icon"][0] - boxes[0]["icon"][0] == 24 + new_safe["left"] - base_safe["left"]
    assert boxes[0]["art"] == [0, 0, 96, 60]
    assert boxes[1]["art"] == [0, 0, 132, 70]
    assert boxes[0]["elastic"][0] + boxes[0]["elastic"][2] == 96 - base_safe["right"]
    assert boxes[1]["elastic"][0] + boxes[1]["elastic"][2] == 132 - new_safe["right"]
    assert reports[0]["resources"] == reports[1]["resources"]
    assert len([key for key in reports[0]["resources"] if key.startswith("font-")]) == 1
    for scene, report in zip((short, long), reports, strict=True):
        assert report["project_revision"] == saved["revision"]
        assert report["layout_sha256"] == saved["layout"]["sha256"]
        with Image.open(scene["overview"]) as image:
            assert image.size == (scene["width"], scene["height"])
        assert hashlib.sha256(Path(scene["overview"]).read_bytes()).hexdigest() == report["files"]["overview"]["sha256"]
    assert project_bytes(saved) == before
    assert store.get_layout(root, "screen") == saved
    assert not store.context(root)["data"].get("page_builds", {})


def test_invalid_scene_preflights_before_rendering_and_render_failure_keeps_published_batch(typography_project, label):
    store, context = typography_project
    saved = save(store, context, [{**label, "text": "A", "width": 18, "wrap": False}])
    root, revision = saved["project_root"], saved["revision"]
    old = preview_layout_variants(store, root, revision, "screen", [_scene("published")])
    index_before = Path(old["index"]).read_bytes()
    report_before = Path(old["report"]).read_bytes()

    def failure(second, match, phase):
        with pytest.raises(ProjectError, match=match) as caught:
            preview_layout_variants(store, root, revision, "screen", [_scene("replacement"), second])
        diagnostic = Path(re.search(r"诊断目录：(.+)$", str(caught.value)).group(1))
        report = _read(diagnostic / "failure.json")
        assert report["phase"] == phase
        assert report["project_revision"] == revision
        assert Path(old["index"]).read_bytes() == index_before
        assert Path(old["report"]).read_bytes() == report_before
        assert store.get_layout(root, "screen") == saved
        return diagnostic

    preflight = failure(_scene("Replacement"), r"预检.*Replacement.*重复", "预检")
    assert not (preflight / "current").exists()
    preflight = failure(_scene("unknown-text", texts={"missing": "A"}), r"预检.*unknown-text.*texts.missing", "预检")
    assert not (preflight / "current").exists()
    rendering = failure(_scene("overflows", texts={"label": "AAAA"}), r"渲染.*overflows.*文字节点 label", "渲染")
    assert (rendering / "current/scenes/replacement/overview.png").is_file()


def test_variants_compare_current_exported_states_and_select_locale_styles(typography_project, typography_spec, label):
    store, context = typography_project
    root = context["project_root"]
    run = context["data"]["components"]["sample"]["latest_run"]
    # 合成同一当前绘制产物中的两个状态，再通过正式导出登记。
    with Image.new("RGBA", (8, 6), (170, 20, 20, 255)) as source:
        source.paste((20, 130, 30, 255), (4, 0, 8, 6))
        source.save(run["preview"])
    drawn = store.record_run(root, context["revision"], "sample", {**run, "run_id": "two-states"})
    geometry = {"mode": "contain", "content_rect": {"x": 0, "y": 0, "width": 4, "height": 6}}
    normal = {**export_item(), "region": {"x": 0, "y": 0, "width": 4, "height": 6}, "geometry": geometry}
    pressed = {**normal, "asset_id": "button-pressed", "state": "pressed",
               "output_path": "smart-ui/exports/ui/button-pressed.png", "region": {**normal["region"], "x": 4}}
    exported = store.export_assets(root, drawn["revision"], "sample", [normal, pressed])
    style = typography_spec["styles"]["button-label"]
    locale_font = Path(style["font_path"]).with_name("locale.ttf")
    shutil.copyfile(style["font_path"], locale_font)
    typography_spec["size_scale"]["locale"] = 16
    typography_spec["styles"]["locale-label"] = {
        **style, "name": "本地化文字", "size_id": "locale", "font_path": str(locale_font),
    }
    updated = store.update_typography(root, exported["revision"], typography_spec)
    saved = save(store, updated, [image_node(), {**label, "x": 20, "y": 10, "width": 40, "text": "A"}])

    result = preview_layout_variants(store, root, saved["revision"], "screen", [
        _scene("normal"), _scene("pressed", images={"button-instance": "button-pressed"}),
        _scene("locale", texts={"label": "AA"}, text_styles={"label": "locale-label"}),
    ])
    normal_scene, pressed_scene, _ = result["scenes"]
    assert "scenes/pressed/overview.png" in Path(result["index"]).read_text(encoding="utf-8")
    normal_report, pressed_report, locale_report = [_read(scene["report"]) for scene in result["scenes"]]
    assert normal_report["definition"]["nodes"][0]["asset_id"] == "button"
    assert pressed_report["definition"]["nodes"][0]["asset_id"] == "button-pressed"
    assert normal_report["definition"]["nodes"][1] == pressed_report["definition"]["nodes"][1]
    for scene, expected in ((normal_scene, (170, 20, 20)), (pressed_scene, (20, 130, 30))):
        with Image.open(scene["overview"]) as image:
            assert image.convert("RGB").getpixel((10, 8)) == expected
    assert locale_report["definition"]["nodes"][1]["text_style_id"] == "locale-label"
    assert locale_report["renderer"]["nodes"][1]["font_size"] == 16
    assert ({key for key in normal_report["resources"] if key.startswith("font-")} !=
            {key for key in locale_report["resources"] if key.startswith("font-")})
    assert store.get_layout(root, "screen") == saved


@pytest.mark.parametrize("override,match", [
    ({"images": {"missing-node": "button"}}, r"images.missing-node.*图像节点"),
    ({"images": {"label": "button"}}, r"images.label.*图像节点"),
    ({"images": {"button-instance": "missing-asset"}}, r"button-instance.*不存在的资产.*missing-asset"),
    ({"text_styles": {"button-instance": "button-label"}}, r"text_styles.button-instance.*文字节点"),
    ({"text_styles": {"label": "missing-role"}}, r"label.*不存在的文字样式.*missing-role"),
])
def test_variant_overrides_reject_unknown_or_wrong_type_nodes_and_bindings(typography_project, label, override, match):
    store, context = typography_project
    saved = save(store, context, [image_node(), label])
    root = saved["project_root"]
    with pytest.raises(ProjectError, match=rf"预检.*overrides.*{match}"):
        preview_layout_variants(store, root, saved["revision"], "screen", [_scene("overrides", **override)])
    assert not (Path(root) / ".smartui/previews/variants/screen").exists()


def test_variant_checks_effective_assets_and_rejects_a_stale_overridden_state(exported_project):
    store, context = exported_project
    root = context["project_root"]
    pressed = {**export_item(), "asset_id": "button-pressed", "state": "pressed",
               "output_path": "smart-ui/exports/ui/button-pressed.png"}
    store.export_assets(root, context["revision"], "sample", [pressed])
    newer = record_sample_run(store, store.context(root), "newer", (20, 100, 180, 255))
    updated = store.export_assets(root, newer["revision"], "sample", [export_item()])
    saved = save(store, updated, [image_node()])
    with pytest.raises(ProjectError, match="预检.*pressed.*button-pressed.*重新导出"):
        preview_layout_variants(store, root, saved["revision"], "screen", [
            _scene("normal"), _scene("pressed", images={"button-instance": "button-pressed"}),
        ])
    assert not (Path(root) / ".smartui/previews/variants/screen").exists()

    stale_base = save(store, saved, [image_node(asset_id="button-pressed")], layout_id="stale-base")
    with pytest.raises(ProjectError, match="预检.*inherited.*button-pressed.*重新导出"):
        preview_layout_variants(store, root, stale_base["revision"], "stale-base", [_scene("inherited")])
    result = preview_layout_variants(store, root, stale_base["revision"], "stale-base", [
        _scene("replaced", images={"button-instance": "button"}),
    ])
    assert Path(result["scenes"][0]["overview"]).is_file()
