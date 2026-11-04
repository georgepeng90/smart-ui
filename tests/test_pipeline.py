"""验证项目绘制程序和任意节点局部更新的预检边界。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from smartui_mcp.pipeline import draw_component, plan_component
from smartui_mcp.programs import run_program
from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256


PROGRAM = '''
def build(parameters, spec, component_id):
    return {
        "width": spec["width"], "height": spec["height"],
        "nodes": [
            {"key": "assembly", "name": "组件分组", "kind": "grouplayer", "parent": "root",
             "properties": {"opacity": spec.get("group_opacity", 255)}},
            {"key": "ink", "name": "主要图层", "kind": "paintlayer", "parent": "assembly"},
            {"key": "detail", "name": "细节图层", "kind": "paintlayer", "parent": "assembly",
             "properties": {"visible": spec.get("detail_visible", True)}},
            {"key": "clip", "name": "透明度蒙版", "kind": "transparencymask", "parent": "ink",
             "rects": spec.get("clip_rects", [[0, 0, 80, 40, 255]])},
        ],
        "paint": [
            {"node": "ink", "brush": parameters["brush"], "color": parameters["color"],
             "size": spec.get("ink_size", 3),
             "strokes": [{"kind": "line", "from": [0, 0], "to": [20, 10]}]},
            {"node": "detail", "brush": parameters["brush"], "color": spec.get("detail_color", "#abcdef"),
             "size": 2, "strokes": [{"kind": "ellipse", "rect": [8, 8, 12, 10]}]},
        ],
    }
'''


class UnusedBridge:
    def __init__(self):
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        raise AssertionError("参数和文件校验必须发生在调用 Krita 之前")


class MissingBrushBridge:
    def __init__(self):
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "status":
            return {"documents": []}
        if method == "brushes":
            return []
        raise AssertionError("缺少指定笔刷时不得执行绘制")


@pytest.fixture
def project(tmp_path, request):
    store = ProjectStore()
    context = store.create(str(tmp_path / "project"), "demo", "通用绘制项目", paths=getattr(request, "param", None))
    context = store.save_program(context["project_root"], context["revision"], "drawing",
                                 "组件绘制程序", "由项目定义图层与笔触", PROGRAM)
    context = store.update_style(context["project_root"], context["revision"], "项目参数", [],
                                 {"brush": "project brush", "color": "#123456"}, [])
    context = store.define_component(context["project_root"], context["revision"], "sample", "示例组件",
                                     {"program_id": "drawing", "width": 96, "height": 64})
    return store, context


def record_source(store, context):
    root = Path(context["project_root"])
    document = root / context["data"]["paths"]["sources"] / "sample.kra"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_bytes(b"recorded source artifact")
    spec = context["data"]["components"]["sample"]["spec"]
    program = store.get_program(str(root), spec["program_id"])
    plan = run_program(program["source"], context["data"]["style"]["parameters"], spec, "sample")
    snapshot = root / "style-snapshot.json"
    snapshot.write_text(json.dumps({"spec": spec}), encoding="utf-8")
    preview = root / "preview.png"
    preview.write_bytes(b"recorded preview")
    operations = root / "operations.json"
    operations.write_text("[]", encoding="utf-8")
    source_path = root / "program-snapshot.py"
    source_path.write_bytes(program["source"].encode("utf-8"))
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return store.record_run(str(root), context["revision"], "sample", {
        "run_id": "previous", "status": "drawn", "style_version": context["data"]["style"]["version"],
        "spec_sha256": spec_sha256(spec), "document": str(document), "preview": str(preview),
        "operations_path": str(operations), "style_snapshot_path": str(snapshot),
        "program_id": program["id"], "program_version": program["version"], "program_sha256": program["sha256"],
        "program_snapshot_path": str(source_path), "plan_path": str(plan_path),
    })


def change_spec(store, context, changes):
    component = context["data"]["components"]["sample"]
    return store.define_component(context["project_root"], context["revision"], "sample", component["name"],
                                  component["spec"] | changes)


def assert_preflight_rejected(store, context, reason, layers=None):
    bridge = UnusedBridge()
    with pytest.raises(ValueError, match=reason):
        draw_component(context["project_root"], context["revision"], "sample", layers, client=bridge, store=store)
    assert bridge.calls == []
    assert store.context(context["project_root"]) == context


def test_plan_component_executes_project_program_without_creating_assets(project):
    store, context = project
    result = plan_component(context["project_root"], "sample", store=store)
    program = context["data"]["programs"]["drawing"]
    assert result["revision"] == context["revision"]
    assert result["program_id"] == "drawing"
    assert result["program_version"] == program["version"]
    assert result["program_sha256"] == program["sha256"]
    assert [node["key"] for node in result["plan"]["nodes"]] == ["assembly", "ink", "detail", "clip"]
    assert not (Path(context["project_root"]) / context["data"]["paths"]["sources"] / "sample.kra").exists()
    assert store.context(context["project_root"]) == context


@pytest.mark.parametrize("source,reason", [
    ("def build(a, b, c):\n    raise ValueError('项目参数矛盾')", "项目参数矛盾"),
    ("def build(a, b, c):\n    return {'width': 0, 'height': 1, 'nodes': [], 'paint': []}", "width"),
])
def test_bad_program_or_plan_refuses_before_any_krita_call(project, source, reason):
    store, context = project
    context = store.save_program(context["project_root"], context["revision"], "drawing", "待检程序", "", source)
    assert_preflight_rejected(store, context, reason)
    assert not (Path(context["project_root"]) / context["data"]["paths"]["sources"] / "sample.kra").exists()


def test_missing_execution_parameters_refuses_before_any_krita_call(project):
    store, context = project
    context = store.update_style(context["project_root"], context["revision"], "缺少项目所需参数", [], {}, [])
    assert_preflight_rejected(store, context, "brush")
    assert not (Path(context["project_root"]) / context["data"]["paths"]["sources"] / "sample.kra").exists()


def test_tampered_project_program_refuses_before_any_krita_call(project):
    store, context = project
    program = context["data"]["programs"]["drawing"]
    source_path = Path(context["project_root"]) / program["source_path"]
    source_path.write_text(PROGRAM + "\n# 未经登记的修改\n", encoding="utf-8")
    bridge = UnusedBridge()
    with pytest.raises(ProjectError, match="程序源码已被手动修改"):
        draw_component(context["project_root"], context["revision"], "sample", client=bridge, store=store)
    assert bridge.calls == []
    assert not (Path(context["project_root"]) / context["data"]["paths"]["sources"] / "sample.kra").exists()


def test_missing_installed_brush_records_failed_attempt_without_drawing(project):
    store, context = project
    bridge = MissingBrushBridge()
    with pytest.raises(RuntimeError, match="制作失败，已写入组件记录：Krita 未安装指定笔刷"):
        draw_component(context["project_root"], context["revision"], "sample", client=bridge, store=store)
    assert bridge.calls == [("status", {}), ("brushes", {"query": "project brush"})]
    updated = store.context(context["project_root"])
    component = updated["data"]["components"]["sample"]
    assert updated["revision"] != context["revision"]
    assert component["latest_run"] is None
    assert component["needs_review"]
    assert len(component["runs"]) == 1
    failed = component["runs"][-1]
    assert failed["status"] == "failed"
    assert failed["error"] == "Krita 未安装指定笔刷：project brush"
    assert not Path(failed["document"]).exists()
    operations_path = Path(failed["operations_path"])
    calls = json.loads(operations_path.read_text(encoding="utf-8"))
    assert [call["method"] for call in calls] == ["status", "brushes"]
    result = json.loads((operations_path.parent / "result.json").read_text(encoding="utf-8"))
    assert result["error"] == failed["error"]
    assert Path(failed["style_snapshot_path"]).is_file()
    assert Path(failed["program_snapshot_path"]).read_bytes() == PROGRAM.encode("utf-8")
    plan = json.loads(Path(failed["plan_path"]).read_text(encoding="utf-8"))
    assert plan["width"] == 96
    assert failed["program_sha256"] == context["data"]["programs"]["drawing"]["sha256"]
    document = (Path(context["project_root"]) / context["data"]["paths"]["documents"] / "components.md").read_text(encoding="utf-8")
    assert "最近尝试失败，需要重新检查" in document
    assert failed["error"] in document


@pytest.mark.parametrize("project", [{"sources": "design/editable"}], indirect=True)
def test_drawing_uses_configured_source_directory(project):
    store, context = project
    with pytest.raises(RuntimeError, match="Krita 未安装指定笔刷"):
        draw_component(context["project_root"], context["revision"], "sample", client=MissingBrushBridge(), store=store)
    root = Path(context["project_root"])
    failed = store.context(str(root))["data"]["components"]["sample"]["runs"][-1]
    assert Path(failed["document"]) == root / "design" / "editable" / "sample.kra"
    assert (root / "design" / "editable").is_dir()
    assert not (root / "art/ui/sources").exists()


def test_partial_drawing_resolves_recorded_files_after_project_directory_move(project):
    store, context = project
    context = record_source(store, context)
    original_root = Path(context["project_root"])
    moved_root = original_root.with_name("moved-project")
    original_root.rename(moved_root)
    moved = store.context(str(moved_root))
    bridge = MissingBrushBridge()
    with pytest.raises(RuntimeError, match="Krita 未安装指定笔刷"):
        draw_component(str(moved_root), moved["revision"], "sample", ["ink"], client=bridge, store=store)
    assert bridge.calls == [("status", {}), ("brushes", {"query": "project brush"})]
    latest = moved["data"]["components"]["sample"]["latest_run"]
    assert Path(latest["document"]).is_relative_to(moved_root)
    assert Path(latest["plan_path"]).is_relative_to(moved_root)


@pytest.mark.parametrize("layers", [None, ["ink"]])
def test_changed_source_file_refuses_before_any_krita_call(project, layers):
    store, context = project
    context = record_source(store, context)
    document = Path(context["data"]["components"]["sample"]["latest_run"]["document"])
    document.write_bytes(b"user changed source file")
    assert_preflight_rejected(store, context, "源文件已在工具外修改", layers)
    assert document.read_bytes() == b"user changed source file"


@pytest.mark.parametrize("layers", [[], ["missing"], ["ink", "ink"], "ink", [None]])
def test_partial_edit_rejects_empty_duplicate_unknown_or_invalid_keys(project, layers):
    store, context = project
    assert_preflight_rejected(store, context, "layers 必须", layers)


def test_partial_edit_requires_a_successful_source_record(project):
    store, context = project
    assert_preflight_rejected(store, context, "尚无源文件", ["ink"])


def test_partial_edit_requires_historical_plan(project, monkeypatch):
    store, context = project
    context = record_source(store, context)
    historical = deepcopy(context)
    del historical["data"]["components"]["sample"]["latest_run"]["plan_path"]
    monkeypatch.setattr(store, "context", lambda project_root: deepcopy(historical))
    assert_preflight_rejected(store, historical, "历史产物没有绘制计划", ["ink"])


@pytest.mark.parametrize("changes", [{"detail_color": "#111111"}, {"detail_visible": False},
                                     {"clip_rects": [[0, 0, 30, 20, 255]]}])
def test_partial_edit_rejects_changes_to_unselected_paint_properties_or_mask(project, changes):
    store, context = project
    context = record_source(store, context)
    context = change_spec(store, context, changes)
    assert_preflight_rejected(store, context, "改变了未选择的节点", ["ink"])


@pytest.mark.parametrize("selected,changes", [
    (["ink"], {"ink_size": 5}),
    (["detail"], {"detail_color": "#111111"}),
    (["assembly"], {"group_opacity": 128}),
    (["clip"], {"clip_rects": [[0, 0, 30, 20, 255]]}),
    (["ink", "clip"], {"ink_size": 5, "clip_rects": []}),
])
def test_partial_edit_accepts_any_selected_node_keys(project, selected, changes):
    store, context = project
    context = record_source(store, context)
    context = change_spec(store, context, changes)
    bridge = MissingBrushBridge()
    with pytest.raises(RuntimeError, match="Krita 未安装指定笔刷"):
        draw_component(context["project_root"], context["revision"], "sample", selected, client=bridge, store=store)
    assert bridge.calls == [("status", {}), ("brushes", {"query": "project brush"})]


def test_style_and_program_version_changes_allow_local_update_when_plan_scope_matches(project):
    store, context = project
    context = record_source(store, context)
    latest = context["data"]["components"]["sample"]["latest_run"]
    parameters = context["data"]["style"]["parameters"] | {"color": "#445566"}
    context = store.update_style(context["project_root"], context["revision"], "只修改主要图层颜色", [], parameters, [])
    context = store.save_program(context["project_root"], context["revision"], "drawing", "新版程序", "",
                                 PROGRAM + "\n# 程序版本改变，绘制行为仍由项目参数决定。\n")
    assert context["data"]["style"]["version"] > latest["style_version"]
    assert context["data"]["programs"]["drawing"]["version"] > latest["program_version"]
    bridge = MissingBrushBridge()
    with pytest.raises(RuntimeError, match="Krita 未安装指定笔刷"):
        draw_component(context["project_root"], context["revision"], "sample", ["ink"], client=bridge, store=store)
    assert bridge.calls == [("status", {}), ("brushes", {"query": "project brush"})]


def test_new_program_version_cannot_change_unselected_nodes(project):
    store, context = project
    context = record_source(store, context)
    source = PROGRAM.replace('spec.get("detail_color", "#abcdef")', '"#000000"')
    context = store.save_program(context["project_root"], context["revision"], "drawing", "改变细节的程序", "", source)
    assert_preflight_rejected(store, context, "改变了未选择的节点", ["ink"])


def test_partial_edit_rejects_changed_canvas(project):
    store, context = project
    context = record_source(store, context)
    context = change_spec(store, context, {"width": 120})
    assert_preflight_rejected(store, context, "不能改变画布或图层结构", ["ink"])


def test_partial_edit_rejects_changed_node_structure_even_if_node_selected(project):
    store, context = project
    context = record_source(store, context)
    source = PROGRAM.replace('"parent": "assembly"', '"parent": "root"')
    context = store.save_program(context["project_root"], context["revision"], "drawing", "改变图层归属", "", source)
    assert_preflight_rejected(store, context, "不能改变画布或图层结构", ["ink", "detail"])


def test_partial_edit_rejects_tampered_historical_plan(project):
    store, context = project
    context = record_source(store, context)
    plan_path = Path(context["data"]["components"]["sample"]["latest_run"]["plan_path"])
    plan_path.write_text("{}", encoding="utf-8")
    assert_preflight_rejected(store, context, "历史绘制计划已被修改", ["ink"])
