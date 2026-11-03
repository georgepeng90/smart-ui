"""圆形标记示例：循环生成笔触，用透明度蒙版限定条纹区域。"""

import math


def build(parameters, spec, component_id):
    size = spec["size"]
    center = size / 2
    brush = parameters["brush"]
    nodes = [
        {"key": "disc", "name": component_id + "/圆盘", "kind": "paintlayer", "parent": "root"},
        {"key": "stripes", "name": component_id + "/条纹", "kind": "paintlayer", "parent": "root"},
        {"key": "clip", "name": component_id + "/条纹范围", "kind": "transparencymask", "parent": "stripes",
         "rects": [[size//4, size//4, spec["clip_width"], size//2, 255]]},
        {"key": "mark", "name": component_id + "/星形", "kind": "paintlayer", "parent": "root"},
    ]
    star = []
    for index in range(10):
        radius = size * (0.28 if index % 2 == 0 else 0.13)
        angle = -math.pi / 2 + index * math.pi / 5
        star.append(["M" if index == 0 else "L", center + radius * math.cos(angle), center + radius * math.sin(angle)])
    star.append(["Z"])
    paint = [
        {"node": "disc", "brush": brush, "size": 4, "color": spec["edge"],
         "strokes": [{"kind": "ellipse", "rect": [8, 8, size-16, size-16], "fill": "foreground"}]},
        {"node": "disc", "brush": brush, "size": 1, "color": spec["face"],
         "strokes": [{"kind": "ellipse", "rect": [14, 14, size-28, size-28], "fill": "foreground"}]},
        {"node": "stripes", "brush": brush, "size": 4, "opacity": 0.35, "color": spec["shine"],
         "strokes": [{"kind": "line", "from": [0, y], "to": [size, y]} for y in range(0, size, 12)]},
        {"node": "mark", "brush": brush, "size": 2, "color": spec["shine"],
         "strokes": [{"kind": "path", "commands": star, "fill": "foreground"}]},
    ]
    return {"width": size, "height": size, "nodes": nodes, "paint": paint}
