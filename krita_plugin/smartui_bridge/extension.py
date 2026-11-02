"""文件队列只负责通信；所有 Krita API 调用都在 GUI 主线程执行。"""

import json
import os
from pathlib import Path
import time
import traceback

from krita import Extension, Krita
from PyQt5.QtCore import QTimer

from .engine import DrawingEngine, BatchError


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class SmartUIExtension(Extension):
    def setup(self):
        configured = os.environ.get("SMARTUI_BRIDGE_DIR")
        self.directory = Path(configured) if configured else Path(os.environ["LOCALAPPDATA"]) / "SmartUI" / "bridge"
        for name in ("requests", "processing", "responses", "archive", "previews"):
            (self.directory / name).mkdir(parents=True, exist_ok=True)
        self.engine = DrawingEngine(self.directory)
        self.busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        write_json(self.directory / "status.json", {
            "pid": os.getpid(), "started_at": time.time(),
            "krita_version": Krita.instance().version(), "protocol_version": 1,
        })

    def createActions(self, window):
        pass

    def poll(self):
        if self.busy or Krita.instance().activeWindow() is None:
            return
        pending = sorted((self.directory / "requests").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        if not pending:
            return
        self.busy = True
        source = pending[0]
        claimed = self.directory / "processing" / source.name
        try:
            source.replace(claimed)
            request = json.loads(claimed.read_text(encoding="utf-8"))
            if request.get("id") != source.stem:
                raise ValueError("请求 id 必须与文件名一致")
            result = self.engine.dispatch(request["method"], request.get("params", {}))
            response = {"id": source.stem, "ok": True, "result": result}
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            if isinstance(exc, BatchError):
                error.update(operation_index=exc.index, completed=exc.completed)
            response = {"id": source.stem, "ok": False, "error": error}
            with (self.directory / "errors.log").open("a", encoding="utf-8") as log:
                log.write("\n请求 " + source.stem + "\n" + traceback.format_exc())
        finally:
            self.busy = False
        write_json(self.directory / "responses" / source.name, response)
        if claimed.exists():
            claimed.replace(self.directory / "archive" / source.name)
