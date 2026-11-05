"""以真实 Chromium 验证独立页面、浏览器文字和截图来源。"""

import hashlib
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image
from playwright.sync_api import sync_playwright
import pytest

from smartui_mcp.browser_preview import render_page


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def page(tmp_path):
    return {"width": 240, "height": 180, "background": "#123456", "nodes": []}


@pytest.fixture
def image_node(tmp_path):
    source = tmp_path / "独立图像.png"
    image = Image.new("RGBA", (6, 6), "#00FF00")
    for x in range(2):
        for y in range(2):
            image.putpixel((x, y), (255, 0, 0, 255))
            image.putpixel((4 + x, y), (0, 0, 255, 255))
            image.putpixel((x, 4 + y), (255, 255, 0, 255))
            image.putpixel((4 + x, 4 + y), (255, 0, 255, 255))
    image.putpixel((0, 0), (255, 0, 0, 128))
    image.save(source)
    patches = []
    for sy, sh, ty, th in ((0, 2, 0, 2), (2, 2, 2, 12), (4, 2, 14, 2)):
        for sx, sw, tx, tw in ((0, 2, 0, 2), (2, 2, 2, 10), (4, 2, 12, 2)):
            patches.append({"source": [sx, sy, sw, sh], "target": [tx, ty, tw, th]})
    return {"id": "九宫格", "kind": "image", "x": 4, "y": 4, "width": 10, "height": 12,
            "source": str(source), "sha256": _sha(source),
            "patch_plan": {"image_size": [14, 16], "content_offset": [-2, -2], "patches": patches}}


