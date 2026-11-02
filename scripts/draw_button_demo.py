"""用原生笔刷绘制分层按钮，并模拟一次只修改高光的反馈。"""

from datetime import datetime
import json
from pathlib import Path
import random
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from smartui_mcp import BridgeClient


def rounded(x, y, w, h, r):
    return [["M", x + r, y], ["L", x + w - r, y], ["Q", x + w, y, x + w, y + r],
            ["L", x + w, y + h - r], ["Q", x + w, y + h, x + w - r, y + h],
            ["L", x + r, y + h], ["Q", x, y + h, x, y + h - r],
            ["L", x, y + r], ["Q", x, y, x + r, y], ["Z"]]


def main():
    directory = ROOT / "artifacts" / ("button-demo-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    directory.mkdir(parents=True, exist_ok=False)
    path = str(directory / "button.kra")
    client = BridgeClient()
    log = []

    def call(method, params):
        result = client.call(method, params)
        log.append({"method": method, "params": params, "result": result})
        (directory / "operations.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def layer(name):
        return {"op": "layer.create", "document": path, "name": name, "kind": "paintlayer", "parent": "name:按钮组件"}

    def paint(name, color, size, strokes, brush="b) Basic-5 Size", opacity=1):
        return {"op": "paint", "document": path, "node": "name:" + name, "brush": brush,
                "color": color, "size": size, "opacity": opacity, "strokes": strokes}

    def shape(name, color, x, y, w, h, r):
        return paint(name, color, 1, [{"kind": "path", "commands": rounded(x, y, w, h, r), "fill": "foreground"}])

    layers = ["投影", "深色外轮廓", "金属边框", "内侧暗边", "按钮表面", "笔刷纹理", "金属亮边", "高光", "装饰铆钉"]
    operations = [{"op": "document.create", "path": path, "width": 512, "height": 256, "name": "原生笔刷按钮样例"},
                  {"op": "layer.create", "document": path, "name": "按钮组件", "kind": "grouplayer"}]
    operations += [layer(name) for name in layers]
    operations += [shape("投影", "#102229", 55, 77, 402, 119, 26),
                   shape("深色外轮廓", "#15272D", 53, 59, 406, 127, 27),
                   shape("金属边框", "#B18B4F", 57, 62, 398, 119, 25),
                   shape("内侧暗边", "#132E3A", 64, 70, 384, 102, 19),
                   shape("按钮表面", "#357783", 70, 76, 372, 91, 15)]
    operations.append({"op": "layer.update", "document": path, "node": "name:按钮表面", "properties": {"alpha_locked": True}})
    for y in range(77, 168, 3):
        t = (y - 77) / 91
        rgb = [round(a + (b - a) * t) for a, b in zip((67, 140, 144), (27, 66, 83))]
        color = "#" + "".join(format(v, "02x") for v in rgb)
        operations.append(paint("按钮表面", color, 5, [{"kind": "line", "from": [69, y], "to": [443, y]}]))
    rng = random.Random(30)
    texture = []
    for _ in range(90):
        x, y = rng.randint(82, 416), rng.randint(85, 155)
        texture.append({"kind": "line", "from": [x, y], "to": [x + rng.randint(4, 16), y - 2], "pressure_from": 0.3, "pressure_to": 0.6})
    operations.append(paint("笔刷纹理", "#C0C4A3", 10, texture, "h) Chalk Grainy", 0.10))
    operations.append({"op": "mask.create", "document": path, "parent": "name:笔刷纹理", "name": "纹理范围蒙版", "rects": [[78, 83, 356, 77, 255]]})
    operations.append(paint("金属亮边", "#EDD39A", 2.5, [{"kind": "path", "commands": [["M", 62, 89], ["Q", 65, 65, 84, 66], ["L", 428, 66], ["Q", 448, 67, 450, 88]]}]))
    highlight_path = [{"kind": "path", "commands": [["M", 88, 84], ["C", 170, 76, 345, 77, 423, 85]]}]
    operations.append(paint("高光", "#FFF3C4", 6, highlight_path))
    operations.append({"op": "mask.create", "document": path, "parent": "name:高光", "name": "高光范围蒙版", "rects": [[86, 76, 340, 20, 255]]})
    for x in (62, 450):
        operations.append(paint("装饰铆钉", "#E5BE78", 1, [{"kind": "ellipse", "rect": [x - 3, 116, 6, 9], "fill": "foreground"}]))
    operations.append({"op": "document.save", "document": path})
    call("execute", {"operations": operations})
    before = call("inspect", {"document": path})
    shutil.copyfile(call("preview", {"document": path})["path"], directory / "before.png")
    call("execute", {"operations": [{"op": "document.close", "document": path}, {"op": "document.open", "path": path}]})
    # 模拟反馈：上沿高光偏黄且过强，只调整高光及其蒙版。
    call("execute", {"operations": [
        {"op": "layer.clear", "document": path, "node": "name:高光"},
        paint("高光", "#B8E5E9", 3, highlight_path),
        {"op": "layer.update", "document": path, "node": "name:高光", "properties": {"opacity": 170}},
        {"op": "mask.update", "document": path, "node": "name:高光范围蒙版", "rects": [[100, 76, 312, 20, 255]]},
        {"op": "document.save", "document": path},
    ]})
    after = call("inspect", {"document": path})
    shutil.copyfile(call("preview", {"document": path})["path"], directory / "after.png")

    def indexed(root):
        result = {}
        for child in root["children"]:
            result[child["name"]] = child
            result.update(indexed(child))
        return result

    first, final = indexed(before["root"]), indexed(after["root"])
    assert first.keys() == final.keys()
    for name in first:
        assert first[name]["id"] == final[name]["id"], name
        if name not in ("高光", "高光范围蒙版") and "pixel_sha256" in first[name]:
            assert first[name]["pixel_sha256"] == final[name]["pixel_sha256"], name
    assert first["高光"]["pixel_sha256"] != final["高光"]["pixel_sha256"]
    (directory / "README.md").write_text(
        "# Krita 原生笔刷按钮样例\n\n使用原生路径填色、Basic-5 Size 描边和 Chalk Grainy 纹理，保留 9 个绘画图层、1 个分组和 2 个透明度蒙版。\n\n"
        "模拟反馈：上沿高光偏黄且过强，改为偏冷的窄高光。修改在关闭、重新打开后的同一 button.kra 内完成；其他绘画图层的像素哈希和所有节点 UUID 均保持。\n\n"
        "这是工具能力样例，没有使用参考风格，也不代表已验证风格识别或专业美术质量。\n\n"
        "绘制及修改的完整命令见 operations.json；源文件为 button.kra；前后预览为 before.png 和 after.png。\n",
        encoding="utf-8")
    print(directory)


if __name__ == "__main__":
    main()
