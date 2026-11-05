import hashlib
import json
from pathlib import Path

import pytest

from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256, style_sha256


@pytest.fixture
def store():
    return ProjectStore()


@pytest.fixture
def project(store, tmp_path):
    return store.create(str(tmp_path / "project"), "demo.v1", "中文项目")


def define(store, project, spec=None):
    if "button" not in project["data"]["programs"]:
        project = store.save_program(project["project_root"], project["revision"], "button", "按钮程序", "通用绘制程序", "result = []\n")
    return store.define_component(project["project_root"], project["revision"], "primary-button", "主按钮",
                                  {"program_id": "button", **(spec or {"width": 160, "text": "开始"})})


def successful_run(project, tmp_path, run_id="run-1", status="drawn"):
    program = project["data"]["programs"]["button"]
    run = {"run_id": run_id, "status": status,
           "style_sha256": style_sha256(project["data"]["style"]),
           "program_id": program["id"], "program_sha256": program["sha256"],
           "spec_sha256": spec_sha256(project["data"]["components"]["primary-button"]["spec"])}
    for field, filename in (("document", "button.pcraft"), ("preview", "button.png"),
                            ("style_snapshot_path", "style.json")):
        path = tmp_path / filename
        path.write_bytes(f"实际产物 {field}".encode())
        run[field] = str(path)
    plan = tmp_path / "plan.json"
    plan.write_text('{"operations": []}', encoding="utf-8")
    run.update(plan_path=str(plan),
               backend={"name": "photocraft", "version": "synthetic metadata fixture", "sha256": "0" * 64,
                        "cli_path": str(tmp_path / "photocraft-cli")})
    if status == "verified":
        run["verification"] = {"reopened": True, "stable_node_ids": True,
                               "stable_pixels": True, "stable_properties": True}
        run["inspection"] = {"before": {"nodes": [{"id": "button"}]},
                             "reopened": {"nodes": [{"id": "button"}]}}
    return run


def record(store, project, run):
    return store.record_run(project["project_root"], project["revision"], "primary-button", run)


def test_create_writes_readable_documents_and_content_revision(store, project):
    root = Path(project["project_root"])
    payload = (root / "smart-ui/project.json").read_bytes()
    assert project["revision"] == spec_sha256(json.loads(payload))
    assert "中文项目" in payload.decode()
    assert project["data"]["style"]["user_confirmation"] is None
    assert project["data"]["schema_version"] == 5
    assert project["data"]["typography"] is None
    assert project["data"]["programs"] == {}
    for filename in ("style.md", "components.md", "typography.md"):
        assert "中文项目" in (root / "smart-ui/docs" / filename).read_text(encoding="utf-8")
        assert project["data"]["document_hashes"][filename] == hashlib.sha256((root / "smart-ui/docs" / filename).read_bytes()).hexdigest()
    assert store.context(str(root)) == project


@pytest.mark.parametrize("existing", ["smart-ui/project.json", "smart-ui/typography.json", "smart-ui/docs/style.md",
                                      "smart-ui/docs/components.md", "smart-ui/docs/typography.md"])
def test_create_refuses_existing_managed_files(store, tmp_path, existing):
    (tmp_path / existing).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / existing).write_text("用户原稿", encoding="utf-8")
    with pytest.raises(ProjectError, match="不能覆盖"):
        store.create(str(tmp_path), "demo", "项目")
    assert (tmp_path / existing).read_text(encoding="utf-8") == "用户原稿"


def test_stale_revision_cannot_overwrite_new_component(store, project):
    updated = define(store, project)
    with pytest.raises(ProjectError, match="revision 冲突"):
        store.update_style(project["project_root"], project["revision"], "过期更新", [], {"radius": 2}, [])
    assert store.context(project["project_root"]) == updated


@pytest.mark.parametrize("filename", ["style.md", "components.md", "typography.md"])
def test_manual_document_edits_are_detected_before_read_or_write(store, project, filename):
    root = Path(project["project_root"])
    before = (root / "smart-ui/project.json").read_bytes()
    (root / "smart-ui/docs" / filename).write_text("手工更新，必须保留", encoding="utf-8")
    with pytest.raises(ProjectError, match="受管文档已被修改"):
        store.context(str(root))
    with pytest.raises(ProjectError, match="受管文档已被修改"):
        define(store, project)
    assert (root / "smart-ui/project.json").read_bytes() == before
    assert (root / "smart-ui/docs" / filename).read_text(encoding="utf-8") == "手工更新，必须保留"


def test_projects_are_isolated(store, project, tmp_path):
    other = store.create(str(tmp_path / "other"), "other", "另一个项目")
    define(store, project)
    assert store.context(other["project_root"]) == other


