import concurrent.futures
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import UUID

from smartui_mcp.client import BridgeClient, BridgeProtocolError, BridgeRemoteError, BridgeTimeoutError


@contextmanager
def responding_bridge(root, handle):
    """模拟扩展原子发布响应；退出时传回后台线程错误。"""
    stop = threading.Event()
    errors = []

    def run():
        seen = set()
        try:
            while not stop.is_set():
                for path in (root / "requests").glob("*.json"):
                    if path.name in seen:
                        continue
                    seen.add(path.name)
                    request = json.loads(path.read_text(encoding="utf-8"))
                    response = handle(request)
                    response_dir = root / "responses"
                    response_dir.mkdir(parents=True, exist_ok=True)
                    temporary = response_dir / (path.stem + ".tmp")
                    temporary.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
                    os.replace(temporary, response_dir / path.name)
                stop.wait(0.002)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)
        if thread.is_alive():
            raise AssertionError("模拟 Krita 线程未结束")
        if errors:
            raise errors[0]


class BridgeClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.client = BridgeClient(self.root, timeout=1)

    def test_request_is_complete_before_atomic_publication(self):
        replace = os.replace
        publications = []

        def observe(source, destination):
            if Path(destination).parent.name == "requests":
                self.assertEqual(Path(source).suffix, ".tmp")
                self.assertFalse(Path(destination).exists())
                request = json.loads(Path(source).read_text(encoding="utf-8"))
                self.assertEqual(request["id"], Path(destination).stem)
                self.assertEqual(str(UUID(request["id"])), request["id"])
                publications.append(request)
            return replace(source, destination)

        with responding_bridge(self.root, lambda r: {"id": r["id"], "ok": True, "result": ["原生笔刷"]}):
            with patch("smartui_mcp.client.os.replace", side_effect=observe):
                self.assertEqual(self.client.call("brushes", {"query": "笔刷"}), ["原生笔刷"])
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0]["method"], "brushes")
        self.assertEqual(publications[0]["params"], {"query": "笔刷"})
        self.assertEqual(list((self.root / "requests").glob("*.tmp")), [])

    def test_remote_error_preserves_batch_details_and_result_file(self):
        details = {"type": "ValueError", "message": "缺少笔刷：测试", "operation_index": 2, "completed": [{"op": "layer.create", "node": "uuid"}]}
        with responding_bridge(self.root, lambda r: {"id": r["id"], "ok": False, "error": details}):
            with self.assertRaises(BridgeRemoteError) as caught:
                self.client.call("execute", {"operations": [{"op": "paint"}]})
        error = caught.exception
        self.assertEqual(error.error, details)
        self.assertTrue(error.response_path.exists())
        self.assertIn(error.request_id, str(error))
        self.assertIn(str(error.response_path), str(error))
        self.assertIn('"operation_index": 2', str(error))
        self.assertIn('"completed"', str(error))

    def test_timeout_submits_once_and_keeps_original_request(self):
        client = BridgeClient(self.root, timeout=0.02)
        with self.assertRaises(BridgeTimeoutError) as caught:
            client.call("execute", {"operations": [{"op": "document.save"}]})
        error = caught.exception
        requests = list((self.root / "requests").glob("*.json"))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].stem, error.request_id)
        self.assertEqual(error.response_path, self.root / "responses" / requests[0].name)
        self.assertIn("未自动重试", str(error))
        self.assertIn(str(error.response_path), str(error))

    def test_malformed_response_is_not_treated_as_success(self):
        invalid = [
            lambda r: {"id": "wrong", "ok": True, "result": {}},
            lambda r: {"id": r["id"], "ok": 1, "result": {}},
            lambda r: {"id": r["id"], "ok": True},
            lambda r: {"id": r["id"], "ok": False, "error": "失败"},
        ]
        for response in invalid:
            with self.subTest(response=response):
                with responding_bridge(self.root, response):
                    with self.assertRaises(BridgeProtocolError):
                        self.client.call("status")

    def test_concurrent_calls_use_distinct_requests(self):
        with responding_bridge(self.root, lambda r: {"id": r["id"], "ok": True, "result": r["params"]["number"]}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda n: self.client.call("inspect", {"number": n}), range(8)))
        self.assertEqual(results, list(range(8)))
        self.assertEqual(len(list((self.root / "requests").glob("*.json"))), 8)

    def test_invalid_timeout_and_json_do_not_submit(self):
        for timeout in [0, -1, float("nan"), float("inf")]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                BridgeClient(self.root, timeout=timeout)
        with self.assertRaises(ValueError):
            self.client.call("execute", {"opacity": float("nan")})
        self.assertFalse((self.root / "requests").exists())

    def test_environment_configuration(self):
        with patch.dict(os.environ, {"SMARTUI_BRIDGE_DIR": str(self.root), "SMARTUI_BRIDGE_TIMEOUT": "2.5"}):
            client = BridgeClient()
        self.assertEqual(client.bridge_dir, self.root)
        self.assertEqual(client.timeout, 2.5)
        with patch.dict(os.environ, {"SMARTUI_BRIDGE_TIMEOUT": "invalid"}):
            with self.assertRaisesRegex(ValueError, "SMARTUI_BRIDGE_TIMEOUT"):
                BridgeClient(self.root)


if __name__ == "__main__":
    unittest.main()
