"""通过官方 Unity CLI 命令执行插件随附的预制体构建脚本。"""

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from .unity_script import SCRIPT_DIRECTORY, SCRIPT_ENTRY, SCRIPT_NAME, write_script


COMMAND_ID = "run_script"
COMMAND_PACKAGE = "Unity.Pipeline.Editor"
COMMAND_PARAMETERS = {
    "file": ("String", True),
    "entry": ("String", False),
    "args": ("JArray", False),
    "mode": ("String", False),
    "timeout_ms": ("Int32", False),
}


class UnityCliError(ValueError):
    """Unity CLI 调用失败；indeterminate 表示需要检查同一次执行的结果。"""

    def __init__(self, message, *, indeterminate=False, response=None):
        if indeterminate:
            message += "。执行结果未确定，请检查同一次请求的结果文件和目标资源，避免重复提交"
        super().__init__(message)
        self.indeterminate = indeterminate
        self.response = response


class UnityCliClient:
    def __init__(self, *, timeout_seconds=180):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
            raise ValueError("Unity CLI 超时秒数必须是 1 到 86400 之间的整数")
        self.timeout_seconds = timeout_seconds

    def inspect(self, project_root):
        project = Path(project_root).resolve()
        if not project.is_dir():
            raise UnityCliError(f"Unity 工程目录不存在：{project}")
        data = self._run(project, "--detail", "full")
        commands = data.get("commands") if isinstance(data, dict) else None
        if not isinstance(commands, list) or any(not isinstance(row, dict) for row in commands):
            raise UnityCliError(f"Unity CLI 命令目录缺少有效的 data.commands：{project}")
        matches = [
            row for row in commands
            if row.get("name") == COMMAND_ID and not row.get("group") and row.get("package") == COMMAND_PACKAGE
        ]
        if len(matches) > 1:
            raise UnityCliError(f"Unity CLI 命令目录重复登记了 {COMMAND_ID}：{project}")
        registration = matches[0] if matches else None
        if registration is not None:
            parameters = registration.get("parameters")
            if not isinstance(parameters, list) or any(not isinstance(row, dict) for row in parameters):
                raise UnityCliError(f"Unity CLI 命令 {COMMAND_ID} 缺少参数定义：{project}")
            for name, (kind, required) in COMMAND_PARAMETERS.items():
                matching = [row for row in parameters if row.get("name") == name]
                if len(matching) != 1 or matching[0].get("type") != kind or matching[0].get("required") is not required:
                    raise UnityCliError(f"Unity CLI 命令 {COMMAND_ID} 的 {name} 参数定义不匹配（要求 {kind}）：{project}")
        return {
            "project_path": str(project),
            "command": COMMAND_ID,
            "available": registration is not None,
            "registration": registration,
        }

    def execute(self, project_root, request_path):
        project = Path(project_root).resolve()
        request = Path(request_path).absolute()
        if not request.is_relative_to(project) or request.resolve() != request or not request.is_file():
            raise UnityCliError(f"Unity 执行请求必须是工程目录 {project} 下的现有文件：{request}")
        if not self.inspect(project)["available"]:
            raise UnityCliError(f"目标 Unity Editor 未登记官方命令 {COMMAND_ID}：{project}")
        script = SCRIPT_DIRECTORY / SCRIPT_NAME
        if script.is_file():
            return self._execute_script(project, request, script)
        dispatched = False
        try:
            with TemporaryDirectory(prefix="smartui-unity-") as directory:
                script = Path(directory) / SCRIPT_NAME
                write_script(script)
                dispatched = True
                return self._execute_script(project, request, script)
        except OSError as exc:
            raise UnityCliError(f"无法处理 SmartUI 构建脚本：{exc}", indeterminate=dispatched) from exc

    def _execute_script(self, project, request, script):
        if script.resolve().is_relative_to(project / "Assets"):
            raise UnityCliError(f"SmartUI 构建脚本必须在工程 Assets 目录之外：{script}")
        data = self._run(
            project, COMMAND_ID, "--file", str(script), "--entry", SCRIPT_ENTRY,
            "--args", json.dumps([str(request)]), "--mode", "ephemeral",
            "--timeout_ms", str(self.timeout_seconds * 1000), executing=True,
        )
        execution = data.get("result") if isinstance(data, dict) else None
        if isinstance(execution, dict) and execution.get("success") is False:
            raise UnityCliError(
                f"Unity CLI {COMMAND_ID} 执行失败（{project}）：{execution}",
                indeterminate=True, response=data,
            )
        receipt = execution.get("result") if isinstance(execution, dict) else None
        if (
            not isinstance(receipt, dict)
            or execution.get("success") is not True
            or receipt.keys() != {"request_id", "result_path", "status"}
            or any(not isinstance(value, str) or not value for value in receipt.values())
        ):
            raise UnityCliError(
                f"Unity CLI {COMMAND_ID} 缺少有效的 SmartUI 执行回执（{project}）",
                indeterminate=True, response=data,
            )
        return receipt

    def _run(self, project, *arguments, executing=False):
        argv = [
            "unity", "--format", "json", "--no-banner", "--non-interactive",
            "command", "--project-path", str(project), "--timeout", str(self.timeout_seconds),
            *arguments,
        ]
        operation = COMMAND_ID if executing else "命令发现"
        context = f"Unity CLI {operation}（{project}）"
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=self.timeout_seconds + 5,
            )
        except subprocess.TimeoutExpired as exc:
            raise UnityCliError(f"{context}等待超时", indeterminate=executing) from exc
        except OSError as exc:
            raise UnityCliError(f"{context}无法启动：{exc}") from exc

        try:
            response = json.loads(completed.stdout)
        except ValueError:
            response = None
        if completed.returncode != 0 or (isinstance(response, dict) and response.get("success") is False):
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            stdout = completed.stdout.decode("utf-8", errors="replace").strip()
            details = "\n".join(filter(None, (stdout, stderr)))
            raise UnityCliError(
                f"{context}失败（退出码 {completed.returncode}）：{details}",
                indeterminate=executing,
                response=response,
            )
        if not isinstance(response, dict) or response.get("success") is not True or "data" not in response:
            raise UnityCliError(f"{context}返回了无效的 Unity CLI JSON 响应", indeterminate=executing)
        data = response["data"]
        target = data.get("target") if isinstance(data, dict) else None
        if isinstance(target, dict) and "projectPath" in target:
            reported_path = target["projectPath"]
            if not isinstance(reported_path, str) or not Path(reported_path).is_absolute() or Path(reported_path).resolve() != project:
                raise UnityCliError(
                    f"{context}返回的工程路径不匹配：{reported_path!r}", indeterminate=executing, response=response,
                )
        return data