def test_style_changes_preserve_current_result_and_mark_component_for_review(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path, status="verified"))
    original = project["data"]["components"]["primary-button"]["latest_run"]
    assert not project["data"]["components"]["primary-button"]["needs_review"]
    project = store.update_style(project["project_root"], project["revision"], "圆角更新", [], {"radius": 8}, [])
    component = project["data"]["components"]["primary-button"]
    assert component["needs_review"]
    assert component["latest_run"] == original
    assert component["latest_run"]["style_sha256"] != style_sha256(project["data"]["style"])
    assert "需要复查：是" in (Path(project["project_root"]) / "smart-ui/docs/components.md").read_text(encoding="utf-8")


def test_spec_change_keeps_run_and_marks_review(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path))
    original = project["data"]["components"]["primary-button"]["latest_run"]
    project = define(store, project, {"width": 240})
    component = project["data"]["components"]["primary-button"]
    assert component["needs_review"]
    assert component["latest_run"] == original


def test_failed_attempt_is_not_a_persistent_component_result(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path))
    with pytest.raises(ProjectError, match="成功产物"):
        record(store, project, {"run_id": "run-failed", "status": "failed", "error": "PhotoCraft 绘制失败"})
    assert store.context(project["project_root"]) == project


@pytest.mark.parametrize("field", ["document", "preview", "style_snapshot_path", "plan_path"])
def test_missing_artifacts_cannot_be_recorded_as_success(store, project, tmp_path, field):
    project = define(store, project)
    run = successful_run(project, tmp_path)
    Path(run[field]).unlink()
    with pytest.raises(ProjectError, match="现存文件的绝对路径"):
        record(store, project, run)
    assert store.context(project["project_root"]) == project


def test_verified_requires_checks_and_actual_inspection(store, project, tmp_path):
    project = define(store, project)
    run = successful_run(project, tmp_path, status="verified")
    run["verification"] = "看起来没问题"
    with pytest.raises(ProjectError, match="verification"):
        record(store, project, run)
    run = successful_run(project, tmp_path, status="verified")
    del run["inspection"]["reopened"]
    with pytest.raises(ProjectError, match="实际检查数据"):
        record(store, project, run)


def test_artifact_hashes_are_computed_and_user_confirmation_is_not_inferred(store, project, tmp_path):
    project = define(store, project)
    run = successful_run(project, tmp_path)
    run["user_confirmation"] = "用户通过"
    project = record(store, project, run)
    latest = project["data"]["components"]["primary-button"]["latest_run"]
    assert latest["artifact_hashes"]["document"] == hashlib.sha256(Path(run["document"]).read_bytes()).hexdigest()
    assert latest["user_confirmation"] is None


def test_reference_ids_and_rule_evidence_must_exist(store, project):
    project = store.add_reference(project["project_root"], project["revision"], {"id": "ref-1", "source": "参考.png"})
    with pytest.raises(ProjectError, match="已存在"):
        store.add_reference(project["project_root"], project["revision"], {"id": "ref-1"})
    with pytest.raises(ProjectError, match="不存在的参考"):
        store.update_style(project["project_root"], project["revision"], "", [], {}, ["unknown"])
    with pytest.raises(ProjectError, match="不存在的参考"):
        store.update_style(project["project_root"], project["revision"], "", [{"evidence": {"reference_id": "unknown"}}], {}, [])
    project = store.update_style(project["project_root"], project["revision"], "明确规则", [{"reference_ids": ["ref-1"]}], {}, ["ref-1"])
    assert project["data"]["style"]["reference_ids"] == ["ref-1"]


def test_lock_conflict_is_explicit_and_does_not_remove_other_lock(store, project):
    lock = Path(project["project_root"]) / ".smartui/project.lock"
    lock.write_text("other-process")
    with pytest.raises(ProjectError, match="项目正在被其他操作使用"):
        store.context(project["project_root"])
    assert lock.read_text() == "other-process"


def test_nan_is_rejected_without_changing_project(store, project):
    with pytest.raises(ProjectError, match="JSON"):
        store.update_style(project["project_root"], project["revision"], "", [], {"radius": float("nan")}, [])
    assert store.context(project["project_root"]) == project


def test_interrupted_multi_file_write_restores_project_state(store, project, monkeypatch):
    import os
    real_replace = os.replace
    def fail_state_replace(source, destination):
        if destination.name == "project.json":
            raise OSError("模拟磁盘错误")
        return real_replace(source, destination)
    monkeypatch.setattr("smartui_mcp.projects.os.replace", fail_state_replace)
    with pytest.raises(ProjectError, match="写入失败.*已恢复先前状态"):
        store.update_style(project["project_root"], project["revision"], "发生中断", [], {}, [])
    assert store.context(project["project_root"]) == project


