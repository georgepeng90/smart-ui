"""验证原生制作、局部修改范围和失败时保留已发布源文件。"""

import json
import shutil
from pathlib import Path

import pytest

from smartui_mcp.client import PhotoCraftCapabilityError, PhotoCraftClient
from smartui_mcp.pipeline import draw_component, plan_component
from smartui_mcp.projects import ProjectError, ProjectStore


PROGRAM = '''
def build(parameters, spec, component_id):
    return {"schema_version": 1, "width": spec["width"], "height": spec["height"], "nodes": [
        {"key": "assembly", "name": "组件分组", "kind": "group", "parent": "root",
         "properties": {"opacity": spec.get("group_opacity", 1)}},
        {"key": "ink", "name": "主体", "kind": "shape", "parent": "assembly", "shape": {
            "kind": "roundedRect", "rect": [5, 5, 80, 50], "radii": spec.get("radius", 5),
            "fill": parameters["color"]}},
        {"key": "detail", "name": "细节", "kind": "shape", "parent": "assembly",
         "properties": {"visible": spec.get("detail_visible", True)}, "shape": {
            "kind": "ellipse", "rect": [12, 12, 10, 10], "fill": spec.get("detail_color", "#abcdef")}}
    ]}
'''


class UnusedClient:
    def session(self, root, trace):
        raise AssertionError("参数和文件校验必须发生在启动 PhotoCraft 之前")


class MissingClient:
    def session(self, root, trace):
        raise FileNotFoundError("未安装 PhotoCraft CLI")


@pytest.fixture
def project(tmp_path, request):
    store = ProjectStore()
    context = store.create(str(tmp_path / "project"), "demo", "通用绘制项目")
    context = store.save_program(context["project_root"], context["revision"], "drawing", "组件程序", "", PROGRAM)
    context = store.update_style(context["project_root"], context["revision"], "项目参数", [], {"color": "#123456"}, [])
    context = store.define_component(context["project_root"], context["revision"], "sample", "示例组件",
                                     {"program_id": "drawing", "width": 96, "height": 64})
    return store, context


def record_source(store, context):
    client = PhotoCraftClient()
    try:
        client.status()
    except FileNotFoundError:
        pytest.skip("原生制作回归需要安装 PhotoCraft CLI")
    result = draw_component(context["project_root"], context["revision"], "sample", client=client, store=store)
    assert all(result["verification"].values())
    return store.context(context["project_root"])


def change_spec(store, context, changes):
    component = context["data"]["components"]["sample"]
    return store.define_component(context["project_root"], context["revision"], "sample", component["name"],
                                  component["spec"] | changes)


def assert_preflight_rejected(store, context, reason, layers=None):
    with pytest.raises((ValueError, ProjectError), match=reason):
        draw_component(context["project_root"], context["revision"], "sample", layers, client=UnusedClient(), store=store)
    assert store.context(context["project_root"]) == context


def test_plan_has_no_backend_or_asset_side_effects(project):
    store, context = project
    result = plan_component(context["project_root"], "sample", store=store)
    assert result["revision"] == context["revision"]
    assert result["program_sha256"] == context["data"]["programs"]["drawing"]["sha256"]
    assert [node["key"] for node in result["plan"]["nodes"]] == ["assembly", "ink", "detail"]
    assert not list(Path(context["project_root"]).rglob("*.pcraft"))
    assert store.context(context["project_root"]) == context


@pytest.mark.parametrize("source,reason", [
    ("def build(a, b, c):\n    raise ValueError('项目参数矛盾')", "项目参数矛盾"),
    ("def build(a, b, c):\n    return {'schema_version': 1, 'width': 0, 'height': 1, 'nodes': []}", "width"),
])
def test_invalid_program_refuses_before_backend(project, source, reason):
    store, context = project
    context = store.save_program(context["project_root"], context["revision"], "drawing", "待检程序", "", source)
    assert_preflight_rejected(store, context, reason)


def test_missing_required_project_parameter_refuses_before_backend(project):
    store, context = project
    context = store.update_style(context["project_root"], context["revision"], "缺少颜色", [], {}, [])
    assert_preflight_rejected(store, context, "color")


def test_tampered_program_refuses_before_backend(project):
    store, context = project
    path = Path(context["project_root"]) / context["data"]["programs"]["drawing"]["source_path"]
    path.write_text(PROGRAM + "\n# 未登记修改", encoding="utf-8")
    with pytest.raises(ProjectError, match="程序源码已被手动修改"):
        draw_component(context["project_root"], context["revision"], "sample", client=UnusedClient(), store=store)


def test_missing_backend_records_failure_with_reproducible_inputs(project):
    store, context = project
    with pytest.raises(RuntimeError, match="制作失败，已保留本机诊断.*未安装"):
        draw_component(context["project_root"], context["revision"], "sample", client=MissingClient(), store=store)
    updated = store.context(context["project_root"])
    component = updated["data"]["components"]["sample"]
    assert updated["revision"] == context["revision"]
    assert component["latest_run"] is None
    assert component["needs_review"]
    diagnostic = Path(context["project_root"]) / ".smartui/diagnostics/sample"
    assert (diagnostic / "program.py").read_bytes() == PROGRAM.encode("utf-8")
    assert json.loads((diagnostic / "plan.json").read_text(encoding="utf-8"))["width"] == 96
    assert "未安装" in json.loads((diagnostic / "result.json").read_text(encoding="utf-8"))["error"]
    with pytest.raises(RuntimeError):
        draw_component(context["project_root"], context["revision"], "sample", client=MissingClient(), store=store)
    assert len(list(diagnostic.parent.iterdir())) == 1


