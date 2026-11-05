import hashlib
import json
from pathlib import Path

import pytest

from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256


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
           "style_version": project["data"]["style"]["version"],
           "program_id": program["id"], "program_version": program["version"], "program_sha256": program["sha256"],
           "spec_sha256": spec_sha256(project["data"]["components"]["primary-button"]["spec"])}
    for field, filename in (("document", "button.pcraft"), ("preview", "button.png"),
                            ("operations_path", "operations.json"), ("style_snapshot_path", "style.json")):
        path = tmp_path / filename
        path.write_bytes(f"实际产物 {field}".encode())
        run[field] = str(path)
    source = tmp_path / "program.py"
    source.write_bytes((Path(project["project_root"]) / program["source_path"]).read_bytes())
    plan = tmp_path / "plan.json"
    plan.write_text('{"operations": []}', encoding="utf-8")
    saved = tmp_path / "source-snapshot.pcraft"
    saved.write_bytes(Path(run["document"]).read_bytes())
    run.update(program_snapshot_path=str(source), plan_path=str(plan), source_snapshot_path=str(saved),
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
    from pathlib import Path
    root = Path(project["project_root"])
    payload = (root / ".smartui/project.json").read_bytes()
    assert project["revision"] == hashlib.sha256(payload).hexdigest()
    assert "中文项目" in payload.decode()
    assert project["data"]["style"]["version"] == 0
    assert project["data"]["style"]["user_confirmation"] is None
    assert project["data"]["schema_version"] == 3
    assert project["data"]["programs"] == {}
    for filename in ("style.md", "components.md"):
        assert "尚未确认" in (root / "docs/ui" / filename).read_text(encoding="utf-8")
        assert project["data"]["document_hashes"][filename] == hashlib.sha256((root / "docs/ui" / filename).read_bytes()).hexdigest()
    assert store.context(str(root)) == project


@pytest.mark.parametrize("existing", [".smartui/project.json", "docs/ui/style.md", "docs/ui/components.md"])
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


@pytest.mark.parametrize("filename", ["style.md", "components.md"])
def test_manual_document_edits_are_detected_before_read_or_write(store, project, filename):
    from pathlib import Path
    root = Path(project["project_root"])
    before = (root / ".smartui/project.json").read_bytes()
    (root / "docs/ui" / filename).write_text("手工更新，必须保留", encoding="utf-8")
    with pytest.raises(ProjectError, match="手动修改"):
        store.context(str(root))
    with pytest.raises(ProjectError, match="手动修改"):
        define(store, project)
    assert (root / ".smartui/project.json").read_bytes() == before
    assert (root / "docs/ui" / filename).read_text(encoding="utf-8") == "手工更新，必须保留"


def test_projects_are_isolated(store, project, tmp_path):
    other = store.create(str(tmp_path / "other"), "other", "另一个项目")
    define(store, project)
    assert store.context(other["project_root"]) == other


def test_style_changes_preserve_old_run_and_mark_component_for_review(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path, status="verified"))
    original = project["data"]["components"]["primary-button"]["latest_run"]
    assert not project["data"]["components"]["primary-button"]["needs_review"]
    project = store.update_style(project["project_root"], project["revision"], "圆角更新", [], {"radius": 8}, [])
    component = project["data"]["components"]["primary-button"]
    assert project["data"]["style"]["version"] == 1
    assert component["target_style_version"] == 1
    assert component["needs_review"]
    assert component["latest_run"] == original
    assert component["runs"] == [original]
    assert component["latest_run"]["style_version"] == 0
    from pathlib import Path
    assert "需要重新检查：是" in (Path(project["project_root"]) / "docs/ui/components.md").read_text(encoding="utf-8")


def test_description_only_does_not_bump_style_version(store, project):
    project = store.update_style(project["project_root"], project["revision"], "增加风格解释", [], {}, [])
    assert project["data"]["style"]["version"] == 0


def test_spec_change_keeps_run_and_marks_review(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path))
    original = project["data"]["components"]["primary-button"]["latest_run"]
    project = define(store, project, {"width": 240})
    component = project["data"]["components"]["primary-button"]
    assert component["needs_review"]
    assert component["latest_run"] == original