def test_json_source_has_no_derived_review_state(store, project):
    project = define(store, project)
    root = Path(project["project_root"])
    state = json.loads((root / "smart-ui/project.json").read_bytes())
    component_path = state["components"]["primary-button"]
    assert component_path == "smart-ui/components/primary-button.json"
    assert "needs_review" not in json.loads((root / component_path).read_bytes())
    assert project["data"]["components"]["primary-button"]["needs_review"]


def test_markdown_summarizes_current_rules_references_and_assets(store, project, tmp_path):
    reference = {"id": "sample", "source": str(tmp_path / "参考图.png"), "width": 800, "height": 600}
    project = store.add_reference(project["project_root"], project["revision"], reference)
    rule = {"id": "round-corners", "description": "按钮使用圆角", "evidence": {
        "reference_id": "sample", "note": "参考图中按钮四角一致"}}
    project = store.update_style(project["project_root"], project["revision"], "项目风格", [rule], {"radius": 8}, ["sample"])
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path, status="verified"))
    root = Path(project["project_root"])
    style = (root / "smart-ui/docs/style.md").read_text(encoding="utf-8")
    components = (root / "smart-ui/docs/components.md").read_text(encoding="utf-8")
    assert "round-corners" in style
    assert "按钮使用圆角" in style
    assert "参考图中按钮四角一致" in style
    assert "sample" in style
    assert "主按钮（primary-button）" in components
    assert "需要复查：否" in components
    assert "(<../programs/button.py>)" in components
    assert "[可编辑源文件]" in components
    assert "[当前预览]" in components
    assert "用户美术确认" in components

def save_program(store, project, source="result = []\n", name="按钮程序", description="程序说明"):
    return store.save_program(project["project_root"], project["revision"], "button", name, description, source)


def test_program_source_is_saved_without_execution_and_updates_same_file(store, project):
    source = "raise RuntimeError('保存时不得执行')\r\n"
    project = save_program(store, project, source)
    first = store.get_program(project["project_root"], "button")
    assert first["source"] == source
    assert first["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert first["source_path"] == "smart-ui/programs/button.py"
    renamed = save_program(store, project, source, "可维护的按钮程序", "补充使用说明")
    assert renamed["data"]["programs"]["button"]["sha256"] == first["sha256"]
    updated = save_program(store, renamed, "result = ['new']\n")
    program = updated["data"]["programs"]["button"]
    assert program["source_path"] == first["source_path"]
    assert program["sha256"] != first["sha256"]
    assert set(program) == {"id", "name", "description", "source_path", "sha256"}
    assert store.get_program(project["project_root"], "button")["source"] == "result = ['new']\n"
    root = Path(project["project_root"])
    assert sorted(path.name for path in (root / "smart-ui/programs").iterdir()) == ["button.json", "button.py"]

@pytest.mark.parametrize("source", ["", " \n", "def broken(:\n", "\x00"])
def test_invalid_program_source_does_not_change_project(store, project, source):
    with pytest.raises(ProjectError, match="程序 button 源码无效"):
        save_program(store, project, source)
    assert store.context(project["project_root"]) == project
    assert not (Path(project["project_root"]) / "smart-ui/programs").exists()


def test_stale_revision_cannot_write_program_file(store, project):
    current = save_program(store, project)
    with pytest.raises(ProjectError, match="revision 冲突"):
        save_program(store, project, "result = [1]\n")
    assert (Path(project["project_root"]) / "smart-ui/programs/button.py").read_bytes() == b"result = []\n"
    assert store.context(project["project_root"]) == current


def test_existing_orphan_program_file_is_not_overwritten(store, project):
    orphan = Path(project["project_root"]) / "smart-ui/programs/button.py"
    orphan.parent.mkdir()
    orphan.write_text("保留未登记的源码", encoding="utf-8")
    with pytest.raises(ProjectError, match="未登记.*不能覆盖"):
        save_program(store, project, "result = [2]\n")
    assert orphan.read_text(encoding="utf-8") == "保留未登记的源码"
    assert store.context(project["project_root"]) == project

@pytest.mark.parametrize("access", ["context", "get_program", "save_program"])
def test_program_tampering_is_reported_before_read_or_update(store, project, access):
    project = save_program(store, project)
    path = Path(project["project_root"]) / "smart-ui/programs/button.py"
    path.write_text("result = ['手改']\n", encoding="utf-8")
    before = (Path(project["project_root"]) / "smart-ui/project.json").read_bytes()
    with pytest.raises(ProjectError, match="手动修改.*摘要不一致"):
        if access == "context":
            store.context(project["project_root"])
        elif access == "get_program":
            store.get_program(project["project_root"], "button")
        else:
            save_program(store, project)
    assert (Path(project["project_root"]) / "smart-ui/project.json").read_bytes() == before


def test_program_metadata_cannot_redirect_source_outside_managed_directory(store, project, tmp_path):
    project = save_program(store, project)
    path = Path(project["project_root"]) / "smart-ui/programs/button.json"
    record = json.loads(path.read_bytes())
    record["source_path"] = str(tmp_path / "outside.py")
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ProjectError, match="程序路径或标识不一致"):
        store.context(project["project_root"])

