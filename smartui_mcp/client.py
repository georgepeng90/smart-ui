"""启动独立 PhotoCraft headless 会话；失败请求绝不自动重放。"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from uuid import uuid4

SUPPORTED_VERSION = "photocraft-cli 0.20.1 (327c4cc82, 2026-11-03)"

class PhotoCraftError(RuntimeError):
    """保留原生请求、响应和 stderr 证据的后端错误。"""
    def __init__(self, message, *, request_id=None, stderr_path=None, response=None):
        self.request_id = request_id
        self.stderr_path = stderr_path
        self.response = response
        super().__init__(f"{message}；request_id={request_id}；stderr={stderr_path}")

class PhotoCraftTimeoutError(PhotoCraftError):
    """结果不确定，禁止自动重新提交。"""

class PhotoCraftProtocolError(PhotoCraftError):
    """原生 JSONL 响应不符合协议。"""

class PhotoCraftRemoteError(PhotoCraftError):
    """原生命令、批次或必要资源验证失败。"""

class PhotoCraftClient:
    def __init__(self, cli_path=None, timeout=60):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("PhotoCraft timeout 必须是大于 0 的有限秒数")
        self.timeout = timeout
        self._configured_cli = cli_path

    @property
    def cli_path(self):
        configured = self._configured_cli if self._configured_cli is not None else os.environ.get("SMARTUI_PHOTOCRAFT_CLI")
        if configured is not None:
            if not str(configured).strip():
                raise ValueError("SMARTUI_PHOTOCRAFT_CLI 不能为空")
            candidate = Path(configured).expanduser().resolve()
            if not candidate.is_file():
                raise FileNotFoundError(f"PhotoCraft CLI 不存在：{candidate}")
        else:
            candidates = []
            for variable, suffix in (
                ("LOCALAPPDATA", "Programs/ArtCraft/PhotoCraft/photocraft-cli.exe"),
                ("ProgramFiles", "ArtCraft/PhotoCraft/photocraft-cli.exe"),
                ("ProgramFiles(x86)", "ArtCraft/PhotoCraft/photocraft-cli.exe"),
            ):
                if os.environ.get(variable):
                    candidates.append(Path(os.environ[variable]) / suffix)
            found = next((p for p in candidates if p.is_file()), None)
            found = found or shutil.which("photocraft-cli")
            if found is None:
                raise FileNotFoundError("未找到 PhotoCraft CLI；请安装受支持版本，或设置 SMARTUI_PHOTOCRAFT_CLI 为可执行文件绝对路径")
            candidate = Path(found).resolve()
        return candidate

    def status(self):
        cli_path = self.cli_path
        try:
            result = subprocess.run([str(cli_path), "--version"], capture_output=True,
                                    text=True, encoding="utf-8", timeout=self.timeout,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PhotoCraftError(f"无法读取 PhotoCraft 版本：{exc}") from exc
        if result.returncode:
            raise PhotoCraftError(f"PhotoCraft --version 失败：{result.stderr.strip()}")
        version = result.stdout.strip()
        if version != SUPPORTED_VERSION:
            raise PhotoCraftError(f"不支持的 PhotoCraft 构建：{version!r}；要求 {SUPPORTED_VERSION}")
        return {"cli_path": str(cli_path), "version": version,
                "sha256": hashlib.sha256(cli_path.read_bytes()).hexdigest()}

    @contextmanager
    def session(self, root, trace=None):
        """trace 为列表或 callback(entry)；每个请求记录响应或错误及耗时。"""
        self.status()
        session = PhotoCraftSession(self, root, trace)
        try:
            yield session
        finally:
            session.close()

class PhotoCraftSession:
    def __init__(self, client, root, trace=None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"PhotoCraft 会话根目录不是目录：{self.root}")
        if trace is not None and not isinstance(trace, list) and not callable(trace):
            raise ValueError("trace 必须是列表或 callback(entry)")
        self.trace = trace
        self.timeout = client.timeout
        self.stderr_path = self.root / f"photocraft-{uuid4().hex}.stderr.log"
        self._stderr = self.stderr_path.open("x", encoding="utf-8")
        self._lock = threading.Lock()
        self._sequence = 0
        self._closed = False
        self._failed = False
        env = dict(os.environ)
        for key in ("PHOTOCRAFT_CONTROL_PORT", "PHOTOCRAFT_CONTROL_TOKEN", "PHOTOCRAFT_CONTROL_TOKEN_FILE"):
            env.pop(key, None)
        try:
            self.process = subprocess.Popen(
                [str(client.cli_path), "serve", "--automation-read-root", str(self.root),
                 "--automation-write-root", str(self.root)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self._stderr, text=True, encoding="utf-8", env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._stderr.close()
            raise PhotoCraftError(f"无法启动 PhotoCraft：{exc}", stderr_path=self.stderr_path) from exc
        self._replies = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self._replies.put(line)
        except (OSError, UnicodeError) as exc:
            self._replies.put(exc)
        finally:
            self._replies.put(None)

    def _record(self, entry):
        if isinstance(self.trace, list):
            self.trace.append(entry)
        elif self.trace is not None:
            self.trace(entry)

    def call(self, method, params=None):
        if not isinstance(method, str) or not method.strip():
            raise ValueError("PhotoCraft method 必须是非空字符串")
        params = {} if params is None else params
        if not isinstance(params, dict):
            raise ValueError("PhotoCraft params 必须是 JSON 对象")
        json.dumps(params, allow_nan=False)
        with self._lock:
            if self._closed or self._failed:
                raise PhotoCraftError("PhotoCraft 会话已结束或结果不确定，请检查证据后创建新会话", stderr_path=self.stderr_path)
            self._sequence += 1
            request = {"id": self._sequence, "method": method, "params": params}
            entry = {"request": request}
            started = time.monotonic()
            error_args = {"request_id": request["id"], "stderr_path": self.stderr_path}
            try:
                self.process.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n")
                self.process.stdin.flush()
                try:
                    line = self._replies.get(timeout=self.timeout)
                except queue.Empty:
                    self._failed = True
                    raise PhotoCraftTimeoutError(f"PhotoCraft 超过 {self.timeout:g} 秒未回应；请求可能已经执行，未自动重试", **error_args) from None
                if line is None or isinstance(line, Exception):
                    self._failed = True
                    raise PhotoCraftProtocolError(f"PhotoCraft 连接中断：{line}；请求可能已经执行，未自动重试", **error_args)
                try:
                    response = json.loads(line)
                except ValueError as exc:
                    self._failed = True
                    raise PhotoCraftProtocolError(f"PhotoCraft 返回非法 JSON：{line!r}", **error_args) from exc
                entry["response"] = response
                if not isinstance(response, dict) or type(response.get("id")) is not int or response.get("id") != request["id"]:
                    self._failed = True
                    raise PhotoCraftProtocolError("PhotoCraft 响应 id 不匹配", response=response, **error_args)
                if response.get("ok") is False and "error" in response:
                    raise PhotoCraftRemoteError(f"PhotoCraft 执行失败：{response['error']}", response=response, **error_args)
                if response.get("ok") is not True or "result" not in response:
                    self._failed = True
                    raise PhotoCraftProtocolError("PhotoCraft 响应缺少有效 ok/result/error", response=response, **error_args)
                result = response["result"]
                if isinstance(result, dict) and (result.get("warnings") or result.get("failed", 0)):
                    raise PhotoCraftRemoteError("PhotoCraft 返回资源警告或批次失败：" + json.dumps(result, ensure_ascii=False), response=response, **error_args)
                return result
            except (OSError, UnicodeError) as exc:
                self._failed = True
                error = PhotoCraftProtocolError(f"PhotoCraft 传输失败：{exc}；未自动重试", **error_args)
                entry["error"] = str(error)
                raise error from exc
            except Exception as exc:
                entry["error"] = str(exc)
                raise
            finally:
                entry["elapsed_seconds"] = time.monotonic() - started
                self._record(entry)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self._reader.join(timeout=1)
        self.process.stdout.close()
        self._stderr.close()
