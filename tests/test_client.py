"""验证独立 JSONL 会话、可执行文件发现及失败证据。"""
import hashlib
import io
import json
import queue
from types import SimpleNamespace

import pytest
from smartui_mcp.client import (DRAW_COMMANDS, DRAW_METHODS, PREVIEW_METHODS, VERIFIED_VERSION,
    PhotoCraftCapabilityError, PhotoCraftClient, PhotoCraftSession, PhotoCraftError,
    PhotoCraftProtocolError, PhotoCraftRemoteError, PhotoCraftTimeoutError)

class Input(io.StringIO):
    def close(self):
        if not self.closed:
            self.sent = self.getvalue()
        super().close()


class Process:
    def __init__(self, replies):
        self.stdin = Input()
        self.stdout = io.StringIO("".join(json.dumps(reply) + "\n" for reply in replies))
    def wait(self, timeout):
        return 0

    @property
    def requests(self):
        sent = self.stdin.sent if self.stdin.closed else self.stdin.getvalue()
        return [json.loads(line) for line in sent.splitlines()]

@pytest.fixture
def fake_session(tmp_path, monkeypatch):
    sessions = []
    def create(replies):
        process = Process(replies)
        monkeypatch.setattr("smartui_mcp.client.subprocess.Popen", lambda *a, **k: process)
        session = PhotoCraftSession({"cli_path": str(tmp_path / "cli.exe")}, tmp_path, .02)
        session.trace = []
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


def success(request_id, result):
    return {"id": request_id, "ok": True, "result": result}


def registry_replies(*, missing_methods=(), missing_commands=()):
    return [
        success(1, sorted((DRAW_METHODS | {"methods"}) - set(missing_methods))),
        success(2, [{"id": command, "enabled": False, "needsDocument": True}
                    for command in sorted(DRAW_COMMANDS - set(missing_commands))]),
    ]


@pytest.fixture
def installed_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("SMARTUI_PHOTOCRAFT_CLI", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "Programs/ArtCraft/PhotoCraft/photocraft-cli.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"installed")
    return exe


def use_cli(monkeypatch, exe, version, replies):
    def version_response(args, **kwargs):
        assert args == [str(exe.resolve()), "--version"]
        return SimpleNamespace(returncode=0, stdout=version + "\n", stderr="")

    process = Process(replies)
    monkeypatch.setattr("smartui_mcp.client.subprocess.run", version_response)
    monkeypatch.setattr("smartui_mcp.client.subprocess.Popen", lambda *a, **k: process)
    return process


@pytest.mark.parametrize("version", [VERIFIED_VERSION, "photocraft-cli 0.20.2 (future, 2026-11-04)"])
def test_unverified_build_can_draw_when_capabilities_are_present(tmp_path, monkeypatch, installed_cli, version):
    exe = installed_cli
    process = use_cli(monkeypatch, exe, version, registry_replies())
    client = PhotoCraftClient()
    status = client.status()
    assert status["cli_path"] == str(exe.resolve())
    assert status["version"] == version
    assert status["sha256"] == hashlib.sha256(b"installed").hexdigest()
    assert status["build_verification"] == "unverified"
    assert status["missing_capabilities"] == []
    assert [r["method"] for r in process.requests] == ["methods", "engine.commands"]
    process = use_cli(monkeypatch, exe, version, registry_replies() + [success(3, {"width": 8})])
    trace = []
    with client.session(tmp_path, trace=trace) as session:
        assert session.backend == status
        assert session.call("doc.new", {"width": 8, "height": 8}) == {"width": 8}
    assert [r["method"] for r in process.requests] == ["methods", "engine.commands", "doc.new"]
    assert [r["request"]["method"] for r in trace] == ["doc.new"]


def test_missing_drawing_capabilities_reported_and_block_before_writes(tmp_path, monkeypatch, installed_cli):
    replies = registry_replies(missing_methods=["doc.save"], missing_commands=["shape.edit"])
    use_cli(monkeypatch, installed_cli, VERIFIED_VERSION, replies)
    client = PhotoCraftClient()
    status = client.status()
    assert status["missing_capabilities"] == ["method:doc.save", "command:shape.edit"]
    process = use_cli(monkeypatch, installed_cli, VERIFIED_VERSION, replies)
    with pytest.raises(PhotoCraftCapabilityError, match="缺少必需能力.*doc.save.*shape.edit") as error:
        with client.session(tmp_path):
            pytest.fail("缺少制作能力不得开放会话")
    assert error.value.backend == status
    assert [r["method"] for r in process.requests] == ["methods", "engine.commands"]


@pytest.mark.parametrize("reply,probe", [
    ({"id": 1, "ok": False, "error": "unknown method"}, "methods"),
    (success(2, {"commands": [{"id": "shape.edit"}]}), "engine.commands"),
])
def test_unusable_probe_reports_which_interface_failed(tmp_path, monkeypatch, installed_cli, reply, probe):
    replies = [reply] if probe == "methods" else registry_replies()[:1] + [reply]
    process = use_cli(monkeypatch, installed_cli, VERIFIED_VERSION, replies)
    with pytest.raises(PhotoCraftCapabilityError, match="无法核验.*" + probe):
        with PhotoCraftClient().session(tmp_path):
            pytest.fail("查询失败不得开放会话")
    assert process.requests[-1]["method"] == probe


def test_preview_requires_only_open_and_render(tmp_path, monkeypatch, installed_cli):
    replies = [success(1, sorted(PREVIEW_METHODS | {"methods"})), success(2, {"width": 8})]
    process = use_cli(monkeypatch, installed_cli, VERIFIED_VERSION, replies)
    with PhotoCraftClient().session(tmp_path, preview_only=True) as session:
        assert session.call("doc.open", {"path": "preview.pcraft"}) == {"width": 8}
    assert [r["method"] for r in process.requests] == ["methods", "doc.open"]


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
