"""从组件和文字样式定位页面，并验证集中重建的发布边界。"""

import json
from pathlib import Path

from PIL import Image
import pytest

from smartui_mcp.projects import ProjectError, ProjectStore
from test_component_usage import usage
from test_compositions import text_node
from test_export_workflow import export_item
from test_page_layouts import exported_project, image_node, label, project_font, save, typography_project, typography_spec
from test_review_workflow import project, record_sample_run


@pytest.mark.parametrize("mode", ["custom", "default", "no-device", "clean", "new"])
def test_rebuild_preserves_page_annotation_settings_outside_unity_assets(tmp_path, mode):
    root = tmp_path / "Unity Project"
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.0f1\n", encoding="utf-8")
    store = ProjectStore()
    current = store.create(str(root), "game", "项目")
    source = tmp_path / "button.png"
    with Image.new("RGBA", (18, 12), "red") as image:
        image.save(source)
    current = store.register_image(str(root), current["revision"], "button", "按钮", str(source),
                                   {"kind": "imported", "description": "合成图像"})
    current = save(store, current, [image_node()])
    device = ({"top": 48, "bottom": 24, "label": "Test device", "extend_background": True,
               "background_node": "button-instance"} if mode == "custom" else
              {"top": 0, "bottom": 0, "label": "No device"} if mode == "no-device" else None)
    before = None
    if mode != "new":
        current = store.build_layout(str(root), current["revision"], "screen",
                                     annotate=mode != "clean", annotation_device=device)
        before = current["page_build"]
    current = save(store, current, [image_node(x=10)])
    result = store.rebuild_pages(str(root), current["revision"], ["screen"])
    assert result["completed_layout_ids"] == ["screen"]
    verified = store.get_page_build(str(root), "screen")
    assert verified["is_current"]
    record = verified["page_build"]
    assert record["nodes"][0]["x"] == 10
    inputs = json.loads(Path(record["files"]["inputs"]["path"]).read_bytes())
    if mode == "clean":
        assert "annotated" not in record["files"]
        assert "annotation_device" not in record and "annotation_device" not in inputs
    else:
        annotated = Path(record["files"]["annotated"]["path"])
        assert annotated.is_relative_to(root / "smart-ui/previews/pages/screen")
        assert annotated.is_file()
        expected = before["annotation_device"] if before is not None else {
            "top": 4, "bottom": 3, "label": "Illustrative device", "extend_background": True}
        assert record["annotation_device"] == inputs["annotation_device"] == expected
    assert not list((root / "Assets").iterdir())


def test_rebuild_preflights_retained_device_background_before_publishing(exported_project):
    store, current = exported_project
    root = current["project_root"]
    current = save(store, current, [image_node()], layout_id="first")
    current = save(store, current, [image_node("device-bg")], layout_id="second")
    current = store.build_layout(root, current["revision"], "second", annotation_device={
        "top": 20, "bottom": 10, "label": "Test device", "background_node": "device-bg"})
    previous = store.get_page_build(root, "second")["page_build"]
    previous_png = Path(previous["files"]["annotated"]["path"]).read_bytes()
    current = save(store, current, [image_node("replacement")], layout_id="second")

    with pytest.raises(ProjectError, match="second.*annotation_device.background_node"):
        store.rebuild_pages(root, current["revision"], ["first", "second"])

    assert store.context(root)["revision"] == current["revision"]
    assert not (Path(root) / "smart-ui/previews/pages/first").exists()
    assert store.get_page_build(root, "second")["page_build"] == previous
    assert Path(previous["files"]["annotated"]["path"]).read_bytes() == previous_png