def test_real_creation_keeps_only_current_artifact(project):
    store, context = project
    updated = record_source(store, context)
    run = updated["data"]["components"]["sample"]["latest_run"]
    root = Path(context["project_root"])
    assert Path(run["document"]) == root / "smart-ui/sources/sample.pcraft"
    assert "source_snapshot_path" not in run
    assert "operations_path" not in run
    assert run["backend"]["name"] == "photocraft"
    assert run["backend"]["build_verification"] in ("verified", "unverified")
    assert run["backend"]["missing_capabilities"] == []
    assert set(run["layer_ids"]) == {"assembly", "ink", "detail"}
    assert not list((root / ".smartui/temp").iterdir())


def test_missing_capability_preserves_actual_build_in_diagnostic(project):
    store, context = project
    backend = {"version": "photocraft-cli 0.20.2", "cli_path": str(Path(context["project_root"]) / "cli.exe"),
               "sha256": "0" * 64, "build_verification": "unverified", "missing_capabilities": ["method:doc.save"]}

    class IncompatibleClient:
        def session(self, root, trace):
            raise PhotoCraftCapabilityError("PhotoCraft 缺少必需能力：method:doc.save", backend=backend)

    with pytest.raises(RuntimeError, match="缺少必需能力：method:doc.save"):
        draw_component(context["project_root"], context["revision"], "sample", client=IncompatibleClient(), store=store)
    root = Path(context["project_root"])
    result = json.loads((root / ".smartui/diagnostics/sample/result.json").read_text(encoding="utf-8"))
    assert result["backend"] == {"name": "photocraft", **backend}
    assert store.context(context["project_root"]) == context


def test_local_update_after_project_move_preserves_ids_and_other_nodes(project):
    store, context = project
    context = record_source(store, context)
    old = context["data"]["components"]["sample"]["latest_run"]
    original = Path(context["project_root"])
    moved = original.with_name("moved-project")
    original.rename(moved)
    shutil.rmtree(moved / ".smartui")
    context = change_spec(store, store.context(str(moved)), {"radius": 10})
    result = draw_component(str(moved), context["revision"], "sample", ["ink"], store=store)
    assert result["verification"]["unchanged_other_nodes"]
    assert result["verification"]["target_content_changed"]
    latest = store.context(str(moved))["data"]["components"]["sample"]["latest_run"]
    assert latest["layer_ids"] == old["layer_ids"]
    assert Path(latest["plan_path"]).is_relative_to(moved)


@pytest.mark.parametrize("layers", [None, ["ink"]])
def test_external_source_change_refuses_before_backend(project, layers):
    store, context = project
    context = record_source(store, context)
    source = Path(context["data"]["components"]["sample"]["latest_run"]["document"])
    source.write_bytes(b"external edit")
    assert_preflight_rejected(store, context, "源文件已在工具外修改", layers)
    assert source.read_bytes() == b"external edit"


@pytest.mark.parametrize("layers", [[], ["missing"], ["ink", "ink"], "ink", [None]])
def test_invalid_local_keys_refuse_before_backend(project, layers):
    assert_preflight_rejected(*project, "layers 必须", layers)


def test_local_edit_requires_successful_source(project):
    assert_preflight_rejected(*project, "尚无源文件", ["ink"])


@pytest.mark.parametrize("changes", [{"detail_color": "#111111"}, {"detail_visible": False}, {"group_opacity": .5}])
def test_local_edit_cannot_change_unselected_nodes(project, changes):
    store, context = project
    context = change_spec(store, record_source(store, context), changes)
    assert_preflight_rejected(store, context, "改变了未选择的节点", ["ink"])


@pytest.mark.parametrize("changes", [{"width": 120}, {"height": 72}])
def test_update_cannot_change_canvas(project, changes):
    store, context = project
    context = change_spec(store, record_source(store, context), changes)
    assert_preflight_rejected(store, context, "不能改变画布或图层结构", ["ink"])


def test_update_cannot_change_structure(project):
    store, context = project
    context = record_source(store, context)
    source = PROGRAM.replace('"parent": "assembly"', '"parent": "root"')
    context = store.save_program(context["project_root"], context["revision"], "drawing", "修改归属", "", source)
    assert_preflight_rejected(store, context, "不能改变画布或图层结构", ["ink", "detail"])


def test_tampered_plan_refuses_before_backend(project):
    store, context = project
    context = record_source(store, context)
    Path(context["data"]["components"]["sample"]["latest_run"]["plan_path"]).write_text("{}", encoding="utf-8")
    assert_preflight_rejected(store, context, "当前产物的绘制计划已被修改", ["ink"])


def test_failed_update_preserves_source_and_latest_success(project):
    store, context = project
    context = record_source(store, context)
    old = context["data"]["components"]["sample"]["latest_run"]
    source = Path(old["document"]).read_bytes()
    context = change_spec(store, context, {"radius": 9})
    with pytest.raises(RuntimeError, match="未安装"):
        draw_component(context["project_root"], context["revision"], "sample", ["ink"], client=MissingClient(), store=store)
    updated = store.context(context["project_root"])["data"]["components"]["sample"]
    assert updated["latest_run"] == old
    assert Path(old["document"]).read_bytes() == source
    assert "runs" not in updated
