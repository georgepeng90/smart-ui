from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from smartui_mcp.migration import apply_layout_migration, migrate_project_layout, plan_layout_migration
from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return str(path)


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / "game"
    old = root / "docs/ui-reference"
    source = b"result = []\n"
    program_path = "programs/button/1.py"
    write(old / program_path, source)
    program = {"id": "button", "name": "button", "description": "", "version": 1,
               "sha256": sha(source), "source_path": program_path,
               "history": [{"version": 1, "sha256": sha(source), "source_path": program_path}]}
    style = {"version": 1, "description": "[说明](guide.md)", "rules": [], "parameters": {},
             "reference_ids": ["ref"], "user_confirmation": None}
    spec = {"program_id": "button", "width": 32}
    reference = {"id": "ref", "source": write(old / "references/ref.png", b"reference pixels"),
                 "preview": write(old / ".smartui/cache/ref.png", b"preview pixels"),
                 "width": 32, "height": 32, "sha256": sha(b"reference pixels")}
    run = {"run_id": "run-1", "status": "verified", "style_version": 1,
           "program_id": "button", "program_version": 1, "program_sha256": sha(source),
           "spec_sha256": spec_sha256(spec), "layer_ids": {}, "verification": {}}
    artifacts = {"document": ("assets/button.kra", b"KRA pixels"),
                 "preview": (".smartui/runs/run-1/preview.png", b"PNG pixels"),
                 "operations_path": (".smartui/runs/run-1/operations.json", json.dumps([
                     {"method": "inspect", "params": {"document": str(old / "assets/button.kra")}}]).encode()),
                 "style_snapshot_path": (".smartui/runs/run-1/style-snapshot.json", json.dumps(
                     {"style": style, "spec": spec, "component_id": "button"}).encode()),
                 "program_snapshot_path": (".smartui/runs/run-1/program.py", source),
                 "plan_path": (".smartui/runs/run-1/plan.json", b'{"nodes": []}'),
                 "source_snapshot_path": (".smartui/runs/run-1/output.kra", b"KRA pixels")}
    run["artifact_hashes"] = {}
    for field, (relative, payload) in artifacts.items():
        run[field] = write(old / relative, payload)
        run["artifact_hashes"][field] = sha(payload)
    write(old / ".smartui/runs/run-1/result.json", json.dumps(run).encode())
    metadata = {key: run[key] for key in ("run_id", "status", "style_version", "spec_sha256", "program_id", "program_version", "program_sha256")}
    comparison_path = write(old / ".smartui/comparisons/compare/comparison.png", b"comparison pixels")
    comparison = {"component_id": "button", "run": metadata, "reference": {"id": "ref", "source": reference["source"],
                   "region_source_xywh": [0, 0, 32, 32]}, "artifact": {"source": run["preview"]},
                  "images": {"comparison": {"path": comparison_path, "sha256": sha(b"comparison pixels")}}}
    check_path = write(old / ".smartui/visual-checks/check/overview.png", b"check pixels")
    check = {"id": "check", "display": {"background": "#FFFFFF", "columns": 1, "grayscale": None}, "items": [{"component_id": "button", "run": metadata,
             "source": run["preview"], "label": "button", "region_source_xywh": [0, 0, 32, 32], "scale": 1}],
             "images": {"overview": {"path": check_path, "sha256": sha(b"check pixels")}}}
    state = {"schema_version": 2, "project_id": "demo", "name": "demo", "references": {"ref": reference},
             "style": style, "programs": {"button": program}, "components": {"button": {"name": "button", "spec": spec,
             "target_style_version": 1, "runs": [run], "latest_run": deepcopy(run)}}, "comparisons": {"compare": comparison},
             "feedback": {}, "visual_checks": {"check": check}}
    documents = ProjectStore()._render(state)
    state["document_hashes"] = {name: sha(payload) for name, payload in documents.items()}
    for name, payload in documents.items():
        write(old / name, payload)
    write(old / "guide.md", b"[Program](programs/button/1.py)\n![Reference](references/ref.png)\n[Source]: assets/button.kra\n")
    write(old / "project.json", json.dumps(state).encode())
    return root, old, state


