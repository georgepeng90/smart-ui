"""在浏览器干净稿上绘制供人工评审的适配标记。"""

from PIL import Image, ImageDraw, ImageFont


def validate_device(device, page):
    if device is None:
        return {"top": max(1, round(page["width"] * .06)),
                "bottom": max(1, round(page["width"] * .04)),
                "label": "Illustrative device", "extend_background": True}
    if not isinstance(device, dict) or device.keys() - {
            "top", "bottom", "label", "background_node", "extend_background"}:
        raise ValueError("annotation_device 包含未知字段")
    for key in ("top", "bottom"):
        if type(device.get(key)) is not int or not 0 <= device[key] <= 2048:
            raise ValueError(f"annotation_device.{key} 必须为 0 到 2048 的整数")
    if not isinstance(device.get("label"), str) or not device["label"].strip():
        raise ValueError("annotation_device.label 必须说明设备或示意用途")
    if type(device.get("extend_background", False)) is not bool:
        raise ValueError("annotation_device.extend_background 必须为布尔值")
    background = device.get("background_node")
    if background is not None and (not isinstance(background, str) or not any(
            node["id"] == background and node["kind"] == "image" for node in page["nodes"])):
        raise ValueError("annotation_device.background_node 必须指向现有图像节点")
    return device


def render_annotation(clean, output, page, renderer, device):
    device = validate_device(device, page)
    width, height = page["width"], page["height"]
    top, bottom = device.get("top", 0), device.get("bottom", 0)
    scale = max(1, round(width / 540))
    font = ImageFont.load_default(size=13 * scale)
    legend = ("Element Bounds", "Anchor Side", "Pivot", "Stretch Direction", "Safe Area", "Layout Group")
    if top or bottom:
        legend += ("Design Area",)
    gray, gold, green, blue = "#999999", "#ffbe32", "#49e592", "#49aaff"
    outline_styles = {
        "Element Bounds": (gray, None),
        "Safe Area": (green, 4),
        "Layout Group": ("#cf91ff", 8),
        "Design Area": ("#e3e8ef", 0),
    }
    legend_width = round(max(font.getlength(label) for label in legend)) + 35 * scale
    columns = min(len(legend), max(1, width // legend_width))
    row_height = 22 * scale
    footer = ((len(legend) + columns - 1) // columns + 1) * row_height
    full_height = top + height + bottom
    sheet = Image.new("RGBA", (width, full_height + footer), "#19212d")
    if device.get("extend_background") or device.get("background_node"):
        sheet.paste(page["background"], (0, 0, width, full_height))
    if device.get("background_node"):
        node = next(node for node in page["nodes"] if node["id"] == device["background_node"])
        with Image.open(node["source"]) as source:
            background = source.convert("RGBA").resize((width, full_height), Image.Resampling.LANCZOS)
        sheet.alpha_composite(background)
    with Image.open(clean) as source:
        sheet.paste(source.convert("RGBA"), (0, top))
    draw = ImageDraw.Draw(sheet)

    def dashed(box, color, phase=None):
        x, y, right, bottom_edge = box
        period = (7 if phase is None else 12) * scale
        dash_length = 3 * scale

        def segments(start, end):
            # Align design, safe area, and group dashes to the same grid so shared edges show each color.
            first = start if phase is None else start - (start - phase * scale) % period
            # Short edges that lie in a gap still need a visible dash.
            if first + dash_length < start and first + period >= end:
                first = start
            for offset in range(first, end, period):
                near, far = max(offset, start), min(offset + dash_length, end)
                if near <= far:
                    yield near, far

        for start, end in segments(x, right):
            for edge in (y, bottom_edge):
                draw.line((start, edge, end, edge), fill=color, width=scale)
        for start, end in segments(y, bottom_edge):
            for edge in (x, right):
                draw.line((edge, start, edge, end), fill=color, width=scale)

    def arrow(x, y, length, vertical):
        def point(along, across):
            return (x + across, y + along) if vertical else (x + along, y + across)

        tip = min(5 * scale, length // 3)
        draw.line((point(0, 0), point(length, 0)), fill=blue, width=scale)
        for end, inside in ((0, tip), (length, length - tip)):
            draw.line((point(inside, -tip), point(end, 0), point(inside, tip)), fill=blue, width=scale)

    def anchor(x, y):
        radius = 4 * scale
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=gold)

    def pivot(x, y):
        arm = 5 * scale
        draw.line((x - arm, y, x + arm, y), fill=green, width=scale)
        draw.line((x, y - arm, x, y + arm), fill=green, width=scale)

    text_boxes = []
    for node in renderer["nodes"]:
        if node["kind"] == "text" and node.get("visible_box"):
            x, y, w, h = node["visible_box"]
            text_boxes.append((x, top + y, x + w, top + y + h))

    def stretch(x, y, right, bottom_edge, vertical):
        start, end = (y, bottom_edge) if vertical else (x, right)
        near, far = (x, right) if vertical else (y, bottom_edge)
        gap = 8 * scale
        tip = min(5 * scale, (end - start) // 3)
        if tip < 1:
            return False
        for edge in (near - gap, far + gap, far - gap, near + gap):
            box = ((edge - tip, start, edge + tip, end) if vertical else
                   (start, edge - tip, end, edge + tip))
            l, t, r, b = box
            if l <= 0 or r >= width - 1 or t <= top or b >= top + height - 1:
                continue
            if any(l <= tr and r >= tl and t <= tb and b >= tt for tl, tt, tr, tb in text_boxes):
                continue
            arrow(edge if vertical else start, start if vertical else edge, end - start, vertical)
            return True
        return False

    missing = []
    omitted_stretch = []
    if top or bottom:
        dashed((0, top, width - 1, top + height - 1), *outline_styles["Design Area"])
    if "safe_area" in page:
        safe = page["safe_area"]
        dashed((safe["left"], top + safe["top"], width - safe["right"] - 1,
                top + height - safe["bottom"] - 1), *outline_styles["Safe Area"])
    else:
        missing.append({"field": "safe_area"})
    if top:
        draw.rounded_rectangle((width * .37, -top, width * .63, top * .65), radius=top // 3, fill="#090e16")
    if bottom:
        y = full_height - max(1, bottom // 3)
        draw.rounded_rectangle((width * .35, y, width * .65, min(full_height - 1, y + 3 * scale)),
                               radius=2 * scale, fill="#e3e8ef")

    bounds = {node["id"]: node["layout_box"] for node in renderer["nodes"]}
    bounds.update({group["id"]: group["box"] for group in page.get("layout_groups", ())})
    groups = {group["id"] for group in page.get("layout_groups", ())}
    adaptations = page.get("unity_adaptation", {})
    for node_id, (x, y, w, h) in bounds.items():
        x, y, w, h = round(x), top + round(y), round(w), round(h)
        if node_id in groups or adaptations.get(node_id, {}).get("annotate", True):
            label = "Layout Group" if node_id in groups else "Element Bounds"
            dashed((x, y, x + w - 1, y + h - 1), *outline_styles[label])
    for node_id, (x, y, w, h) in bounds.items():
        x, y, w, h = round(x), top + round(y), round(w), round(h)
        settings = adaptations.get(node_id, {})
        # 隐藏重复标记仍检查完整约束，避免代表条目掩盖其他实例的缺项。
        if not settings.get("annotate", True):
            missing.extend({"node_id": node_id, "field": field}
                           for field in ("anchor_min", "anchor_max", "pivot") if field not in settings)
            continue
        if "anchor_min" in settings:
            low, high = settings["anchor_min"], settings["anchor_max"]
            sides = [start if start == end and start in (0, 1) else .5 for start, end in zip(low, high)]
            if sides != [.5, .5]:
                anchor(x + sides[0] * (w - 1), y + (1 - sides[1]) * (h - 1))
            for axis, (start, end) in enumerate(zip(low, high)):
                if start != end and not stretch(x, y, x + w - 1, y + h - 1, axis == 1):
                    omitted_stretch.append({"node_id": node_id, "axis": "xy"[axis]})
        else:
            missing.extend({"node_id": node_id, "field": field} for field in ("anchor_min", "anchor_max"))
        if "pivot" in settings:
            px, py = settings["pivot"]
            px, py = x + px * (w - 1), y + (1 - py) * (h - 1)
            pivot(px, py)
        else:
            missing.append({"node_id": node_id, "field": "pivot"})

    for index, label in enumerate(legend):
        x = (index % columns) * (width // columns) + 5 * scale
        y = full_height + (index // columns) * row_height + 3 * scale
        mid = y + 8 * scale
        if index == 1:
            anchor(x + 10 * scale, mid)
        elif index == 2:
            pivot(x + 10 * scale, mid)
        elif index == 3:
            arrow(x, mid, 20 * scale, False)
        else:
            dashed((x, mid - 5 * scale, x + 20 * scale, mid + 5 * scale), *outline_styles[label])
        draw.text((x + 27 * scale, y), label, fill="#e3e8ef", font=font)
    caption = f"Design Area: {width} x {height}"
    if top or bottom:
        caption += f" | {device['label']} (+{top} / +{bottom} px)"
    draw.text((5 * scale, full_height + footer - row_height), caption, fill="#e3e8ef", font=font)
    sheet.save(output)
    return {"size": list(sheet.size), "missing": missing, "omitted_stretch": omitted_stretch}
