"""验证真实候选制作与项目并发写入不会使源文件脱离运行记录。"""

import hashlib
import json
from pathlib import Path

import pytest

from smartui_mcp.client import PhotoCraftClient
from smartui_mcp.pipeline import draw_component
from smartui_mcp.projects import ProjectError, ProjectStore


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
    failures = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / ".smartui/runs").glob("*/result.json")]
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