@pytest.fixture
def text_node(tmp_path):
    # 合成字体覆盖用例文本，只验证字体加载和浏览器排版，不代表中文美术效果。
    characters = sorted(set("中文 A</script><script>window.injected=1</script>&\u2028"))
    names = {character: f"uni{ord(character):04X}" for character in characters}
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", *names.values()])
    builder.setupCharacterMap({ord(character): name for character, name in names.items()})
    glyphs = {}
    for name in [".notdef", *names.values()]:
        pen = TTGlyphPen(None)
        if name not in (".notdef", names[" "]):
            pen.moveTo((80, 0))
            pen.lineTo((520, 0))
            pen.lineTo((520, 700))
            pen.lineTo((80, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 80) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "BrowserPreviewTest", "styleName": "Regular",
                            "uniqueFontIdentifier": "BrowserPreviewTest", "fullName": "BrowserPreviewTest",
                            "psName": "BrowserPreviewTest"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    font = tmp_path / "browser-preview.ttf"
    builder.save(font)
    return {"id": "中文文案", "kind": "text", "x": 20, "y": 30, "width": 200, "height": 80,
            "text": "中文 A", "font_path": str(font), "font_sha256": _sha(font),
            "font_size": 20, "line_height": 28, "align": "left", "wrap": True,
            "overflow": "error", "color": "#FFFFFF", "stroke_width": 0, "stroke_color": "#000000"}


def test_nine_slice_corners_offsets_and_browser_evidence(tmp_path, page, image_node):
    page["nodes"] = [image_node]
    result = render_page(tmp_path / "render", page)
    with Image.open(result["overview"]["path"]) as image:
        image = image.convert("RGBA")
        assert image.size == (240, 180)
        assert image.getpixel((1, 1)) == (18, 52, 86, 255)
        assert image.getpixel((2, 2)) == (137, 26, 43, 255)
        assert image.getpixel((3, 3)) == (255, 0, 0, 255)
        assert image.getpixel((14, 2)) == (0, 0, 255, 255)
        assert image.getpixel((2, 16)) == (255, 255, 0, 255)
        assert image.getpixel((14, 16)) == (255, 0, 255, 255)
        assert image.getpixel((9, 9)) == (0, 255, 0, 255)
    renderer = result["renderer"]
    assert renderer["engine"] == "chromium"
    assert renderer["version"]
    assert renderer["viewport"] == {"width": 240, "height": 180}
    assert renderer["device_scale_factor"] == 1
    assert renderer["nodes"][0]["box"] == [2, 2, 14, 16]
    for key in ("overview", "html"):
        assert result[key]["sha256"] == _sha(result[key]["path"])


def test_fractional_nine_slice_size_does_not_rescale_fixed_left_corner(tmp_path, page, image_node):
    image_node["width"] += 0.5
    plan = image_node["patch_plan"]
    plan["image_size"][0] += 0.5
    for patch in plan["patches"]:
        if patch["target"][0] == 2:
            patch["target"][2] += 0.5
        elif patch["target"][0] == 12:
            patch["target"][0] += 0.5
    page["nodes"] = [image_node]
    result = render_page(tmp_path / "render", page)
    assert result["renderer"]["nodes"][0]["box"] == [2, 2, 14.5, 16]
    with Image.open(result["overview"]["path"]) as image:
        image = image.convert("RGBA")
        assert image.getpixel((2, 2)) == (137, 26, 43, 255)
        assert image.getpixel((3, 3)) == (255, 0, 0, 255)


def test_slice_edges_do_not_sample_colors_from_neighboring_source_regions(tmp_path, page, image_node):
    page["nodes"] = [image_node]
    result = render_page(tmp_path / "render", page)
    with Image.open(result["overview"]["path"]) as image:
        image = image.convert("RGBA")
        # 四条绿边和中央都必须保持纯绿，包括紧贴其他颜色切片的位置。
        for bounds in ((4, 2, 14, 4), (2, 4, 4, 16), (14, 4, 16, 16),
                       (4, 16, 14, 18), (4, 4, 14, 16)):
            region = image.crop(bounds)
            assert region.getcolors() == [(region.width * region.height, (0, 255, 0, 255))]


def test_offline_html_embeds_sources_and_uses_same_page_for_png(tmp_path, page, image_node, text_node):
    page["nodes"] = [image_node, text_node]
    result = render_page(tmp_path / "render", page)
    Path(image_node["source"]).unlink()
    html = Path(result["html"]["path"])
    document = html.read_text(encoding="utf-8")
    assert image_node["source"] not in document
    assert text_node["font_path"] not in document
    assert "data:image/png;base64," in document and "data:font/otf;base64," in document
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 240, "height": 180}, device_scale_factor=1)
        context.set_offline(True)
        tab = context.new_page()
        requests = []
        tab.on("request", lambda request: requests.append(request.url))
        tab.goto(html.as_uri())
        evidence = tab.evaluate("() => window.smartuiReady")
        assert evidence["fonts"][0]["status"] == "loaded"
        assert tab.locator("#toolbar").is_visible()
        tab.locator("#zoom").select_option("2")
        assert tab.locator("#stage").evaluate("element => element.getBoundingClientRect().width") == 480
        tab.evaluate("() => window.smartuiForScreenshot()")
        exported = tmp_path / "same-page.png"
        tab.screenshot(path=str(exported), omit_background=True)
        assert all(url.startswith(("file:", "data:")) for url in requests)
        browser.close()
    with Image.open(result["overview"]["path"]) as original, Image.open(exported) as repeated:
        assert original.size == repeated.size
        assert original.tobytes() == repeated.tobytes()