def test_failed_run_keeps_last_success_but_requires_review(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path))
    original = project["data"]["components"]["primary-button"]["latest_run"]
    project = record(store, project, {"run_id": "run-failed", "status": "failed", "error": "PhotoCraft 绘制失败"})
    component = project["data"]["components"]["primary-button"]
    assert component["latest_run"] == original
    assert component["runs"][-1]["error"] == "PhotoCraft 绘制失败"
    assert component["needs_review"]
    from pathlib import Path
    document = (Path(project["project_root"]) / "docs/ui/components.md").read_text(encoding="utf-8")
    assert "最近尝试失败，需要重新检查" in document
    assert "不能据此确认当前磁盘文件仍然有效" in document
    assert "PhotoCraft 绘制失败" in document


@pytest.mark.parametrize("field", ["document", "preview", "operations_path", "style_snapshot_path", "program_snapshot_path", "plan_path"])
def test_missing_artifacts_cannot_be_recorded_as_success(store, project, tmp_path, field):
    project = define(store, project)
    run = successful_run(project, tmp_path)
    from pathlib import Path
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
    from pathlib import Path
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
    assert project["data"]["style"]["version"] == 1


def test_lock_conflict_is_explicit_and_does_not_remove_other_lock(store, project):
    from pathlib import Path
    lock = Path(project["project_root"]) / ".smartui/project.lock"
    lock.write_text("other-process")
    with pytest.raises(ProjectError, match="项目正在被其他操作使用"):
        store.context(project["project_root"])
    assert lock.read_text() == "other-process"


def test_nan_is_rejected_without_changing_project(store, project):
    with pytest.raises(ProjectError, match="JSON"):
        store.update_style(project["project_root"], project["revision"], "", [], {"radius": float("nan")}, [])
    assert store.context(project["project_root"]) == project


def test_interrupted_multi_file_write_is_detected_and_not_silently_repaired(store, project, monkeypatch):
    import os
    real_replace = os.replace
    def fail_state_replace(source, destination):
        if destination.name == "project.json":
            raise OSError("模拟磁盘错误")
        return real_replace(source, destination)
    monkeypatch.setattr("smartui_mcp.projects.os.replace", fail_state_replace)
    with pytest.raises(ProjectError, match="写入失败"):
        define(store, project)
    with pytest.raises(ProjectError, match="手动修改"):
        store.context(project["project_root"])


def test_json_source_has_no_derived_review_state(store, project):
    from pathlib import Path
    project = define(store, project)
    state = json.loads((Path(project["project_root"]) / ".smartui/project.json").read_text(encoding="utf-8"))
    assert "needs_review" not in state["components"]["primary-button"]
    assert project["data"]["components"]["primary-button"]["needs_review"]


def test_markdown_summarizes_rules_references_layers_and_history(store, project, tmp_path):
    from pathlib import Path
    reference = {"id": "sample", "source": str(tmp_path / "参考图.png"), "width": 800, "height": 600}
    project = store.add_reference(project["project_root"], project["revision"], reference)
    rule = {"id": "round-corners", "description": "按钮使用圆角", "evidence": {
        "reference_id": "sample", "note": "参考图中按钮四角一致"}}
    project = store.update_style(project["project_root"], project["revision"], "项目风格", [rule], {"radius": 8}, ["sample"])
    project = define(store, project)
    run = successful_run(project, tmp_path, status="verified")
    run["layer_ids"] = {f"图层 {i}": i + 1 for i in range(7)}
    run["inspection"]["before"]["large_debug_payload"] = "不应复制到文档的完整检查对象"
    project = record(store, project, run)
    root = Path(project["project_root"])
    style = (root / "docs/ui/style.md").read_text(encoding="utf-8")
    components = (root / "docs/ui/components.md").read_text(encoding="utf-8")
    assert "round-corners：按钮使用圆角" in style
    assert "参考标识：sample" in style
    assert "说明：参考图中按钮四角一致" in style
    assert "| sample | 参考图.png | 800 × 600 |" in style
    assert style.count("```json") == 1
    assert "工具检查通过，等待用户确认" in components
    assert "目标风格版本：1" in components
    assert "实际产物风格版本：1" in components
    assert "| button | 按钮程序 | 1 | [查看程序源码](<../../art/ui/programs/button/1.py>) |" in components
    assert "目标程序：按钮程序（button），版本 1" in components
    assert "实际产物程序版本：button，版本 1" in components
    assert "本次执行源码" in components
    assert "本次执行计划" in components
    assert "[button.pcraft](<" in components
    assert "[button.png](<" in components
    for index in range(7):
        assert f"| 图层 {index} | {index + 1} |" in components
    assert "像素保持一致：通过" in components
    assert "| run-1 | 工具检查通过 | 1 | — |" in components
    assert "不应复制到文档的完整检查对象" not in components
    assert components.count("```json") == 1
    assert "不应复制到文档的完整检查对象" in json.dumps(project["data"], ensure_ascii=False)


