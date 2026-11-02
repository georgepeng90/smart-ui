"""使用 Krita 原生绘画 API；不提供任意 Python 执行入口。"""

import hashlib
import math
from pathlib import Path
import re
import time
import uuid

from krita import Krita, ManagedColor, Selection, InfoObject
from PyQt5.QtCore import QByteArray, QPoint, QRectF
from PyQt5.QtGui import QColor, QPainterPath


OPERATIONS = {
    "document.create", "document.open", "document.save", "document.close",
    "layer.create", "layer.update", "layer.clear", "mask.create", "mask.update", "paint",
}


class BatchError(Exception):
    def __init__(self, index, operation, cause, completed):
        super().__init__("操作 {} ({}) 失败：{}".format(index, operation, cause))
        self.index = index
        self.completed = completed


def number(value, label, low=None, high=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + " 必须是有限数值")
    if integer and not isinstance(value, int):
        raise ValueError(label + " 必须是整数")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError("{} 超出允许范围 [{}, {}]".format(label, low, high))
    return value


def vector(value, length, label):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("{} 必须包含 {} 个数值".format(label, length))
    return [number(v, label) for v in value]


def fields(obj, required, optional=()):
    if not isinstance(obj, dict):
        raise ValueError("参数必须是 JSON 对象")
    missing = set(required) - set(obj)
    unknown = set(obj) - set(required) - set(optional)
    if missing or unknown:
        raise ValueError("参数错误：缺少 {}，未知 {}".format(sorted(missing), sorted(unknown)))


