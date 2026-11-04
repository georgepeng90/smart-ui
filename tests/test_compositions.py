from copy import deepcopy
import hashlib
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image
import pytest

from smartui_mcp.compositions import build_composition


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def image_node(tmp_path):
    source = tmp_path / "icon.png"
    with Image.new("RGBA", (4, 2), (200, 50, 70, 0)) as image:
        image.putpixel((1, 0), (250, 0, 0, 128))
        image.putpixel((2, 0), (0, 255, 0, 255))
        image.save(source)
    return {"id": "icon", "kind": "image", "source": str(source), "sha256": _sha(source),
            "x": 0, "y": 0, "width": 4, "height": 2, "region": None, "asset_id": "coin", "version": 1}


@pytest.fixture
def text_node(tmp_path):
    # 字体只包含 A 和空格，测试不依赖开发者机器上的任何字体。
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "space", "A"])
    builder.setupCharacterMap({32: "space", 65: "A"})
    glyphs = {}
    for name in (".notdef", "space", "A"):
        pen = TTGlyphPen(None)
        if name == "A":
            pen.moveTo((0, 0))
            pen.lineTo((500, 0))
            pen.lineTo((500, 700))
            pen.lineTo((0, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "CompositionTest", "styleName": "Regular",
                            "uniqueFontIdentifier": "CompositionTest", "fullName": "CompositionTest",
                            "psName": "CompositionTest"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    path = tmp_path / "fixture.ttf"
    builder.save(path)
    return {"id": "label", "kind": "text", "text": "A A", "font_path": str(path), "font_size": 20,
            "color": "#FF0000", "x": 2, "y": 2}


def _build(root, base_nodes, **kwargs):
    return build_composition(str(root), "example", **({"width": 64, "height": 48, "background": "#123456", "nodes": base_nodes} | kwargs))


def test_real_pixels_composite_once_and_layer_order_is_preserved(tmp_path, image_node):
    original = Path(image_node["source"])
    before = original.read_bytes(), original.stat().st_mtime_ns
    second = {**image_node, "id": "same-icon-again", "x": 1}
    result = _build(tmp_path, [image_node, second])
    with Image.open(result["images"]["overview"]["path"]) as overview:
        assert overview.getpixel((0, 0)) == (18, 52, 86, 255)
        assert overview.getpixel((1, 0)) == (134, 26, 43, 255)
        assert overview.getpixel((2, 0)) == (125, 127, 0, 255)
        assert overview.getpixel((3, 0)) == (0, 255, 0, 255)
    with Image.open(result["images"]["node-1"]["path"]) as snapshot:
        assert snapshot.getpixel((0, 0)) == (200, 50, 70, 0)
        assert snapshot.getpixel((1, 0)) == (250, 0, 0, 128)
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before
    assert result["color_processing"]["icc_conversion"] is False
    assert result["nodes"][0]["sha256"] == image_node["sha256"]
    assert result["nodes"][1]["snapshot_key"] == "node-2"
    assert not list((tmp_path / ".smartui/compositions/example").glob("*.json"))
    for info in result["images"].values():
        assert Path(info["path"]).is_absolute()
        assert _sha(Path(info["path"])) == info["sha256"]


def test_crop_is_original_pixels_and_contain_is_centered(tmp_path, image_node):
    node = {**image_node, "x": 10, "y": 8, "width": 8, "height": 8, "region": [1, 0, 2, 1]}
    result = _build(tmp_path, [node])
    evidence = result["nodes"][0]
    assert evidence["display_rect"] == [10, 10, 8, 4]
    assert evidence["source_size"] == [4, 2]
    assert evidence["region_source_xywh"] == node["region"]
    with Image.open(result["images"]["node-1"]["path"]) as snapshot:
        assert snapshot.size == (2, 1)
        assert [snapshot.getpixel((x, 0)) for x in range(2)] == [(250, 0, 0, 128), (0, 255, 0, 255)]
    with Image.open(result["images"]["overview"]["path"]) as overview:
        assert overview.getpixel((10, 9)) == (18, 52, 86, 255)
        assert overview.getpixel((10, 14)) == (18, 52, 86, 255)


def test_tall_image_contain_uses_width_and_integer_pixel_rounding(tmp_path, image_node):
    result = _build(tmp_path, [{**image_node, "width": 3, "height": 1}])
    assert result["nodes"][0]["display_rect"] == [0, 0, 2, 1]


def test_font_bytes_snapshot_and_text_anchor_are_recorded(tmp_path, text_node):
    source = Path(text_node["font_path"])
    before = source.read_bytes(), source.stat().st_mtime_ns
    result = _build(tmp_path, [text_node])
    info = result["fonts"]["font-1"]
    assert Path(info["path"]).read_bytes() == before[0]
    assert info["sha256"] == _sha(source) == result["nodes"][0]["font_sha256"]
    assert result["nodes"][0]["anchor"] == "lt"
    assert result["nodes"][0]["display_rect"] == [2, 2, 36, 14]
    with Image.open(result["images"]["overview"]["path"]) as image:
        assert image.getpixel((2, 2)) == (255, 0, 0, 255)
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    # 历史记录所需的所有像素和字体均在输出目录中，不依赖源文件存活。
    source.unlink()
    assert Path(info["path"]).read_bytes() == before[0]


@pytest.mark.parametrize("style_version", [0, 1])
def test_component_keeps_run_and_spec_evidence_without_mutating_input(tmp_path, image_node, style_version):
    node = {key: value for key, value in image_node.items() if key not in ("asset_id", "version")}
    node.update(kind="component", component_id="panel", spec={"width": 4, "height": 2}, run={
        "run_id": "run-1", "status": "drawn", "style_version": style_version, "spec_sha256": "1" * 64,
        "program_id": "panel-program", "program_version": 2, "program_sha256": "2" * 64})
    before = deepcopy(node)
    result = _build(tmp_path, [node])
    assert result["nodes"][0]["run"] == node["run"]
    assert result["nodes"][0]["spec"] == node["spec"]
    assert node == before


def test_same_inputs_produce_same_pixels_snapshots_and_font_bytes(tmp_path, image_node, text_node):
    text_node.update(stroke_width=1, stroke_color="#00FF00")
    first = _build(tmp_path, [image_node, text_node])
    second = build_composition(str(tmp_path), "repeat", 64, 48, "#123456", [image_node, text_node])
    for group in ("images", "fonts"):
        assert {key: info["sha256"] for key, info in first[group].items()} == {
            key: info["sha256"] for key, info in second[group].items()}
    assert first["nodes"] == second["nodes"]
    with Image.open(first["images"]["overview"]["path"]) as image:
        assert image.getpixel((1, 2)) == (0, 255, 0, 255)


def test_large_stroke_is_allowed_when_actual_text_bounds_fit_canvas(tmp_path, text_node):
    node = {**text_node, "text": "A", "x": 280, "y": 280, "stroke_width": 257}
    result = _build(tmp_path, [node], width=640, height=640)
    assert result["nodes"][0]["stroke_width"] == 257
    x, y, width, height = result["nodes"][0]["display_rect"]
    assert x >= 0 and y >= 0 and x + width <= 640 and y + height <= 640


@pytest.mark.parametrize("updates, message", [
    ({"x": True}, "x"), ({"x": float("nan")}, "x"), ({"width": float("inf")}, "width"),
    ({"y": -1}, "y"), ({"width": 0}, "width"), ({"width": 65}, "边界"),
    ({"region": [0, 0, 5, 1]}, "region"), ({"region": [0, 0, True, 1]}, "region"),
    ({"version": True}, "version"), ({"sha256": "invalid"}, "SHA-256"),
    ({"unknown": 1}, "未知"), ({"kind": "bogus"}, "kind"),
    ({1: "invalid field"}, "字符串字段名"),
])
def test_bad_image_nodes_are_rejected_without_publishing(tmp_path, image_node, updates, message):
    with pytest.raises(ValueError, match=message):
        _build(tmp_path, [{**image_node, **updates}])
    assert not (tmp_path / ".smartui/compositions/example").exists()


@pytest.mark.parametrize("updates, message", [
    ({"text": "B"}, "缺少必要字符.*U\\+0042"), ({"text": "A\nA"}, "单行"),
    ({"font_size": True}, "font_size"), ({"font_size": 0}, "font_size"),
    ({"color": "red"}, "color"), ({"stroke_width": -1}, "stroke_width"),
    ({"x": 0, "stroke_width": 2}, "边界"), ({"x": 60}, "边界"),
])
def test_bad_text_nodes_fail_clearly(tmp_path, text_node, updates, message):
    with pytest.raises(ValueError, match=message):
        _build(tmp_path, [{**text_node, **updates}])
    assert not (tmp_path / ".smartui/compositions/example").exists()


def test_missing_or_invalid_font_never_uses_default(tmp_path, text_node):
    source = Path(text_node["font_path"])
    source.write_bytes(b"not a font")
    with pytest.raises(ValueError, match="无法使用字体.*fixture.ttf"):
        _build(tmp_path, [text_node])
    source.unlink()
    with pytest.raises(FileNotFoundError):
        _build(tmp_path, [text_node])


def test_tampered_source_discards_all_staging_files(tmp_path, image_node, text_node):
    Path(image_node["source"]).write_bytes(b"modified")
    with pytest.raises(ValueError, match="SHA-256 与记录不一致"):
        _build(tmp_path, [text_node, image_node])
    assert list((tmp_path / ".smartui/compositions").iterdir()) == []


@pytest.mark.parametrize("options, message", [
    ({"width": True}, "width"), ({"height": 0}, "height"), ({"width": 8193}, "超过上限"),
    ({"background": "#123456FF"}, "background"), ({"nodes": []}, "nodes"),
])
def test_bad_canvas_options_are_rejected_before_output(tmp_path, image_node, options, message):
    with pytest.raises(ValueError, match=message):
        _build(tmp_path, [image_node], **options)
    assert not (tmp_path / ".smartui/compositions").exists()


def test_duplicate_node_ids_and_excess_node_count_rejected(tmp_path, image_node):
    with pytest.raises(ValueError, match="唯一"):
        _build(tmp_path, [image_node, image_node])
    with pytest.raises(ValueError, match="64"):
        _build(tmp_path, [image_node] * 65)


def test_existing_output_is_never_overwritten(tmp_path, image_node):
    result = _build(tmp_path, [image_node])
    path = Path(result["images"]["overview"]["path"])
    original = path.read_bytes()
    with pytest.raises(FileExistsError, match="不能覆盖"):
        _build(tmp_path, [{**image_node, "x": 5}])
    assert path.read_bytes() == original


def test_output_write_error_does_not_publish_partial_result(tmp_path, image_node, monkeypatch):
    original_save = Image.Image.save

    def fail_overview(image, path, *args, **kwargs):
        if Path(path).name == "overview.png":
            raise OSError("组合图写入失败")
        return original_save(image, path, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", fail_overview)
    with pytest.raises(OSError, match="组合图写入失败"):
        _build(tmp_path, [image_node])
    assert list((tmp_path / ".smartui/compositions").iterdir()) == []


def test_managed_output_rejects_directory_links(tmp_path, image_node, monkeypatch):
    linked = tmp_path / ".smartui"
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == linked or original(path))
    with pytest.raises(ValueError, match="符号链接或目录联接"):
        _build(tmp_path, [image_node])
    assert not linked.exists()