def test_success_after_failed_attempt_clears_review_flag(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, {"run_id": "failed-first", "status": "failed", "error": "中断"})
    assert project["data"]["components"]["primary-button"]["needs_review"]
    project = record(store, project, successful_run(project, tmp_path))
    assert not project["data"]["components"]["primary-button"]["needs_review"]


def save_program(store, project, source="result = []\n", name="按钮程序", description="程序说明"):
    return store.save_program(project["project_root"], project["revision"], "button", name, description, source)


def test_program_source_is_saved_without_execution_and_history_is_immutable(store, project):
    source = "raise RuntimeError('保存时不得执行')\r\n"
    project = save_program(store, project, source)
    first = store.get_program(project["project_root"], "button")
    assert first["source"] == source
    assert first["version"] == 1
    assert first["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert first["source_path"] == "art/ui/programs/button/1.py"
    assert first["history"] == [{key: first[key] for key in ("version", "sha256", "source_path")}]
    renamed = save_program(store, project, source, "可维护的按钮程序", "补充使用说明")
    assert renamed["data"]["programs"]["button"]["version"] == 1
    assert renamed["data"]["programs"]["button"]["history"] == first["history"]
    updated = save_program(store, renamed, "result = ['new']\n")
    program = updated["data"]["programs"]["button"]
    assert program["version"] == 2
    assert len(program["history"]) == 2
    assert (Path(project["project_root"]) / first["source_path"]).read_bytes() == source.encode()
    assert store.get_program(project["project_root"], "button")["source"] == "result = ['new']\n"


@pytest.mark.parametrize("source", ["", " \n", "def broken(:\n", "\x00"])
def test_invalid_program_source_does_not_change_project(store, project, source):
    with pytest.raises(ProjectError, match="source|语法无效"):
        save_program(store, project, source)
    assert store.context(project["project_root"]) == project
    assert not (Path(project["project_root"]) / "art/ui/programs").exists()


def test_stale_revision_cannot_write_program_file(store, project):
    current = save_program(store, project)
    with pytest.raises(ProjectError, match="revision 冲突"):
        save_program(store, project, "result = [1]\n")
    assert not (Path(project["project_root"]) / "art/ui/programs/button/2.py").exists()
    assert store.context(project["project_root"]) == current


def test_existing_orphan_program_version_is_not_overwritten(store, project):
    project = save_program(store, project)
    orphan = Path(project["project_root"]) / "art/ui/programs/button/2.py"
    orphan.write_text("保留未登记的源码证据", encoding="utf-8")
    with pytest.raises(ProjectError, match="已有版本不能覆盖"):
        save_program(store, project, "result = [2]\n")
    assert orphan.read_text(encoding="utf-8") == "保留未登记的源码证据"
    assert store.context(project["project_root"]) == project


@pytest.mark.parametrize("access", ["context", "get_program", "save_program"])
def test_program_tampering_is_reported_before_read_or_update(store, project, access):
    project = save_program(store, project)
    path = Path(project["project_root"]) / "art/ui/programs/button/1.py"
    path.write_text("result = ['手改']\n", encoding="utf-8")
    before = (Path(project["project_root"]) / ".smartui/project.json").read_bytes()
    with pytest.raises(ProjectError, match="手动修改.*摘要不一致"):
        if access == "context":
            store.context(project["project_root"])
        elif access == "get_program":
            store.get_program(project["project_root"], "button")
        else:
            save_program(store, project)
    assert (Path(project["project_root"]) / ".smartui/project.json").read_bytes() == before


def test_program_metadata_cannot_redirect_source_outside_managed_directory(store, project, tmp_path):
    project = save_program(store, project)
    path = Path(project["project_root"]) / ".smartui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["programs"]["button"]["source_path"] = str(tmp_path / "outside.py")
    state["programs"]["button"]["history"][0]["source_path"] = str(tmp_path / "outside.py")
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ProjectError, match="历史路径或版本无效"):
        store.context(project["project_root"])


