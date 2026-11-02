"""验证已发现的绘制协议回归，不依赖 Krita 或 Qt 安装。"""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


class QPoint:
    """模拟安装版 paintLine 要求的整数坐标类型。"""

    def __init__(self, x, y):
        if type(x) is not int or type(y) is not int:
            raise TypeError("QPoint 坐标必须为整数")
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class QPainterPath:
    def __init__(self):
        self.commands = []

    def moveTo(self, x, y):
        self.commands.append(("M", x, y))

    def lineTo(self, x, y):
        self.commands.append(("L", x, y))

    def cubicTo(self, *coordinates):
        self.commands.append(("C", *coordinates))

    def quadTo(self, *coordinates):
        self.commands.append(("Q", *coordinates))

    def closeSubpath(self):
        self.commands.append(("Z",))


class Group:
    def __init__(self, name, children=()):
        self._name = name
        self._children = list(children)

    def name(self):
        return self._name

    def setName(self, name):
        self._name = name

    def type(self):
        return "grouplayer"

    def childNodes(self):
        return self._children

    def setAlphaLocked(self, value):
        # Krita 的分组不支持 Alpha 锁定，调用不会改变该状态。
        pass

    def alphaLocked(self):
        return False


class Document:
    def __init__(self, filename, root=None):
        self.filename = str(filename)
        self.root = root
        self.modified = False

    def fileName(self):
        return self.filename

    def rootNode(self):
        return self.root

    def waitForDone(self):
        pass

    def setModified(self, value):
        self.modified = value


@pytest.fixture
def engine(monkeypatch, tmp_path):
    app = SimpleNamespace(opened=[])
    app.documents = lambda: list(app.opened)
    krita = ModuleType("krita")
    krita.Krita = SimpleNamespace(instance=lambda: app)
    krita.ManagedColor = krita.Selection = krita.InfoObject = object
    qtcore = ModuleType("PyQt5.QtCore")
    qtcore.QByteArray = bytes
    qtcore.QPoint = QPoint
    qtcore.QPointF = qtcore.QRectF = object
    qtgui = ModuleType("PyQt5.QtGui")
    qtgui.QColor = object
    qtgui.QPainterPath = QPainterPath
    pyqt = ModuleType("PyQt5")
    pyqt.QtCore, pyqt.QtGui = qtcore, qtgui
    for name, module in {"krita": krita, "PyQt5": pyqt, "PyQt5.QtCore": qtcore, "PyQt5.QtGui": qtgui}.items():
        monkeypatch.setitem(sys.modules, name, module)

    # 直接加载文件，避免导入包时注册或启动 Krita 扩展。
    source = Path(__file__).resolve().parents[1] / "krita_plugin" / "smartui_bridge" / "engine.py"
    spec = importlib.util.spec_from_file_location("_smartui_engine_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DrawingEngine(tmp_path)


@pytest.mark.parametrize("image_first", [True, False])
def test_open_image_does_not_prevent_finding_kra(engine, tmp_path, image_first):
    kra = Document(tmp_path / "drawing.kra")
    image = Document(tmp_path / "reference.png")
    engine.app.opened = [image, kra] if image_first else [kra, image]

    assert engine.document(kra.fileName()) is kra


def test_gui_save_as_stops_old_path_from_addressing_document(engine, tmp_path):
    old_path, new_path = tmp_path / "original.kra", tmp_path / "renamed.kra"
    document = Document(old_path)
    engine.app.opened = [document]
    assert engine.document(str(old_path)) is document

    document.filename = str(new_path)

    with pytest.raises(ValueError, match="文档未打开"):
        engine.document(str(old_path))
    assert engine.document(str(new_path)) is document


def test_unsupported_group_alpha_lock_is_rejected_before_name_changes(engine, tmp_path):
    group = Group("Original")
    document = Document(tmp_path / "drawing.kra", Group("root", [group]))
    engine.app.opened = [document]

    with pytest.raises(ValueError, match="alpha_locked"):
        engine.execute({
            "op": "layer.update", "document": document.fileName(), "node": "name:Original",
            "properties": {"name": "Changed", "alpha_locked": True},
        })

    assert group.name() == "Original"


def test_line_prepares_integer_qpoints_and_preserves_pressure(engine):
    method, arguments = engine.prepare_stroke({
        "kind": "line", "from": [-2, 3], "to": [11, 20],
        "pressure_from": 0.25, "pressure_to": 0.75,
    })

    assert method == "paintLine"
    assert all(isinstance(point, QPoint) for point in arguments[:2])
    assert [(point.x(), point.y()) for point in arguments[:2]] == [(-2, 3), (11, 20)]
    assert arguments[2:4] == [0.25, 0.75]


@pytest.mark.parametrize("coordinates", [[0.5, 1], [0, 1.5], [0.0, 1]])
def test_line_rejects_float_coordinates_with_explicit_guidance(engine, coordinates):
    with pytest.raises(ValueError, match="paintLine.*整数"):
        engine.prepare_stroke({"kind": "line", "from": coordinates, "to": [10, 20]})


@pytest.mark.parametrize("commands", [None, [], [[]], [None], ["M"], [["L", 0, 0]]])
def test_path_requires_a_well_formed_initial_move(engine, commands):
    with pytest.raises(ValueError, match="路径.*M"):
        engine.prepare_stroke({"kind": "path", "commands": commands})


@pytest.mark.parametrize("command", [[], None, ["X", 1, 2], ["L", 1], ["L", "bad", 2]])
def test_malformed_path_command_reports_a_parameter_error(engine, command):
    with pytest.raises(ValueError, match=r"命令|path\.L"):
        engine.prepare_stroke({"kind": "path", "commands": [["M", 0, 0], command]})


def test_path_accepts_fractional_coordinates_as_line_alternative(engine):
    method, arguments = engine.prepare_stroke({
        "kind": "path", "commands": [["M", 0.5, 1.25], ["L", 10.75, 20.5]],
    })

    assert method == "paintPath"
    assert arguments[0].commands == [("M", 0.5, 1.25), ("L", 10.75, 20.5)]
