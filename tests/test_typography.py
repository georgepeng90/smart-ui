"""项目文字规范、字体身份及实际外观摘要。"""

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil

from fontTools.ttLib import TTFont
import pytest

from smartui_mcp.typography import render_typography, resolve_style, style_digest, validate_font, validate_typography
from test_compositions import text_node


REFERENCES = {"sample": {"width": 800, "height": 400}}


@pytest.fixture
def typography(tmp_path, text_node):
    return {
        "description": "用于固定分辨率的中文界面。",
        "design_size": {"width": 1920, "height": 1080},
        "size_scale": {"regular": 24, "same-size": 24},
        "styles": {
            "body": {
                "name": "正文",
                "usage": "用于多行帮助说明。",
                "size_id": "regular",
                "font_path": text_node["font_path"],
                "line_height": 32,
                "letter_spacing": -0.5,
                "fill": {"color": "#ffaa11", "opacity": 1.0},
                "stroke": {"width": 1.5, "color": "#2233aa", "opacity": 0.75},
                "shadow": {"offset_x": -2, "offset_y": 3.5, "blur": 4, "color": "#3322bb", "opacity": 0.25},
                "evidence": [{
                    "reference_id": "sample", "region": [10, 20, 40, 60], "coordinate_size": [400, 200],
                    "observation": "正文的行距比按钮标签更宽。",
                    "decision": "保留多行说明所需的行距。",
                }],
            },
        },
    }


def test_resolves_all_rendering_values_without_mutating_inputs(tmp_path, typography):
    before = deepcopy(typography)
    current = validate_typography(typography, tmp_path, REFERENCES)
    assert typography == before
    font_path = Path(before["styles"]["body"]["font_path"])
    digest = hashlib.sha256(font_path.read_bytes()).hexdigest()
    assert resolve_style(current, "body") == {
        "font_path": str(font_path.resolve()), "font_sha256": digest, "font_size": 24,
        "line_height": 32, "letter_spacing": -0.5,
        "fill": {"color": "#FFAA11", "opacity": 1},
        "stroke": {"width": 1.5, "color": "#2233AA", "opacity": 0.75},
        "shadow": {"offset_x": -2, "offset_y": 3.5, "blur": 4, "color": "#3322BB", "opacity": 0.25},
    }
    resolved = resolve_style(current, "body")
    resolved["fill"]["color"] = "#FFFFFF"
    assert current["styles"]["body"]["fill"]["color"] == "#FFAA11"
    for missing in (None, current):
        with pytest.raises(ValueError, match="不存在的文字样式"):
            resolve_style(missing, "caption")


@pytest.mark.parametrize(("field", "value"), [
    ("design_size.width", True),
    ("size_scale.regular", 0),
    ("size_scale.REGULAR", 24),
    ("styles.BODY", {}),
    ("styles.body.name", " "),
    ("styles.body.usage", None),
    ("styles.body.size_id", "missing"),
    ("styles.body.line_height", 32.0),
    ("styles.body.letter_spacing", float("nan")),
    ("styles.body.fill.color", "#ffaa"),
    ("styles.body.fill.opacity", True),
    ("styles.body.stroke.width", -1),
    ("styles.body.stroke.color", "black"),
    ("styles.body.stroke.opacity", 1.01),
    ("styles.body.shadow.offset_x", float("inf")),
    ("styles.body.shadow.blur", -0.01),
    ("styles.body.shadow.color", "#10010000"),
    ("styles.body.shadow.opacity", -0.01),
    ("styles.body.fill.extra", 1),
    ("styles.body.font_sha256", None),
])
def test_typography_rejects_invalid_fields(tmp_path, typography, field, value):
    target = typography
    *parents, key = field.split(".")
    for parent in parents:
        target = target[parent]
    target[key] = value
    with pytest.raises(ValueError, match=parents[-1] if key == "extra" else key):
        validate_typography(typography, tmp_path, REFERENCES)


@pytest.mark.parametrize(("field", "value"), [
    ("reference_id", "missing"),
    ("coordinate_size", [400, False]),
    ("region", [399, 20, 40, 60]),
    ("region", [10, 20, 0, 60]),
    ("observation", ""),
    ("decision", []),
])
def test_evidence_requires_existing_references_and_valid_regions(tmp_path, typography, field, value):
    typography["styles"]["body"]["evidence"][0][field] = value
    with pytest.raises(ValueError, match=field):
        validate_typography(typography, tmp_path, REFERENCES)


