"""通过原子文件提交连接 Krita，任何失败都不自动重试。"""

import json
import math
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4


class BridgeError(RuntimeError):
    """保留请求位置，便于确认超时后 Krita 是否已经执行。"""

    def __init__(self, message: str, request_id: str, response_path: Path):
        self.request_id = request_id
        self.response_path = response_path
        super().__init__(f"{message}；request_id={request_id}；结果路径={response_path}")


class BridgeTimeoutError(BridgeError):
    """等待结果超时；原请求继续保留，调用方不应盲目重新提交。"""


class BridgeProtocolError(BridgeError):
    """扩展返回了不符合约定的结果。"""


class BridgeRemoteError(BridgeError):
    """完整保留 Krita 的错误和批量操作完成记录。"""

    def __init__(self, error: dict[str, Any], request_id: str, response_path: Path):
        self.error = error
        super().__init__(
            "Krita 执行失败：" + json.dumps(error, ensure_ascii=False),
            request_id,
            response_path,
        )


class BridgeClient:
    def __init__(self, bridge_dir: str | Path | None = None, timeout: float | None = None):
        if bridge_dir is None:
            bridge_dir = os.environ.get("SMARTUI_BRIDGE_DIR")
        if bridge_dir is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                raise ValueError("缺少 LOCALAPPDATA，请设置 SMARTUI_BRIDGE_DIR 指定队列目录")
            bridge_dir = Path(local_app_data) / "SmartUI" / "bridge"
        if not str(bridge_dir).strip():
            raise ValueError("SMARTUI_BRIDGE_DIR 不能为空")
        self.bridge_dir = Path(bridge_dir).resolve()
        if timeout is None:
            configured_timeout = os.environ.get("SMARTUI_BRIDGE_TIMEOUT", "120")
            try:
                timeout = float(configured_timeout)
            except ValueError as error:
                raise ValueError(f"SMARTUI_BRIDGE_TIMEOUT 必须是秒数：{configured_timeout}") from error
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("SMARTUI_BRIDGE_TIMEOUT 必须是大于 0 的有限秒数")
        self.timeout = timeout

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not isinstance(method, str) or not method.strip():
            raise ValueError("method 必须是非空字符串")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("params 必须是 JSON 对象")

        request_id = str(uuid4())
        request_dir = self.bridge_dir / "requests"
        response_path = self.bridge_dir / "responses" / f"{request_id}.json"
        request_path = request_dir / f"{request_id}.json"
        temporary_path = request_dir / f"{request_id}.tmp"
        # 序列化先于文件写入，非法 JSON 不会进入 Krita 队列。
        payload = json.dumps(
            {"id": request_id, "method": method, "params": params},
            ensure_ascii=False,
            allow_nan=False,
        )
        try:
            request_dir.mkdir(parents=True, exist_ok=True)
            response_path.parent.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # 同一目录内发布完整请求，扩展只读取 .json 文件。
            os.replace(temporary_path, request_path)
        except OSError as error:
            raise BridgeError(f"无法提交 Krita 请求：{error}", request_id, response_path) from error

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                with response_path.open("r", encoding="utf-8") as stream:
                    response = json.load(stream)
            except FileNotFoundError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeTimeoutError(
                        f"等待 Krita 结果超过 {self.timeout:g} 秒；请求可能仍在执行，未自动重试",
                        request_id,
                        response_path,
                    ) from None
                time.sleep(min(0.05, remaining))
                continue
            except (OSError, ValueError) as error:
                raise BridgeProtocolError(
                    f"无法读取 Krita 结果：{error}", request_id, response_path
                ) from error
            return self._read_response(response, request_id, response_path)

    @staticmethod
    def _read_response(response: Any, request_id: str, response_path: Path) -> Any:
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise BridgeProtocolError("Krita 结果的 id 与请求不一致", request_id, response_path)
        if response.get("ok") is True and "result" in response:
            return response["result"]
        if response.get("ok") is False:
            error = response.get("error")
            if isinstance(error, dict) and isinstance(error.get("type"), str) and isinstance(error.get("message"), str):
                raise BridgeRemoteError(error, request_id, response_path)
        raise BridgeProtocolError("Krita 结果缺少有效的 ok、result 或 error 字段", request_id, response_path)
