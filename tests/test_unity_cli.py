"""用子进程替身检查 Unity CLI 参数与失败处理。"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from smartui_mcp.unity_cli import UnityCliClient, UnityCliError
from smartui_mcp.unity_script import SCRIPT_NAME, write_script


@pytest.fixture
def cli(tmp_path, monkeypatch):
    project = tmp_path / "含空格 Unity 工程 & UI"
    project.mkdir()
    request = project / "Assets" / "请求 文件" / "unity" / "operations" / ("a" * 32) / "request.json"
    request.parent.mkdir(parents=True)
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
        "name": "run_script", "package": "Unity.Pipeline.Editor",
        "parameters": [
            {"name": "file", "type": "String", "required": True},
            {"name": "entry", "type": "String", "required": False},
            {"name": "args", "type": "JArray", "required": False},
            {"name": "mode", "type": "String", "required": False},
            {"name": "timeout_ms", "type": "Int32", "required": False},
            {"name": "dry_run", "type": "Boolean", "required": False},
        ],
    }
    if case == "missing":
        registration = {"group": "smartui", "name": "execute", "parameters": []}
    if case == "non_official":
        registration["package"] = "Project.Editor"
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
    path = Path(args[args.index("--file") + 1])
    assert "class PrefabBuild" in path.read_text(encoding="utf-8")
    receipt = {"request_id": "a" * 32, "result_path": "result.json", "status": "success"}
    execution = {"success": True, "result": receipt, "diagnostics": []}
    if case == "inner_failure":
        execution = {"success": False, "errorDetails": "Compilation failed"}
    elif case == "missing_receipt":
        execution["result"] = None
    print(json.dumps({"success": True, "data": {"result": execution}}))
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
        if "--file" in argv:
            options["timeout"] = 1
        return original_run([sys.executable, str(script), str(log), *argv[1:]], **options)

    monkeypatch.setattr(subprocess, "run", run_fake)

    def calls():
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return UnityCliClient(), project, request, calls


def test_discovers_then_executes_fixed_command(cli):
    client, project, request, calls = cli
    assert client.execute(project, request) == {
        "request_id": "a" * 32, "result_path": "result.json", "status": "success",
    }
    discovery, execute = calls()
    common_args = [
        "--format", "json", "--no-banner", "--non-interactive",
        "command", "--project-path", str(project), "--timeout", "180",
    ]
    assert discovery == [*common_args, "--detail", "full"]
    script = Path(execute[execute.index("--file") + 1])
    assert script.is_absolute() and not script.is_relative_to(project)
    assert script.name == "SmartUIBuild.cs"
    assert execute == [
        *common_args, "run_script", "--file", str(script),
        "--entry", "SmartUI.UGUI.Editor.PrefabBuild.Execute", "--args", json.dumps([str(request)]),
        "--mode", "ephemeral", "--timeout_ms", "180000",
    ]


def test_dispatches_the_packaged_script_from_the_plugin(cli, monkeypatch, tmp_path):
    client, project, request, calls = cli
    plugin = tmp_path / "插件 安装目录"
    plugin.mkdir()
    script = plugin / SCRIPT_NAME
    write_script(script)
    monkeypatch.setattr("smartui_mcp.unity_cli.SCRIPT_DIRECTORY", plugin)

    assert client.execute(project, request)["status"] == "success"
    _, execute = calls()
    assert execute[execute.index("--file") + 1] == str(script)


@pytest.mark.parametrize("case", ["missing", "non_official"])
def test_inspection_reports_missing_registration(cli, monkeypatch, case):
    client, project, request, calls = cli
    monkeypatch.setenv("SMARTUI_TEST_CLI_CASE", case)
    assert client.inspect(project) == {
        "project_path": str(project), "command": "run_script",
        "available": False, "registration": None,
    }
    with pytest.raises(UnityCliError, match="未登记官方命令") as error:
        client.execute(project, request)
    assert not error.value.indeterminate
    assert all("--detail" in call for call in calls())


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("wrong_parameter", "file 参数定义不匹配"),
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
    [
        ("timeout", "等待超时"), ("editor_failed", "Editor timed out"), ("malformed", "无效的 Unity CLI JSON"),
        ("inner_failure", "Compilation failed"), ("missing_receipt", "缺少有效的 SmartUI 执行回执"),
    ],
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