def test_program_directory_link_is_rejected(store, project, tmp_path):
    from unittest.mock import patch
    linked = Path(project["project_root"]) / "smart-ui/programs"
    real_is_symlink = Path.is_symlink
    with patch.object(Path, "is_symlink", lambda path: path == linked or real_is_symlink(path)):
        with pytest.raises(ProjectError, match="符号链接"):
            save_program(store, project)
    assert not linked.exists()


@pytest.mark.parametrize("edit", [lambda state: state.update(schema_version=1), lambda state: state.pop("programs")])
def test_schema_requires_explicit_program_collection(store, project, edit):
    path = Path(project["project_root"]) / "smart-ui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    edit(state)
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ProjectError, match="schema_version=5|programs"):
        store.context(project["project_root"])


def test_component_requires_registered_program_but_keeps_arbitrary_spec(store, project):
    for spec in ({}, {"program_id": "unknown"}):
        with pytest.raises(ProjectError, match="program_id"):
            store.define_component(project["project_root"], project["revision"], "custom", "自定义组件", spec)
    project = save_program(store, project)
    spec = {"program_id": "button", "custom_shape": {"method": "任意算法"}, "glossy": False}
    project = store.define_component(project["project_root"], project["revision"], "custom", "自定义组件", spec)
    assert project["data"]["components"]["custom"]["spec"] == spec


def test_program_update_preserves_success_and_requires_review(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path))
    latest = project["data"]["components"]["primary-button"]["latest_run"]
    project = save_program(store, project, "result = ['新绘制方法']\n")
    component = project["data"]["components"]["primary-button"]
    assert component["needs_review"]
    assert component["latest_run"] == latest
    assert latest["program_sha256"] != project["data"]["programs"]["button"]["sha256"]


@pytest.mark.parametrize("field,value", [("program_id", "other"), ("program_sha256", "0" * 64)])
def test_successful_run_must_match_current_program(store, project, tmp_path, field, value):
    project = define(store, project)
    run = successful_run(project, tmp_path)
    run[field] = value
    with pytest.raises(ProjectError, match=field):
        record(store, project, run)
    assert store.context(project["project_root"]) == project


def test_replacing_current_result_keeps_only_latest_and_checks_plan_hash(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path))
    current = successful_run(project, tmp_path, run_id="run-2")
    Path(current["plan_path"]).write_text('{"nodes": []}', encoding="utf-8")
    project = record(store, project, current)
    component = project["data"]["components"]["primary-button"]
    assert component["latest_run"]["run_id"] == "run-2"
    assert "runs" not in component
    assert component["latest_run"]["artifact_hashes"]["plan_path"] == hashlib.sha256(
        Path(current["plan_path"]).read_bytes()).hexdigest()



def test_component_id_case_collision_is_rejected_before_publication(store, project):
    project = define(store, project)
    root = Path(project["project_root"])
    component = root / "smart-ui/components/primary-button.json"
    before = component.read_bytes()

    with pytest.raises(ProjectError, match="component_id.*仅大小写不同"):
        store.define_component(str(root), project["revision"], "Primary-Button", "重复", {"program_id": "button"})

    assert component.read_bytes() == before
    assert store.context(str(root)) == project


def test_image_id_case_collision_is_rejected_before_replacing_image(store, project, tmp_path):
    from PIL import Image

    original = tmp_path / "original.png"
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (4, 4), "red").save(original)
    Image.new("RGB", (4, 4), "blue").save(replacement)
    root = project["project_root"]
    provenance = {"kind": "imported", "description": "测试图片"}
    registered = store.register_image(root, project["revision"], "icon", "图标", str(original), provenance)

    with pytest.raises(ProjectError, match="asset_id.*仅大小写不同"):
        store.register_image(root, registered["revision"], "Icon", "重复", str(replacement), provenance)

    image = store.get_image(root, "icon")["image"]
    assert Path(image["path"]).read_bytes() == original.read_bytes()
    assert store.context(root)["revision"] == registered["revision"]