def test_program_directory_link_is_rejected(store, project, tmp_path):
    from unittest.mock import patch
    linked = Path(project["project_root"]) / "art/ui/programs"
    real_is_symlink = Path.is_symlink
    with patch.object(Path, "is_symlink", lambda path: path == linked or real_is_symlink(path)):
        with pytest.raises(ProjectError, match="符号链接"):
            save_program(store, project)
    assert not linked.exists()


@pytest.mark.parametrize("edit", [lambda state: state.update(schema_version=1), lambda state: state.pop("programs")])
def test_schema_requires_explicit_program_collection(store, project, edit):
    path = Path(project["project_root"]) / ".smartui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    edit(state)
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ProjectError, match="不支持的项目结构|缺少 programs"):
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
    assert latest["program_version"] == 1
    document = (Path(project["project_root"]) / "docs/ui/components.md").read_text(encoding="utf-8")
    assert "目标程序：按钮程序（button），版本 2" in document
    assert "实际产物程序版本：button，版本 1" in document


@pytest.mark.parametrize("field,value", [("program_id", "other"), ("program_version", 2), ("program_sha256", "0" * 64)])
def test_successful_run_must_match_current_program(store, project, tmp_path, field, value):
    project = define(store, project)
    run = successful_run(project, tmp_path)
    run[field] = value
    with pytest.raises(ProjectError, match=field):
        record(store, project, run)
    assert store.context(project["project_root"]) == project


def test_successful_run_snapshot_must_be_actual_program_source(store, project, tmp_path):
    project = define(store, project)
    run = successful_run(project, tmp_path)
    Path(run["program_snapshot_path"]).write_text("result = ['伪造快照']\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="源码摘要与当前程序不一致"):
        record(store, project, run)
    run = successful_run(project, tmp_path)
    project = record(store, project, run)
    latest = project["data"]["components"]["primary-button"]["latest_run"]
    assert latest["artifact_hashes"]["program_snapshot_path"] == run["program_sha256"]
    assert latest["artifact_hashes"]["plan_path"] == hashlib.sha256(Path(run["plan_path"]).read_bytes()).hexdigest()


def test_old_success_without_program_identity_is_retained_and_requires_review(store, project, tmp_path):
    project = define(store, project)
    project = record(store, project, successful_run(project, tmp_path))
    path = Path(project["project_root"]) / ".smartui/project.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    component = state["components"]["primary-button"]
    for run in (component["latest_run"], component["runs"][0]):
        for field in ("program_id", "program_version", "program_sha256", "program_snapshot_path", "plan_path"):
            del run[field]
    path.write_text(json.dumps(state), encoding="utf-8")
    project = store.context(project["project_root"])
    assert project["data"]["components"]["primary-button"]["needs_review"]
    project = store.update_style(project["project_root"], project["revision"], "更新说明", [], {}, [])
    latest = project["data"]["components"]["primary-button"]["latest_run"]
    assert "program_id" not in latest
    assert "program_snapshot_path" not in latest
    assert "历史记录未保存" in (Path(project["project_root"]) / "docs/ui/components.md").read_text(encoding="utf-8")
