"""用子进程替身检查 Unity CLI 参数与失败处理。"""

import json
import subprocess
import sys

import pytest

from smartui_mcp.unity_cli import UnityCliClient, UnityCliError


@pytest.fixture
def cli(tmp_path, monkeypatch):
    project = tmp_path / "含空格 Unity 工程 & UI"
    project.mkdir()
    request = project / "请求 文件.json"
    request.write_text("{}", encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    script = tmp_path / "fake_cli.py"
    script.write_text(
        """
import json
import os
from pathlib import Path
import sys

args = sys.argv[2:]
with Path(sys.argv[1]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
case = os.environ["SMARTUI_TEST_CLI_CASE"]
if "--detail" in args:
    if case == "discovery_failed":
        print(json.dumps({
            "success": False, "data": None,
            "errors": [{"code": "COMMAND_FAILED", "message": "Not a Unity project"}],
        }))
        sys.exit(1)
    registration = {
        "command": "smartui execute", "group": "smartui", "name": "execute",
        "parameters": [{"name": "request_path", "type": "String", "required": True}],
    }
    if case == "missing":
        registration["group"] = "other"
    if case == "wrong_parameter":
        registration["parameters"][0]["name"] = "path"
    target = Path(args[args.index("--project-path") + 1])
    if case == "wrong_project":
        target = target.parent / "Different Project"
    print(json.dumps({"success": True, "data": {"commands": [registration], "target": {"projectPath": str(target)}}}))
else:
    if case == "timeout":
        import time
        time.sleep(60)
    if case == "editor_failed":
        print(json.dumps({
            "success": False, "data": None,
            "errors": [{"code": "COMMAND_FAILED", "message": "Editor timed out"}],
        }))
        print("Editor diagnostic", file=sys.stderr)
        sys.exit(1)
    if case == "malformed":
        print("broken")
        sys.exit(0)
    if case == "raw_receipt":
        print(json.dumps({"request_id": "build-001", "result_path": "result.json", "status": "succeeded"}))
        sys.exit(0)
    print(json.dumps({"success": True, "data": {"result": "written"}}))
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTUI_TEST_CLI_CASE", "ok")
    original_run = subprocess.run

    def run_fake(argv, **options):
        assert argv[0] == "unity"
        assert options["shell"] is False
        assert options["stdin"] == subprocess.DEVNULL
        assert options["timeout"] == 185
        if "--request_path" in argv:
            options["timeout"] = 1
        return original_run([sys.executable, str(script), str(log), *argv[1:]], **options)

    monkeypatch.setattr(subprocess, "run", run_fake)

    def calls():
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return UnityCliClient(), project, request, calls


def test_discovers_then_executes_fixed_command(cli):
    client, project, request, calls = cli
    assert client.execute(project, request) == {"result": "written"}
    discovery, execute = calls()
    common_args = [
        "--format", "json", "--no-banner", "--non-interactive",
        "command", "--project-path", str(project), "--timeout", "180",
    ]
    assert discovery == [*common_args, "--detail", "full"]
    assert execute == [*common_args, "smartui", "execute", "--request_path", str(request)]


def test_accepts_editor_formatted_execution_receipt(cli, monkeypatch):
    client, project, request, calls = cli
    monkeypatch.setenv("SMARTUI_TEST_CLI_CASE", "raw_receipt")
    assert client.execute(project, request) == {
        "request_id": "build-001", "result_path": "result.json", "status": "succeeded",
    }
    assert len(calls()) == 2


def test_inspection_reports_missing_registration(cli, monkeypatch):
    client, project, request, calls = cli
    monkeypatch.setenv("SMARTUI_TEST_CLI_CASE", "missing")
    assert client.inspect(project) == {
        "project_path": str(project), "command": "smartui.execute",
        "available": False, "registration": None,
    }
    with pytest.raises(UnityCliError, match="未登记命令") as error:
        client.execute(project, request)
    assert not error.value.indeterminate
    assert all("--detail" in call for call in calls())


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("wrong_parameter", "必填字符串参数"),
        ("discovery_failed", "Not a Unity project"),
        ("wrong_project", "返回的工程路径不匹配"),
    ],
)
def test_discovery_failure_blocks_dispatch(cli, monkeypatch, case, reason):
    client, project, request, calls = cli
    monkeypatch.setenv("SMARTUI_TEST_CLI_CASE", case)
    with pytest.raises(UnityCliError, match=reason) as error:
        client.execute(project, request)
    assert not error.value.indeterminate
    assert len(calls()) == 1


@pytest.mark.parametrize(
    ("case", "reason"),
    [("timeout", "等待超时"), ("editor_failed", "Editor timed out"), ("malformed", "无效的 Unity CLI JSON")],
)
def test_execution_failure_preserves_diagnostics_and_does_not_retry(cli, monkeypatch, case, reason):
    client, project, request, calls = cli
    monkeypatch.setenv("SMARTUI_TEST_CLI_CASE", case)
    with pytest.raises(UnityCliError, match=reason) as error:
        client.execute(project, request)
    assert error.value.indeterminate
    if case == "editor_failed":
        assert "Editor diagnostic" in str(error.value)
        assert error.value.response["errors"][0]["code"] == "COMMAND_FAILED"
    assert len(calls()) == 2