def test_empty_scale_and_roles_and_optional_effects(tmp_path, typography):
    empty = {"description": "", "design_size": {"width": 1, "height": 1}, "size_scale": {}, "styles": {}}
    assert validate_typography(empty, tmp_path, {}) == empty
    with pytest.raises(ValueError, match="typography"):
        validate_typography(None, tmp_path, {})
    typography["styles"]["body"].update(stroke=None, shadow=None, evidence=[])
    current = validate_typography(typography, tmp_path, {})
    resolved = resolve_style(current, "body")
    assert resolved["stroke"] is None and resolved["shadow"] is None


def test_fonts_require_persistent_project_paths(tmp_path, typography):
    role = typography["styles"]["body"]
    font = Path(role["font_path"])
    cached = tmp_path / ".smartui" / font.name
    cached.parent.mkdir()
    shutil.copyfile(font, cached)
    for path, error in [(font.name, "绝对路径"), (tmp_path.parent / font.name, "工程内"), (cached, "本机文件")]:
        role["font_path"] = str(path)
        with pytest.raises(ValueError, match=error):
            validate_typography(typography, tmp_path, REFERENCES)


def test_font_validation_rejects_missing_glyphs_and_invalid_font_data(tmp_path, typography):
    path = Path(typography["styles"]["body"]["font_path"])
    assert validate_font(path, text="A A\nA")[0] == str(path.resolve())
    with pytest.raises(ValueError, match="缺少字符.*U\\+4E2D"):
        validate_font(path, text="A 中")
    path.write_bytes(b"invalid font")
    with pytest.raises(ValueError, match="无法读取字体.*fixture.ttf"):
        validate_font(path)


def test_font_changes_must_be_accepted_explicitly_and_can_be_skipped_for_snapshots(tmp_path, typography):
    current = validate_typography(typography, tmp_path, REFERENCES)
    font = Path(current["styles"]["body"]["font_path"])
    with TTFont(font) as changed:
        changed["name"].setName("Changed", 1, 3, 1, 0x0409)
        changed.save(font)
    with pytest.raises(ValueError, match="SHA-256 不符"):
        validate_typography(current, tmp_path, REFERENCES)
    updated = validate_typography(typography, tmp_path, REFERENCES)
    assert style_digest(updated, "body") != style_digest(current, "body")
    font.unlink()
    assert validate_typography(current, tmp_path, REFERENCES, verify_fonts=False) == current
    with pytest.raises(ValueError, match="font_sha256"):
        validate_typography(typography, tmp_path, REFERENCES, verify_fonts=False)


def test_digest_depends_on_visual_values_and_font_bytes(tmp_path, typography):
    current = validate_typography(typography, tmp_path, REFERENCES)
    digest = style_digest(current, "body")
    edited = deepcopy(typography)
    role = edited["styles"]["body"]
    source = Path(role["font_path"])
    moved = source.with_name("moved-font.ttf")
    source.rename(moved)
    role.update(name="帮助文字", usage="辅助说明", evidence=[], size_id="same-size", font_path=str(moved))
    role["fill"] = {"color": "#FFAA11", "opacity": 1}
    edited["description"] = "仅更新说明"
    edited["design_size"] = {"width": 1280, "height": 720}
    edited["size_scale"]["unused"] = 40
    assert style_digest(validate_typography(edited, tmp_path, {}), "body") == digest
    for effect in ("fill", "stroke", "shadow"):
        changed = deepcopy(edited)
        changed["styles"]["body"][effect]["color"] = "#AABBCC"
        assert style_digest(validate_typography(changed, tmp_path, {}), "body") != digest
    edited["size_scale"]["same-size"] = 25
    assert style_digest(validate_typography(edited, tmp_path, {}), "body") != digest


def test_generated_document_explains_roles_evidence_and_effects(tmp_path, typography):
    current = validate_typography(typography, tmp_path, REFERENCES)
    current["styles"]["body"]["font_path"] = "fonts/fixture.ttf"
    document = render_typography("演示项目", current).decode("utf-8")
    for value in ("演示项目", "1920 × 1080", "字号档位", "`regular` | 24", "用于多行帮助说明",
                  "fonts/fixture.ttf", "行高为 32 px，字距为 -0.5 px",
                  "#FFAA11", "#2233AA", "#3322BB", "偏移 (-2, 3.5) px", "模糊 4 px",
                  "sample", "[10, 20, 40, 60]", "[400, 200]", "正文的行距", "保留多行说明"):
        assert value in document
    assert "尚未定义" in render_typography("演示项目", None).decode("utf-8")