def kra_path(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or Path(value).suffix.lower() != ".kra":
        raise ValueError("文档必须是绝对 .kra 路径：{}".format(value))
    return str(Path(value).resolve())


class DrawingEngine:
    def __init__(self, directory):
        self.app = Krita.instance()
        self.directory = directory

    def dispatch(self, method, params):
        if method == "status":
            fields(params, [])
            from krita import Node
            return {
                "krita_version": self.app.version(), "protocol_version": 1,
                "operations": sorted(OPERATIONS),
                "native_brush_methods": [m for m in ("paintLine", "paintPath", "paintRectangle", "paintEllipse", "paintPolylineStroke") if hasattr(Node, m)],
                "documents": [d.fileName() for d in self.app.documents()],
            }
        if method == "brushes":
            fields(params, [], ["query"])
            query = params.get("query", "").casefold()
            return sorted(n for n in self.app.resources("preset") if query in n.casefold())
        if method in ("inspect", "preview"):
            fields(params, ["document"])
            doc = self.document(params["document"])
            doc.waitForDone()
            doc.refreshProjection()
            doc.waitForDone()
            if method == "inspect":
                return self.inspect(doc)
            path = self.directory / "previews" / (uuid.uuid4().hex + ".png")
            info = InfoObject()
            info.setProperty("alpha", True)
            old_batchmode = doc.batchmode()
            try:
                doc.setBatchmode(True)
                if not doc.exportImage(str(path), info):
                    raise RuntimeError("导出 PNG 失败：" + str(path))
            finally:
                doc.setBatchmode(old_batchmode)
            return {"path": str(path)}
        if method == "execute":
            fields(params, ["operations"])
            operations = params["operations"]
            if not isinstance(operations, list) or not operations:
                raise ValueError("operations 必须是非空数组")
            completed = []
            start = time.perf_counter()
            for index, operation in enumerate(operations):
                try:
                    result = self.execute(operation)
                    completed.append({"index": index, "op": operation["op"], "result": result})
                except Exception as exc:
                    raise BatchError(index, operation.get("op") if isinstance(operation, dict) else None, exc, completed) from exc
            return {"completed": completed, "elapsed_ms": round((time.perf_counter() - start) * 1000, 2)}
        raise ValueError("不支持的方法：" + str(method))

    def document(self, value):
        path = kra_path(value)
        opened = self.app.documents()
        matches = [d for d in opened if d.fileName() and str(Path(d.fileName()).resolve()).casefold() == path.casefold()]
        if len(matches) != 1:
            raise ValueError("文档未打开或不唯一，请先 document.open：" + path)
        return matches[0]

    def walk(self, node):
        yield node
        for child in node.childNodes():
            yield from self.walk(child)

    def node(self, doc, reference):
        if reference == "root":
            return doc.rootNode()
        if not isinstance(reference, str):
            raise ValueError("节点引用必须是 UUID 或 name:名称")
        nodes = list(self.walk(doc.rootNode()))
        if reference.startswith("name:"):
            matches = [n for n in nodes if n.name() == reference[5:]]
        else:
            matches = [n for n in nodes if n.uniqueId().toString() == reference]
        if len(matches) != 1:
            raise ValueError("节点引用不存在或不唯一：" + reference)
        return matches[0]

    def editable(self, node):
        current = node
        while current is not None:
            if current.locked():
                raise ValueError("节点或其父节点已锁定：" + current.name())
            current = current.parentNode()

    def node_info(self, node, doc):
        result = {
            "id": node.uniqueId().toString(), "name": node.name(), "type": node.type(),
            "visible": node.visible(), "locked": node.locked(), "opacity": node.opacity(),
            "blending_mode": node.blendingMode(), "inherit_alpha": node.inheritAlpha(),
            "alpha_locked": node.alphaLocked(),
            "children": [self.node_info(n, doc) for n in node.childNodes()],
        }
        if node.type() in ("paintlayer", "transparencymask"):
            pixels = bytes(node.pixelData(0, 0, doc.width(), doc.height()))
            result["pixel_sha256"] = hashlib.sha256(pixels).hexdigest()
        return result

    def inspect(self, doc):
        return {"path": doc.fileName(), "name": doc.name(), "width": doc.width(), "height": doc.height(),
                "modified": doc.modified(), "color_model": doc.colorModel(), "color_depth": doc.colorDepth(),
                "root": self.node_info(doc.rootNode(), doc)}

    def execute(self, op):
        if not isinstance(op, dict) or op.get("op") not in OPERATIONS:
            raise ValueError("不支持的操作：" + str(op))
        kind = op["op"]
        if kind in ("document.create", "document.open"):
            required = ["op", "path"] + (["width", "height", "name"] if kind == "document.create" else [])
            fields(op, required)
            path = kra_path(op["path"])
            if kind == "document.create":
                if Path(path).exists() or any(d.fileName() and str(Path(d.fileName()).resolve()).casefold() == path.casefold() for d in self.app.documents()):
                    raise ValueError("新建文档路径已存在：" + path)
                width = number(op["width"], "width", 1, 16384, True)
                height = number(op["height"], "height", 1, 16384, True)
                if not Path(path).parent.is_dir():
                    raise ValueError("文档父目录不存在：" + str(Path(path).parent))
                doc = self.app.createDocument(width, height, op["name"], "RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc", 96.0)
                if doc is None:
                    raise RuntimeError("Krita 无法创建文档：" + path)
                doc.setFileName(path)
                # Krita 默认建立白色背景；UI 图素从透明画布开始。
                for child in doc.rootNode().childNodes():
                    if not doc.rootNode().removeChildNode(child):
                        raise RuntimeError("无法移除新文档的默认背景")
            else:
                if not Path(path).is_file():
                    raise ValueError("源文件不存在：" + path)
                existing = [d for d in self.app.documents() if d.fileName() and str(Path(d.fileName()).resolve()).casefold() == path.casefold()]
                if existing:
                    raise ValueError("文档已经打开：" + path)
                doc = self.app.openDocument(path)
                if doc is None:
                    raise RuntimeError("Krita 无法打开文档：" + path)
            self.app.activeWindow().addView(doc)
            doc.waitForDone()
            return {"document": path, "root": doc.rootNode().uniqueId().toString()}

        if "document" not in op:
            raise ValueError("操作缺少 document")
        doc = self.document(op["document"])
        doc.waitForDone()
        if kind in ("document.save", "document.close"):
            fields(op, ["op", "document"])
            path = kra_path(op["document"])
            if kind == "document.save":
                doc.refreshProjection()
                doc.waitForDone()
                if not doc.saveAs(path):
                    raise RuntimeError("保存源文件失败：" + path)
                doc.waitForDone()
                return {"path": path, "bytes": Path(path).stat().st_size}
            if doc.modified():
                raise ValueError("文档仍有未保存修改，请先保存：" + path)
            if not doc.close():
                raise RuntimeError("关闭文档失败：" + path)
            return {"closed": path}

        if kind == "layer.create":
            fields(op, ["op", "document", "name", "kind"], ["parent"])
            if op["kind"] not in ("paintlayer", "grouplayer"):
                raise ValueError("kind 只允许 paintlayer 或 grouplayer")
            parent = self.node(doc, op.get("parent", "root"))
            if parent.type() != "grouplayer":
                raise ValueError("绘画图层和分组的父节点必须是分组")
            self.editable(parent)
            node = doc.createNode(op["name"], op["kind"])
            if node is None or not parent.addChildNode(node, None):
                raise RuntimeError("创建图层失败：" + op["name"])
        elif kind == "layer.update":
            fields(op, ["op", "document", "node", "properties"])
            node = self.node(doc, op["node"])
            props = op["properties"]
            setters = {"name": "setName", "visible": "setVisible", "locked": "setLocked", "opacity": "setOpacity", "blending_mode": "setBlendingMode", "inherit_alpha": "setInheritAlpha", "alpha_locked": "setAlphaLocked"}
            fields(props, [], setters)
            for key, value in props.items():
                if key == "opacity":
                    number(value, "opacity", 0, 255, True)
                elif key in ("visible", "locked", "inherit_alpha", "alpha_locked") and not isinstance(value, bool):
                    raise ValueError(key + " 必须为布尔值")
                elif key in ("name", "blending_mode") and (not isinstance(value, str) or not value):
                    raise ValueError(key + " 必须为非空字符串")
                if key == "blending_mode" and value not in ("normal", "multiply", "screen", "overlay"):
                    raise ValueError("首版 blending_mode 支持 normal / multiply / screen / overlay")
                if key == "alpha_locked" and node.type() != "paintlayer":
                    raise ValueError("alpha_locked 只适用于绘画图层")
            doc.setModified(True)
            for key, value in props.items():
                getattr(node, setters[key])(value)
                getter = {"blending_mode": "blendingMode", "inherit_alpha": "inheritAlpha", "alpha_locked": "alphaLocked"}.get(key, key)
                if getattr(node, getter)() != value:
                    raise ValueError("Krita 未接受属性 {}={}".format(key, value))
        elif kind == "layer.clear":
            fields(op, ["op", "document", "node"])
            node = self.node(doc, op["node"])
            self.editable(node)
            if node.type() != "paintlayer" or node.colorModel() != "RGBA" or node.colorDepth() != "U8":
                raise ValueError("清空操作只支持 RGBA/U8 绘画图层")
            bounds = node.bounds()
            if not bounds.isEmpty():
                if not node.setPixelData(QByteArray(bytes(bounds.width() * bounds.height() * 4)), bounds.x(), bounds.y(), bounds.width(), bounds.height()):
                    raise RuntimeError("清空图层失败：" + node.name())
        elif kind in ("mask.create", "mask.update"):
            required = ["op", "document", "rects"] + (["parent", "name"] if kind == "mask.create" else ["node"])
            fields(op, required)
            selection = self.selection(op["rects"])
            if kind == "mask.create":
                parent = self.node(doc, op["parent"])
                self.editable(parent)
                if parent.type() not in ("paintlayer", "grouplayer") or parent == doc.rootNode():
                    raise ValueError("透明度蒙版必须挂在普通图层或分组下")
                node = doc.createTransparencyMask(op["name"])
                node.setSelection(selection)
                if not parent.addChildNode(node, None):
                    raise RuntimeError("添加透明度蒙版失败")
            else:
                node = self.node(doc, op["node"])
                self.editable(node)
                if node.type() != "transparencymask":
                    raise ValueError("目标节点不是透明度蒙版")
                node.setSelection(selection)
        elif kind == "paint":
            return self.paint(doc, op)
        else:
            raise ValueError("没有实现的操作：" + kind)
        doc.setModified(True)
        doc.refreshProjection()
        doc.waitForDone()
        return {"node": node.uniqueId().toString(), "name": node.name(), "type": node.type()}

    def selection(self, rects):
        if not isinstance(rects, list):
            raise ValueError("rects 必须是数组；空数组表示全透明")
        selection = Selection()
        for rect in rects:
            values = vector(rect, 5, "mask.rect")
            for i, value in enumerate(values):
                number(value, "mask.rect", 1 if i in (2, 3) else (0 if i == 4 else None), 255 if i == 4 else None, True)
            selection.select(*values)
        return selection

    def paint(self, doc, op):
        fields(op, ["op", "document", "node", "brush", "color", "size", "strokes"], ["opacity"])
        node = self.node(doc, op["node"])
        self.editable(node)
        if node.type() != "paintlayer" or node.paintAbility() != "PAINT":
            raise ValueError("目标不是可绘制的绘画图层：" + node.name())
        presets = self.app.resources("preset")
        if op["brush"] not in presets:
            raise ValueError("找不到原生笔刷预设：" + str(op["brush"]))
        if not isinstance(op["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", op["color"]):
            raise ValueError("color 必须使用 #RRGGBB")
        size = number(op["size"], "size", 0.01, 10000)
        opacity = number(op.get("opacity", 1.0), "opacity", 0, 1)
        strokes = op["strokes"]
        if not isinstance(strokes, list) or not strokes:
            raise ValueError("strokes 必须是非空数组")
        prepared = [self.prepare_stroke(s) for s in strokes]
        window = self.app.activeWindow()
        views = [v for v in window.views() if v.document() == doc]
        if not views:
            raise ValueError("目标文档在当前窗口没有视图")
        view = views[0]
        window.showView(view)
        doc.setActiveNode(node)
        if doc.selection() is not None:
            raise ValueError("文档存在选区，会裁剪笔触；请先在 Krita 取消选区，再使用图层或透明度蒙版限定绘制区域")
        for action_id in ("hmirror_action", "vmirror_action"):
            action = self.app.action(action_id)
            if action is None:
                raise RuntimeError("Krita 缺少镜像状态接口：" + action_id)
            if action.isChecked():
                raise ValueError("Krita 镜像绘制已开启，请先关闭：" + action_id)
        previous = (view.currentBrushPreset(), view.foregroundColor(), view.brushSize(), view.paintingOpacity(), view.paintingFlow(), view.currentBlendingMode(), view.eraserMode(), view.globalAlphaLock(), view.disablePressure())
        try:
            view.setCurrentBrushPreset(presets[op["brush"]])
            view.setForeGroundColor(ManagedColor.fromQColor(QColor(op["color"]), view.canvas()))
            view.setBrushSize(size)
            view.setPaintingOpacity(opacity)
            view.setPaintingFlow(1.0)
            view.setCurrentBlendingMode("normal")
            view.setEraserMode(False)
            view.setGlobalAlphaLock(False)
            view.setDisablePressure(False)
            if view.currentBrushPreset().name() != op["brush"]:
                raise RuntimeError("Krita 未能激活指定笔刷")
            start = time.perf_counter()
            doc.setModified(True)
            for method, args in prepared:
                getattr(node, method)(*args)
            doc.waitForDone()
            doc.refreshProjection()
            doc.waitForDone()
            doc.setModified(True)
            return {"node": node.uniqueId().toString(), "brush": op["brush"], "stroke_count": len(strokes),
                    "native_methods": sorted(set(m for m, _ in prepared)), "elapsed_ms": round((time.perf_counter() - start) * 1000, 2)}
        finally:
            view.setCurrentBrushPreset(previous[0])
            view.setForeGroundColor(previous[1])
            view.setBrushSize(previous[2])
            view.setPaintingOpacity(previous[3])
            view.setPaintingFlow(previous[4])
            view.setCurrentBlendingMode(previous[5])
            view.setEraserMode(previous[6])
            view.setGlobalAlphaLock(previous[7])
            view.setDisablePressure(previous[8])

    def prepare_stroke(self, stroke):
        if not isinstance(stroke, dict):
            raise ValueError("笔触必须为对象")
        kind = stroke.get("kind")
        if kind == "line":
            fields(stroke, ["kind", "from", "to"], ["pressure_from", "pressure_to"])
            start = vector(stroke["from"], 2, "from")
            end = vector(stroke["to"], 2, "to")
            for value in start + end:
                number(value, "Krita 5.3 paintLine 坐标（小数坐标请使用 path）", integer=True)
            return ("paintLine", [QPoint(*start), QPoint(*end),
                                  number(stroke.get("pressure_from", 1.0), "pressure_from", 0, 1), number(stroke.get("pressure_to", 1.0), "pressure_to", 0, 1), "ForegroundColor"])
        if kind not in ("path", "rectangle", "ellipse"):
            raise ValueError("不支持的笔触 kind：" + str(kind))
        fields(stroke, ["kind", "commands" if kind == "path" else "rect"], ["fill"])
        fill = stroke.get("fill", "none")
        if fill not in ("none", "foreground"):
            raise ValueError("fill 只允许 none 或 foreground")
        fill_style = "None" if fill == "none" else "ForegroundColor"
        if kind in ("rectangle", "ellipse"):
            rect = vector(stroke["rect"], 4, "rect")
            number(rect[2], "rect.width", 0.01)
            number(rect[3], "rect.height", 0.01)
            return ("paintRectangle" if kind == "rectangle" else "paintEllipse", [QRectF(*rect), "ForegroundColor", fill_style])
        commands = stroke["commands"]
        if not isinstance(commands, list) or not commands or not isinstance(commands[0], list) or not commands[0] or commands[0][0] != "M":
            raise ValueError("路径必须从 M 命令开始")
        path = QPainterPath()
        methods = {"M": ("moveTo", 2), "L": ("lineTo", 2), "C": ("cubicTo", 6), "Q": ("quadTo", 4), "Z": ("closeSubpath", 0)}
        for command in commands:
            if not isinstance(command, list) or not command or command[0] not in methods:
                raise ValueError("无效的路径命令：" + str(command))
            method, count = methods[command[0]]
            getattr(path, method)(*vector(command[1:], count, "path." + command[0]))
        return ("paintPath", [path, "ForegroundColor", fill_style])