def test_dependencies_report_export_and_page_staleness_until_the_current_run_is_exported(typography_project, label):
    store, context = typography_project
    root = context["project_root"]
    group = {"id": "panel", "kind": "group", "x": 0, "y": 0, "width": 64, "height": 48,
             "layout": {"direction": "overlay"}, "children": [
                 {key: value for key, value in node.items() if key not in ("x", "y")}
                 for node in [image_node(), label]
             ]}
    current = save(store, context, [group])
    current = save(store, current, [label], layout_id="text-only")
    query = store.page_dependencies(root)
    assert query["components"]["sample"] == {
        "asset_ids": ["button"], "uses": [], "needs_review": False, "latest_run_id": "first"}
    assert query["assets"]["button"]["uses"] == [{"layout_id": "screen", "node_id": "button-instance"}]
    assert query["text_styles"]["button-label"] == [
        {"layout_id": "screen", "node_id": "label"}, {"layout_id": "text-only", "node_id": "label"}]
    assert query["pages"]["screen"] == {"status": "not_built", "stale_reasons": ["not_built"], "source_issues": []}
    built = store.rebuild_pages(root, current["revision"], ["screen", "text-only"])
    assert built["completed_layout_ids"] == ["screen", "text-only"]
    before = store.get_page_build(root, "screen")
    assert before["is_current"] and before["matches_sources"]

    style = context["data"]["style"]
    current = store.update_style(root, built["revision"], style["description"], style["rules"],
                                 {"outline": 3}, style["reference_ids"])
    query = store.page_dependencies(root)
    assert query["pages"]["screen"]["stale_reasons"] == ["sources_stale"]
    assert query["pages"]["text-only"]["status"] == "current"
    assert query["assets"]["button"]["source_issue"]["reason"] == "component_inputs_changed"
    stale = store.get_page_build(root, "screen")
    assert stale["matches_layout"] and stale["matches_bindings"]
    assert not stale["matches_sources"] and not stale["is_current"]
    assert stale["page_build"] == before["page_build"]
    with pytest.raises(ProjectError, match="screen.*button.*sample.*重新绘制"):
        store.build_layout(root, current["revision"], "screen")
    with pytest.raises(ProjectError, match="screen.*button.*sample.*重新绘制"):
        store.rebuild_pages(root, current["revision"], ["text-only", "screen"])
    assert store.context(root)["revision"] == current["revision"]

    redrawn = record_sample_run(store, current, "second", (20, 150, 20, 255))
    issue = store.page_dependencies(root)["assets"]["button"]["source_issue"]
    assert issue["reason"] == "export_run_changed"
    assert issue["export_run_id"] == "first" and issue["latest_run_id"] == "second"
    exported = store.export_assets(root, redrawn["revision"], "sample", [export_item()])
    query = store.page_dependencies(root)
    assert query["assets"]["button"]["source_issue"] is None
    assert query["pages"]["screen"]["stale_reasons"] == ["image_bindings_changed"]
    store.rebuild_pages(root, exported["revision"], ["screen"])
    assert store.page_dependencies(root)["pages"]["screen"]["status"] == "current"


def test_rebuild_preflights_every_page_before_publishing_any(typography_project, label, project_font):
    store, context = typography_project
    root = context["project_root"]
    current = save(store, context, [image_node()])
    current = save(store, current, [label], layout_id="text-only")
    with pytest.raises(ProjectError, match="不存在.*missing"):
        store.rebuild_pages(root, current["revision"], ["screen", "missing"])
    with pytest.raises(ProjectError, match="不能包含重复"):
        store.rebuild_pages(root, current["revision"], ["screen", "screen"])
    project_font.unlink()
    with pytest.raises(ProjectError, match="text-only.*输入未通过预检"):
        store.rebuild_pages(root, current["revision"], ["screen", "text-only"])
    after = store.context(root)
    assert after["revision"] == current["revision"]
    assert after["data"].get("page_builds", {}) == {}
    assert not (Path(root) / "smart-ui/previews/pages").exists()