def test_dry_run_has_no_filesystem_effect(legacy):
    root, old, _ = legacy
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    report = migrate_project_layout(old, root)
    assert report["applied"] is False
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert not (root / ".smartui").exists()


def test_complete_history_moves_and_can_be_read_after_project_move(legacy, tmp_path):
    root, old, original = legacy
    before = {path.relative_to(old): path.read_bytes() for path in old.rglob("*") if path.is_file()}
    report = migrate_project_layout(old, root, apply=True)
    assert report["source_retained"] and report["historical_call_records_preserved"]
    backup = Path(report["backup"])
    for relative, payload in before.items():
        assert (old / relative).read_bytes() == payload
        assert (backup / "source" / relative).read_bytes() == payload
    assert (root / "art/ui/sources/button.kra").read_bytes() == b"KRA pixels"
    assert (root / ".smartui/runs/run-1/operations.json").read_bytes() == before[Path(".smartui/runs/run-1/operations.json")]
    encoded = json.loads((root / ".smartui/project.json").read_bytes())
    assert encoded["programs"]["button"]["history"][0]["source_path"] == "art/ui/programs/button/1.py"
    run = encoded["components"]["button"]["latest_run"]
    assert run["document"] == "art/ui/sources/button.kra"
    assert run["artifact_hashes"] == original["components"]["button"]["latest_run"]["artifact_hashes"]
    guide = (root / "docs/ui/guide.md").read_text()
    assert "../../art/ui/programs/button/1.py" in guide
    assert "[Source]: ../../art/ui/sources/button.kra" in guide
    moved = tmp_path / "moved-game"
    shutil.move(str(root), moved)
    store = ProjectStore()
    context = store.context(str(moved))
    assert store.get_program(str(moved), "button")["source"] == "result = []\n"
    runtime_run = context["data"]["components"]["button"]["latest_run"]
    assert Path(runtime_run["document"]) == moved / "art/ui/sources/button.kra"
    assert store._run_artifact(runtime_run, "operations_path") == before[Path(".smartui/runs/run-1/operations.json")]
    store.get_comparison(str(moved), "compare")
    store.get_visual_check(str(moved), "check")
    store._review_run(context["data"], "button", "run-1")
    updated = store.save_program(str(moved), context["revision"], "button", "button", "", "result = [1]\n")
    assert updated["data"]["programs"]["button"]["version"] == 2
    assert (moved / "art/ui/programs/button/2.py").read_text() == "result = [1]\n"


@pytest.mark.parametrize("conflict", ["docs/ui/style.md", "art/ui/programs/button/1.py", ".smartui/runs/run-1/output.kra", ".smartui/project.json"])
def test_target_conflict_fails_before_backup_or_writes(legacy, conflict):
    root, old, _ = legacy
    write(root / conflict, b"user file")
    with pytest.raises(ProjectError, match="不能覆盖"):
        migrate_project_layout(old, root, apply=True)
    assert (root / conflict).read_bytes() == b"user file"
    assert not (root / ".smartui/migration-backups").exists()


def test_source_changes_after_plan_are_rejected(legacy):
    root, old, _ = legacy
    plan = plan_layout_migration(old, root)
    (old / "guide.md").write_bytes(b"new user edit")
    with pytest.raises(ProjectError, match="源文件已变化"):
        apply_layout_migration(plan)
    assert not (root / ".smartui/project.json").exists()
    assert not (old / ".smartui-project.lock").exists()


def test_changed_document_and_missing_recorded_resource_fail(legacy):
    root, old, _ = legacy
    original = (old / "style.md").read_bytes()
    (old / "style.md").write_bytes(b"manual changes")
    with pytest.raises(ProjectError, match="受管文档"):
        migrate_project_layout(old, root)
    (old / "style.md").write_bytes(original)
    (old / ".smartui/runs/run-1/plan.json").unlink()
    with pytest.raises(ProjectError, match="文件缺失"):
        migrate_project_layout(old, root)


