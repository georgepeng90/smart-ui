"""显式迁移 v0.2 按钮项目，保留旧文档和实际绘制历史，不改已有资产。"""

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

from smartui_mcp.projects import ProjectStore, ProjectError, _identifier
from smartui_mcp.programs import validate_source


def migrate(project_root, program_source, program_id):
    root = Path(project_root).resolve()
    _identifier(program_id, "program_id")
    source = Path(program_source).read_bytes()
    validate_source(source.decode("utf-8"))
    store = ProjectStore()
    with store._lock(root):
        state = json.loads((root / "project.json").read_bytes())
        if state["schema_version"] != 1:
            raise ProjectError("此迁移仅用于 schema_version=1 的项目，不重复执行")
        for filename in ("style.md", "components.md"):
            if hashlib.sha256((root / filename).read_bytes()).hexdigest() != state["document_hashes"][filename]:
                raise ProjectError("受管文档已被修改，迁移前必须核对：" + filename)
        if state["style"]["parameters"].get("recipe") != "glossy_button" or any(
            value["spec"].get("recipe") != "glossy_button" for value in state["components"].values()
        ):
            raise ProjectError("此迁移只转换旧版 glossy_button 示例项目，不能猜测其他程序的参数")
        backup = root / ".smartui" / "migrations" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup.mkdir(parents=True)
        for filename in ("project.json", "style.md", "components.md"):
            shutil.copyfile(root / filename, backup / filename)
        path = store._program_path(root, f"programs/{program_id}/1.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(source)
        identity = {"version": 1, "sha256": hashlib.sha256(source).hexdigest(), "source_path": path.relative_to(root).as_posix()}
        state["schema_version"] = 2
        state["programs"] = {program_id: {"id": program_id, "name": "SlimeBlock 亮面按钮绘制程序",
                            "description": "从旧版内置按钮实现移入项目，供项目独立维护；历史绘制记录保持原样。",
                            **identity, "history": [deepcopy(identity)]}}
        state["style"]["parameters"].pop("recipe")
        state["style"]["version"] += 1
        for component in state["components"].values():
            component["spec"].pop("recipe")
            component["spec"]["program_id"] = program_id
            component["target_style_version"] = state["style"]["version"]
        context = store._write(root, state)
        report = {"backup": str(backup), "revision": context["revision"], "schema_version": 2,
                  "program_id": program_id, "asset_files_unchanged": True}
        (backup / "migration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--program-source", required=True, type=Path)
    parser.add_argument("--program-id", required=True)
    arguments = parser.parse_args()
    print(json.dumps(migrate(arguments.project_root, arguments.program_source, arguments.program_id), ensure_ascii=False, indent=2))