def test_chinese_and_script_text_remain_text(tmp_path, page, text_node):
    text_node.update(text="中文</script><script>window.injected=1</script>&\u2028", font_size=12,
                     line_height=20, height=120)
    text_node["id"] = '<img src="https://example.invalid/x" onerror="window.injected=2">'
    page["nodes"] = [text_node]
    result = render_page(tmp_path / "render", page)
    assert result["renderer"]["fonts"][0]["sha256"] == text_node["font_sha256"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        tab = browser.new_page()
        tab.goto(Path(result["html"]["path"]).as_uri())
        tab.evaluate("() => window.smartuiReady")
        assert tab.locator(".node").text_content() == text_node["text"]
        assert tab.evaluate("() => window.injected") is None
        assert tab.locator("script").count() == 2
        assert tab.locator("img").count() == 0
        browser.close()


@pytest.mark.parametrize("change", [{"width": 15, "wrap": False}, {"height": 20},
                                    {"text": "中文\n中文\n中文", "height": 50}])
def test_text_overflow_requires_explicit_clip(tmp_path, page, text_node, change):
    text_node.update(change)
    page["nodes"] = [text_node]
    with pytest.raises(ValueError, match="中文文案.*超出文本框"):
        render_page(tmp_path / "reject", page)
    text_node["overflow"] = "clip"
    result = render_page(tmp_path / "clip", page)
    assert result["renderer"]["nodes"][0]["overflow"] is True
    assert Path(result["overview"]["path"]).is_file()


def test_wrap_and_alignment_are_browser_layout(tmp_path, page, text_node):
    text_node.update(text="中文中文中文中文", width=90, height=100, align="center")
    page["nodes"] = [text_node]
    result = render_page(tmp_path / "render", page)
    measure = result["renderer"]["nodes"][0]
    assert measure["overflow"] is False
    assert measure["content_box"][3] > text_node["line_height"]
    assert measure["content_box"][0] > text_node["x"]


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_text_alignment_reserves_space_for_the_complete_stroke(tmp_path, page, text_node, align):
    text_node.update(align=align, width=80, height=36, wrap=False,
                     stroke_width=6, stroke_color="#FF0000")
    page["nodes"] = [text_node]
    result = render_page(tmp_path / "render", page)
    measure = result["renderer"]["nodes"][0]
    box, content = measure["box"], measure["content_box"]
    assert box == [text_node[key] for key in ("x", "y", "width", "height")]
    assert measure["overflow"] is False
    half_stroke = text_node["stroke_width"] / 2
    assert content[0] - half_stroke >= box[0] - 0.02
    assert content[0] + content[2] + half_stroke <= box[0] + box[2] + 0.02
    if align == "left":
        assert content[0] == pytest.approx(box[0] + half_stroke)
    elif align == "right":
        assert content[0] + content[2] == pytest.approx(box[0] + box[2] - half_stroke)
    else:
        assert content[0] + content[2] / 2 == pytest.approx(box[0] + box[2] / 2)
    with Image.open(result["overview"]["path"]) as image:
        # 真实截图仍包含描边颜色，而非通过去掉描边绕过排版检查。
        assert any(color == (255, 0, 0) for _, color in image.convert("RGB").getcolors(image.width * image.height))


def test_stroked_text_wraps_within_the_inner_box_and_still_rejects_real_overflow(tmp_path, page, text_node):
    text_node.update(text="A A", width=37, height=64, stroke_width=4, wrap=True)
    page["nodes"] = [text_node]
    result = render_page(tmp_path / "wrapped", page)
    measure = result["renderer"]["nodes"][0]
    assert measure["overflow"] is False
    assert measure["content_box"][3] > text_node["line_height"]
    text_node["height"] = 55
    with pytest.raises(ValueError, match="中文文案.*超出文本框"):
        render_page(tmp_path / "too-short", page)
    text_node.update(text="中文 A", width=48, height=36, wrap=False)
    with pytest.raises(ValueError, match="中文文案.*超出文本框"):
        render_page(tmp_path / "too-narrow", page)


def test_missing_and_modified_sources_are_explicit_errors(tmp_path, page, image_node, text_node):
    page["nodes"] = [image_node]
    Path(image_node["source"]).unlink()
    with pytest.raises(ValueError, match="九宫格.*不存在"):
        render_page(tmp_path / "missing", page)
    page["nodes"] = [text_node]
    text_node["font_path"] = str(tmp_path / "missing.ttf")
    with pytest.raises(ValueError, match="中文文案.*不存在"):
        render_page(tmp_path / "missing-font", page)
    path = tmp_path / "corrupt.ttf"
    path.write_bytes(b"invalid-font")
    text_node.update(font_path=str(path), font_sha256="wrong")
    with pytest.raises(ValueError, match="摘要与快照不一致"):
        render_page(tmp_path / "changed", page)
    text_node["font_sha256"] = _sha(path)
    with pytest.raises(ValueError, match="字体.*加载失败"):
        render_page(tmp_path / "invalid", page)


def test_corrupt_image_is_not_silently_replaced(tmp_path, page, image_node):
    source = Path(image_node["source"])
    source.write_bytes(b"not a png")
    image_node["sha256"] = _sha(source)
    page["nodes"] = [image_node]
    with pytest.raises(ValueError, match="九宫格.*解码失败"):
        render_page(tmp_path / "render", page)
