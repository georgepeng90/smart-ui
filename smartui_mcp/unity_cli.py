"""通过 Unity CLI 调用目标工程中已注册的 SmartUI Editor 命令。"""

import json
from pathlib import Path
import subprocess


COMMAND_ID = "smartui.execute"
COMMAND_GROUP, COMMAND_NAME = COMMAND_ID.split(".")
REQUEST_PARAMETER = "request_path"


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
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("Unity CLI 超时秒数必须是正整数")
        self.timeout_seconds = timeout_seconds

    def inspect(self, project_root):
        project = Path(project_root).resolve()
        if not project.is_dir():
            raise UnityCliError(f"Unity 工程目录不存在：{project}")
        data = self._run(project, "--detail", "full")
        commands = data.get("commands") if isinstance(data, dict) else None
        if not isinstance(commands, list) or any(not isinstance(row, dict) for row in commands):
            raise UnityCliError(f"Unity CLI 命令目录缺少有效的 data.commands：{project}")
        matches = [row for row in commands if row.get("group") == COMMAND_GROUP and row.get("name") == COMMAND_NAME]
        if len(matches) > 1:
            raise UnityCliError(f"Unity CLI 命令目录重复登记了 {COMMAND_ID}：{project}")
        registration = matches[0] if matches else None
        if registration is not None:
            parameters = registration.get("parameters")
            if (
                not isinstance(parameters, list)
                or len(parameters) != 1
                or not isinstance(parameters[0], dict)
                or parameters[0].get("name") != REQUEST_PARAMETER
                or parameters[0].get("type") != "String"
                or parameters[0].get("required") is not True
            ):
                raise UnityCliError(f"Unity CLI 命令 {COMMAND_ID} 必须登记一个必填字符串参数 {REQUEST_PARAMETER}：{project}")
        return {
            "project_path": str(project),
            "command": COMMAND_ID,
            "available": registration is not None,
            "registration": registration,
        }

    def execute(self, project_root, request_path):
        project = Path(project_root).resolve()
        request = Path(request_path).resolve()
        if not request.is_relative_to(project) or not request.is_file():
            raise UnityCliError(f"Unity 执行请求必须是工程目录 {project} 下的现有文件：{request}")
        if not self.inspect(project)["available"]:
            raise UnityCliError(f"目标 Unity Editor 未登记命令 {COMMAND_ID}：{project}")
        return self._run(project, COMMAND_GROUP, COMMAND_NAME, f"--{REQUEST_PARAMETER}", str(request), executing=True)

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
        if executing and isinstance(response, dict) and response.keys() == {"request_id", "result_path", "status"}:
            # 支持由 Editor 直接格式化的单个回执。构建结果仍由调用方核对结果文件。
            if all(isinstance(value, str) and value for value in response.values()):
                return response
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
