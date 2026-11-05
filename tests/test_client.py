"""验证独立 JSONL 会话、可执行文件发现及失败证据。"""
import io
import json
import queue
from types import SimpleNamespace

import pytest
from smartui_mcp.client import (PhotoCraftClient, PhotoCraftSession, PhotoCraftError,
    PhotoCraftProtocolError, PhotoCraftRemoteError, PhotoCraftTimeoutError, SUPPORTED_VERSION)

class Process:
    def __init__(self, replies):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(json.dumps(reply) + "\n" for reply in replies))
    def wait(self, timeout):
        return 0

@pytest.fixture
def fake_session(tmp_path, monkeypatch):
    sessions = []
    def create(replies):
        process = Process(replies)
        monkeypatch.setattr("smartui_mcp.client.subprocess.Popen", lambda *a, **k: process)
        session = PhotoCraftSession(SimpleNamespace(timeout=.02, cli_path=tmp_path / "cli.exe"), tmp_path, trace=[])
        sessions.append(session)
        return session, process
    yield create
    for session in sessions:
        session.close()


def test_sequential_ids_and_trace(fake_session):
    s, p = fake_session([{"id": 1, "ok": True, "result": {"a": 1}}, {"id": 2, "ok": True, "result": []}])
    assert s.call("doc.inspect") == {"a": 1}
    assert s.call("engine.commands", {"filter": "shape"}) == []
    requests = [json.loads(line) for line in p.stdin.getvalue().splitlines()]
    assert [r["id"] for r in requests] == [1, 2]
    assert s.trace[1]["response"]["result"] == []
    assert s.stderr_path.is_file()

@pytest.mark.parametrize("response", [
    {"id": 2, "ok": True, "result": {}}, {"id": True, "ok": True, "result": {}},
    {"id": 1, "ok": 1, "result": {}}, {"id": 1, "ok": True}])
def test_invalid_response_poisoned_without_retry(fake_session, response):
    s, p = fake_session([response])
    with pytest.raises(PhotoCraftProtocolError):
        s.call("doc.inspect")
    with pytest.raises(PhotoCraftError):
        s.call("doc.inspect")
    assert len(p.stdin.getvalue().splitlines()) == 1
    assert s.trace[0]["error"]

@pytest.mark.parametrize("response", [
    {"id": 1, "ok": False, "error": "missing resource"},
    {"id": 1, "ok": True, "result": {"warnings": ["missing font"]}},
    {"id": 1, "ok": True, "result": {"completed": 1, "failed": 1, "results": []}}])
def test_failures_preserve_whole_response(fake_session, response):
    s, p = fake_session([response])
    with pytest.raises(PhotoCraftRemoteError) as caught:
        s.call("batch")
    assert caught.value.response == response
    assert caught.value.request_id == 1
    assert caught.value.stderr_path == s.stderr_path
    assert len(p.stdin.getvalue().splitlines()) == 1


def test_timeout_retains_request_and_disables_reuse(fake_session):
    s, p = fake_session([])
    s._reader.join(timeout=1)
    s._replies = queue.Queue()
    with pytest.raises(PhotoCraftTimeoutError, match="未自动重试"):
        s.call("engine.execute", {"command": "shape.create"})
    with pytest.raises(PhotoCraftError):
        s.call("doc.inspect")
    assert len(p.stdin.getvalue().splitlines()) == 1
    assert s.trace[0]["request"]["method"] == "engine.execute"


def test_missing_executable_is_lazy_and_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTUI_PHOTOCRAFT_CLI", str(tmp_path / "absent.exe"))
    client = PhotoCraftClient()
    with pytest.raises(FileNotFoundError, match="不存在"):
        client.status()


def test_default_installed_path_and_hash(tmp_path, monkeypatch):
    monkeypatch.delenv("SMARTUI_PHOTOCRAFT_CLI", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "Programs/ArtCraft/PhotoCraft/photocraft-cli.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"installed")
    monkeypatch.setattr("smartui_mcp.client.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=SUPPORTED_VERSION + "\n", stderr=""))
    status = PhotoCraftClient().status()
    assert status["cli_path"] == str(exe.resolve())
    assert len(status["sha256"]) == 64
    monkeypatch.setattr("smartui_mcp.client.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="photocraft-cli 0.20.2", stderr=""))
    with pytest.raises(PhotoCraftError, match="不支持"):
        PhotoCraftClient().status()


def test_invalid_timeout_or_params_do_not_submit(fake_session):
    for timeout in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            PhotoCraftClient(timeout=timeout)
    s, p = fake_session([])
    with pytest.raises(ValueError):
        s.call("edit", {"x": float("nan")})
    with pytest.raises(ValueError):
        s.call("edit", [])
    assert p.stdin.getvalue() == ""
