"""验证真实候选制作与项目并发写入不会使源文件脱离运行记录。"""

import hashlib
import json
from pathlib import Path

import pytest

from smartui_mcp.client import PhotoCraftClient
from smartui_mcp.pipeline import draw_component
from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256, style_sha256


PROGRAM = '''def build(parameters, spec, component_id):
    return {"schema_version": 1, "width": 32, "height": 32, "nodes": [
        {"key": "base", "name": "base", "kind": "shape", "parent": "root",
         "shape": {"kind": "rect", "rect": [0, 0, 24, 24], "fill": spec["color"]}}]}
'''


def test_publication_keeps_source_and_record_consistent_during_concurrent_updates(tmp_path, monkeypatch):
    client = PhotoCraftClient()
    try:
        client.status()
    except FileNotFoundError:
        pytest.skip("真实发布验证需要安装受支持的 PhotoCraft")
    root = str(tmp_path)
    store = ProjectStore()
    context = store.create(root, "publication", "发布并发验证")
    context = store.save_program(root, context["revision"], "drawing", "drawing", "drawing", PROGRAM)
    context = store.define_component(root, context["revision"], "button", "button",
                                     {"program_id": "drawing", "color": "#ff0000"})
    created = draw_component(root, context["revision"], "button", client=client, store=store)
    document = Path(created["document"])
    original_source = document.read_bytes()
    context = store.context(root)
    context = store.define_component(root, context["revision"], "button", "button",
                                     {"program_id": "drawing", "color": "#00ff00"})
    publish = store.publish_run

    def update_before_publication(*args, **kwargs):
        current = store.context(root)
        store.define_component(root, current["revision"], "other", "other", {"program_id": "drawing"})
        return publish(*args, **kwargs)

    monkeypatch.setattr(store, "publish_run", update_before_publication)
    with pytest.raises(RuntimeError, match="revision"):
        draw_component(root, context["revision"], "button", client=client, store=store)
    assert document.read_bytes() == original_source
    context = store.context(root)
    assert context["data"]["components"]["button"]["latest_run"]["run_id"] == created["run_id"]
    failures = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / ".smartui/diagnostics").glob("*/result.json")]
    assert any(item["status"] == "failed" and "revision" in item["error"] for item in failures)
    monkeypatch.setattr(store, "publish_run", publish)

    import smartui_mcp.projects as projects_module
    replace = projects_module.os.replace
    checked = []

    def replace_while_attempting_project_write(source, destination):
        if Path(destination) == document:
            with pytest.raises(ProjectError, match="其他操作"):
                store.define_component(root, context["revision"], "racing", "racing", {"program_id": "drawing"})
            checked.append(True)
        return replace(source, destination)

    monkeypatch.setattr(projects_module.os, "replace", replace_while_attempting_project_write)
    updated = draw_component(root, context["revision"], "button", client=client, store=store)
    latest = store.context(root)["data"]["components"]["button"]["latest_run"]
    assert checked == [True]
    assert latest["run_id"] == updated["run_id"]
    assert document.read_bytes() != original_source
    assert hashlib.sha256(document.read_bytes()).hexdigest() == latest["artifact_hashes"]["document"]
    assert "cli_path" in updated["backend"]
    assert "cli_path" not in latest["backend"]



def publication_candidate(root, context, run_id):
    candidate = root / ".smartui/temp" / run_id
    candidate.mkdir(parents=True)
    files = {}
    for field, filename in (
        ("source_snapshot_path", "button.pcraft"), ("preview", "preview.png"),
        ("plan_path", "plan.json"), ("style_snapshot_path", "inputs.json"),
    ):
        path = candidate / filename
        path.write_bytes(f"{run_id} {filename}".encode())
        files[field] = str(path)
    data = context["data"]
    return {
        **files, "run_id": run_id, "status": "verified",
        "document": str(root / "smart-ui/sources/button.pcraft"),
        "style_sha256": style_sha256(data["style"]),
        "spec_sha256": spec_sha256(data["components"]["button"]["spec"]),
        "program_id": "drawing", "program_sha256": data["programs"]["drawing"]["sha256"],
        "backend": {"name": "photocraft", "version": "测试", "sha256": "0" * 64, "cli_path": str(root / "cli")},
        "verification": dict.fromkeys(("reopened", "stable_node_ids", "stable_pixels", "stable_properties"), True),
        "inspection": {"before": {"checked": True}, "reopened": {"checked": True}},
    }


def persistent_files(root):
    return {path.relative_to(root): path.read_bytes() for path in (root / "smart-ui").rglob("*") if path.is_file()}


def test_publication_checks_blocked_preview_before_writing_source(tmp_path):
    root = tmp_path.resolve()
    store = ProjectStore()
    context = store.create(str(root), "publication", "发布验证")
    context = store.save_program(str(root), context["revision"], "drawing", "Drawing", "", PROGRAM)
    context = store.define_component(str(root), context["revision"], "button", "按钮", {"program_id": "drawing"})
    blocked = root / "smart-ui/previews/components"
    blocked.parent.mkdir()
    blocked.write_bytes(b"blocked")
    before = persistent_files(root)
    run = publication_candidate(root, context, "blocked-preview")

    with pytest.raises(ProjectError, match="发布目录被文件占用"):
        store.publish_run(str(root), context["revision"], "button", run)

    assert persistent_files(root) == before
    assert store.context(str(root)) == context


@pytest.mark.parametrize("target", ["smart-ui/previews/components/button.png", "smart-ui/project.json"])
def test_publication_restores_persistent_files_after_replace_error(tmp_path, monkeypatch, target):
    import os

    root = tmp_path.resolve()
    store = ProjectStore()
    context = store.create(str(root), "publication", "发布验证")
    context = store.save_program(str(root), context["revision"], "drawing", "Drawing", "", PROGRAM)
    spec = {"program_id": "drawing", "color": "#ff0000"}
    context = store.define_component(str(root), context["revision"], "button", "按钮", spec)
    first = publication_candidate(root, context, "first")
    context = store.publish_run(str(root), context["revision"], "button", first)
    context = store.define_component(str(root), context["revision"], "button", "按钮", spec | {"color": "#00ff00"})
    second = publication_candidate(root, context, "second")
    before = persistent_files(root)
    temporary = set((root / ".smartui/temp").iterdir())
    replace = os.replace

    def fail_target(source, destination):
        if destination == root / target:
            assert (root / "smart-ui/sources/button.pcraft").read_bytes() == b"second button.pcraft"
            raise OSError("模拟发布中断")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_target)
        with pytest.raises(ProjectError, match="模拟发布中断.*已恢复先前状态"):
            store.publish_run(str(root), context["revision"], "button", second)

    assert persistent_files(root) == before
    assert store.context(str(root)) == context
    assert set((root / ".smartui/temp").iterdir()) == temporary
    updated = store.publish_run(str(root), context["revision"], "button", second)
    assert updated["data"]["components"]["button"]["latest_run"]["run_id"] == "second"