def test_external_reference_is_copied_into_project(legacy, tmp_path):
    root, old, state = legacy
    external = tmp_path / "external.png"
    external.write_bytes(b"reference pixels")
    state["references"]["ref"]["source"] = str(external)
    (old / "project.json").write_text(json.dumps(state))
    migrate_project_layout(old, root, apply=True)
    data = json.loads((root / ".smartui/project.json").read_bytes())
    assert data["references"]["ref"]["source"] == "docs/ui/references/ref/external.png"
    assert (root / data["references"]["ref"]["source"]).read_bytes() == external.read_bytes()


def test_cannot_repeat_or_migrate_in_place(legacy):
    root, old, _ = legacy
    with pytest.raises(ProjectError, match="独立目录"):
        migrate_project_layout(old, old)
    migrate_project_layout(old, root, apply=True)
    with pytest.raises(ProjectError, match="不能覆盖"):
        migrate_project_layout(old, root, apply=True)


@pytest.mark.parametrize("relative", ["assets/button.kra", ".smartui/runs/run-1/plan.json", ".smartui/comparisons/compare/comparison.png"])
def test_corrupt_registered_artifact_is_rejected_before_writes(legacy, relative):
    root, old, _ = legacy
    (old / relative).write_bytes(b"corruption")
    with pytest.raises(ProjectError, match="摘要不符"):
        migrate_project_layout(old, root, apply=True)
    assert not (root / ".smartui").exists()


def test_historical_document_hash_remains_valid_history(legacy):
    root, old, state = legacy
    historical = deepcopy(state["components"]["button"]["runs"][0])
    historical["run_id"] = "older-run"
    historical["artifact_hashes"]["document"] = sha(b"previous KRA pixels")
    state["components"]["button"]["runs"].insert(0, historical)
    (old / "project.json").write_text(json.dumps(state))
    migrate_project_layout(old, root, apply=True)
    assert ProjectStore().context(str(root))["data"]["components"]["button"]["runs"][0]["artifact_hashes"]["document"] == sha(b"previous KRA pixels")


def test_ignore_rules_extend_old_file_without_modifying_source(legacy):
    root, old, _ = legacy
    write(old / ".smartui/.gitignore", b"custom-cache/\n")
    migrate_project_layout(old, root, apply=True)
    assert (old / ".smartui/.gitignore").read_bytes() == b"custom-cache/\n"
    rules = (root / ".smartui/.gitignore").read_text(encoding="utf-8").splitlines()
    assert {"custom-cache/", "cache/", "project.lock", ".*.tmp", "migration-backups/"} <= set(rules)


def test_nested_managed_document_names_and_absolute_markdown_links(legacy):
    root, old, _ = legacy
    target = (old / "assets/button.kra").as_posix()
    write(old / "notes/style.md", (
        "[Program](../programs/button/1.py)\n"
        f"[Source](<{target}#layers>)\n"
        "[Website](https://example.com/style.md)\n").encode())
    migrate_project_layout(old, root, apply=True)
    text = (root / "docs/ui/notes/style.md").read_text()
    assert "../../../art/ui/programs/button/1.py" in text
    assert "<../../../art/ui/sources/button.kra#layers>" in text
    assert "https://example.com/style.md" in text


@pytest.mark.parametrize("payload", [b"[]", b'{"schema_version": 3}', b'{"schema_version": 2}'])
def test_invalid_old_state_fails_with_project_error(legacy, payload):
    root, old, _ = legacy
    (old / "project.json").write_bytes(payload)
    with pytest.raises(ProjectError, match="旧|schema_version"):
        migrate_project_layout(old, root)


def test_invalid_program_metadata_is_rejected_before_writes(legacy):
    root, old, state = legacy
    state["programs"]["button"]["version"] = 5
    (old / "project.json").write_text(json.dumps(state))
    with pytest.raises(ProjectError, match="程序元数据无效"):
        migrate_project_layout(old, root, apply=True)
    assert not (root / ".smartui").exists()