def test_group_usage_change_is_a_layout_dependency_without_requiring_component_art(typography_project, label, usage):
    store, context = typography_project
    root = context["project_root"]
    spec = context["data"]["components"]["sample"]["spec"]
    with pytest.raises(ProjectError, match="组件 usage-only.*spec.usage"):
        store.define_component(root, context["revision"], "usage-only", "布局规格", {**spec, "usage": {}})
    current = store.define_component(root, context["revision"], "usage-only", "布局规格", {**spec, "usage": usage})
    caption = {**label, "x": 0, "y": 0, "width": 40}
    group = {"id": "panel", "kind": "group", "region": "safe", "component_id": "usage-only",
             "layout": {"direction": "absolute"}, "children": [caption]}
    safe_area = dict.fromkeys(("top", "right", "bottom", "left"), 2)
    current = save(store, current, [group], safe_area=safe_area)
    built = store.build_layout(root, current["revision"], "screen")
    assert built["is_current"] and built["matches_component_usage"]
    assert built["page_build"]["safe_area"] == safe_area
    query = store.page_dependencies(root)
    assert query["components"]["usage-only"]["uses"] == [{"layout_id": "screen", "node_id": "panel"}]

    changed = {**usage, "pivot": {"x": 0.5, "y": 0.5}}
    current = store.define_component(root, built["revision"], "usage-only", "布局规格", {**spec, "usage": changed})
    stale = store.get_page_build(root, "screen")
    assert stale["matches_sources"] and not stale["matches_component_usage"] and not stale["is_current"]
    assert store.page_dependencies(root)["pages"]["screen"]["stale_reasons"] == ["component_usage_changed"]
    rebuilt = store.build_layout(root, current["revision"], "screen")
    assert rebuilt["is_current"]
    assert rebuilt["page_build"]["component_usage"]["panel"]["pivot"] == {"x": 32, "y": 24}


def test_partial_reexport_leaves_only_previous_run_states_stale(project, usage):
    store, context = project
    root = context["project_root"]
    component = context["data"]["components"]["sample"]
    usage["required_states"] = ["normal", "pressed"]
    configured = store.define_component(root, context["revision"], "sample", component["name"],
                                        {**component["spec"], "usage": usage})
    drawn = record_sample_run(store, configured, "with-usage", (20, 150, 20, 255))
    normal = export_item()
    pressed = {**normal, "asset_id": "button-pressed", "output_path": "smart-ui/exports/ui/button-pressed.png",
               "state": "pressed"}
    exported = store.export_assets(root, drawn["revision"], "sample", [normal, pressed])
    group = {"id": "panel", "kind": "group", "region": "canvas", "component_id": "sample",
             "layout": {"direction": "absolute"}, "children": [image_node()]}
    saved = save(store, exported, [group])
    first = store.build_layout(root, saved["revision"], "screen")

    def states(result):
        return result["page_build"]["component_usage"]["panel"]["states"]

    assert states(first) == {"required": ["normal", "pressed"], "missing": [], "stale": [],
                             "available": ["normal", "pressed"]}
    redrawn = record_sample_run(store, store.context(root), "updated", (20, 150, 20, 255))
    partial = store.export_assets(root, redrawn["revision"], "sample", [normal])
    previous = store.get_page_build(root, "screen")
    assert previous["matches_sources"] and previous["matches_images"] and not previous["matches_component_usage"]
    assert store.page_dependencies(root)["pages"]["screen"]["stale_reasons"] == ["component_usage_changed"]
    rebuilt = store.build_layout(root, partial["revision"], "screen")
    assert states(rebuilt) == {"required": ["normal", "pressed"], "missing": [], "stale": ["pressed"],
                               "available": ["normal"]}
    completed = store.export_assets(root, rebuilt["revision"], "sample", [pressed])
    final = store.build_layout(root, completed["revision"], "screen")
    assert final["is_current"] and states(final) == states(first)


def test_rebuild_preserves_completed_pages_and_reports_the_revision_on_render_failure(typography_project, label):
    store, context = typography_project
    root = context["project_root"]
    current = save(store, context, [image_node()], layout_id="first")
    current = save(store, current, [{**label, "text": "A A A A A A A A", "width": 10, "wrap": False}],
                   layout_id="overflow")
    current = save(store, current, [image_node()], layout_id="last")
    with pytest.raises(ProjectError, match=r"overflow.*已完成 layout_ids：\['first'\].*超出文本框") as failure:
        store.rebuild_pages(root, current["revision"], ["first", "overflow", "last"])
    after = store.context(root)
    assert after["revision"] != current["revision"]
    assert after["revision"] in str(failure.value)
    assert isinstance(failure.value.__cause__, ValueError)
    assert set(after["data"]["page_builds"]) == {"first"}
    assert store.get_page_build(root, "first")["is_current"]
