"""验证独立图像按原字节保存、当前文件完整性和工程路径边界。"""

import hashlib
import os
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from smartui_mcp.image_assets import register_image_file, verify_image_record


PROVENANCE = {"kind": "generated", "tool": "合成测试", "prompt": "透明图标", "actual_result": {"seed": None}}


def source_image(tmp_path, mode="RGBA", color=(20, 40, 60, 0), format="PNG"):
    source = tmp_path / "source.data"
    with Image.new(mode, (8, 6), color) as image:
        image.save(source, format=format)
    return source


def register(tmp_path, source):
    return register_image_file(str(tmp_path), {}, "coin", "硬币", str(source), PROVENANCE)


@pytest.mark.parametrize(("mode", "color", "format", "extension", "transparent"), [
    ("RGBA", (20, 40, 60, 0), "PNG", "png", True),
    ("RGBA", (20, 40, 60, 255), "PNG", "png", False),
    ("RGB", (20, 40, 60), "JPEG", "jpg", False),
    ("RGBA", (20, 40, 60, 0), "WEBP", "webp", True),
])
def test_register_preserves_original_bytes_and_actual_format(tmp_path, mode, color, format, extension, transparent):
    source = source_image(tmp_path, mode, color, format)
    original = source.read_bytes()
    record = register(tmp_path, source)
    path = Path(record["path"])
    assert path == tmp_path / f"smart-ui/sources/images/coin/image.{extension}"
    assert path.read_bytes() == source.read_bytes() == original
    assert record["sha256"] == hashlib.sha256(original).hexdigest()
    assert (record["width"], record["height"]) == (8, 6)
    assert record["has_transparency"] is transparent
    assert record["provenance"] == PROVENANCE
    assert record["provenance"] is not PROVENANCE
    source.unlink()
    verify_image_record(record)


def test_register_replaces_the_same_asset_and_removes_the_previous_format(tmp_path):
    first = register(tmp_path, source_image(tmp_path))
    first_path = Path(first["path"])
    source = source_image(tmp_path, "RGB", (20, 40, 60), "JPEG")
    second = register(tmp_path, source)
    path = Path(second["path"])
    assert second["id"] == first["id"] == "coin"
    assert second["name"] == "硬币"
    assert "version" not in second
    assert path.parent == first_path.parent
    assert list(path.parent.iterdir()) == [path]
    assert path.read_bytes() == source.read_bytes()
    verify_image_record(second)


def test_invalid_current_image_cannot_be_overwritten(tmp_path):
    first = register(tmp_path, source_image(tmp_path))
    current = Path(first["path"])
    current.write_bytes(b"changed")
    with pytest.raises(ValueError, match="无法读取图像资产"):
        register(tmp_path, source_image(tmp_path))
    assert current.read_bytes() == b"changed"


def test_palette_transparency_is_preserved(tmp_path):
    source = tmp_path / "palette.png"
    with Image.new("P", (4, 4), 0) as image:
        image.save(source, transparency=0)
    record = register(tmp_path, source)
    assert record["mode"] == "P"
    assert record["has_transparency"] is True
    assert Path(record["path"]).read_bytes() == source.read_bytes()


def test_changed_copy_preserves_the_current_image(tmp_path, monkeypatch):
    from smartui_mcp import image_assets

    source = source_image(tmp_path)
    original = register(tmp_path, source)
    path = Path(original["path"])
    before = path.read_bytes()
    source_image(tmp_path, color=(1, 2, 3, 4))
    original_copy = image_assets.shutil.copyfileobj

    def changed_copy(original, output):
        original_copy(original, output)
        output.write(b"changed during copy")

    monkeypatch.setattr(image_assets.shutil, "copyfileobj", changed_copy)
    with pytest.raises(ValueError, match="SHA-256"):
        register(tmp_path, source)
    assert list(path.parent.iterdir()) == [path]
    assert path.read_bytes() == before


@pytest.mark.parametrize("provenance", [{}, {"kind": "generated", "tool": "", "prompt": "图"},
    {"kind": "generated", "tool": "工具", "prompt": " "},
    {"kind": "generated", "tool": "工具"}, {"kind": "edited", "tool": "工具"},
    {"kind": "imported", "description": " "},
    {"kind": "imported", "description": "现有素材", "prompt": None},
    {"kind": "imported", "description": "现有素材", "tool": 3},
    PROVENANCE | {"cost": float("nan")},
    PROVENANCE | {"result": (1, 2)}, PROVENANCE | {"result": {1: "value"}}])
def test_invalid_provenance_does_not_write(tmp_path, provenance):
    source = source_image(tmp_path)
    with pytest.raises(ValueError, match="provenance"):
        register_image_file(str(tmp_path), {}, "coin", "硬币", str(source), provenance)
    assert not (tmp_path / "smart-ui").exists()


def test_imported_image_needs_description_without_generated_prompt(tmp_path):
    source = source_image(tmp_path)
    provenance = {"kind": "imported", "description": "从已有基础 UI 导出的 PNG"}
    record = register_image_file(str(tmp_path), {}, "button", "按钮", str(source), provenance)
    assert record["provenance"] == provenance
    assert "prompt" not in record["provenance"]
    assert "tool" not in record["provenance"]
    verify_image_record(record)


@pytest.mark.parametrize(("identifier", "name"), [("../coin", "硬币"), ("coin", ""), ("coin", True)])
def test_invalid_identifier_or_name_does_not_write(tmp_path, identifier, name):
    source = source_image(tmp_path)
    with pytest.raises(ValueError):
        register_image_file(str(tmp_path), {}, identifier, name, str(source), PROVENANCE)
    assert not (tmp_path / "smart-ui").exists()


@pytest.mark.parametrize("kind", ["invalid", "gif", "truncated", "oversized"])
def test_invalid_images_do_not_publish(tmp_path, kind):
    source = tmp_path / "source.png"
    if kind == "invalid":
        source.write_bytes(b"not an image")
    else:
        with Image.new("RGB", (8193, 1) if kind == "oversized" else (8, 6)) as image:
            image.save(source, format="GIF" if kind == "gif" else "PNG")
        if kind == "truncated":
            source.write_bytes(source.read_bytes()[:45])
    with pytest.raises(ValueError, match="图像资产"):
        register(tmp_path, source)
    assert not (tmp_path / "smart-ui").exists()


@pytest.mark.parametrize("change", ["bytes", "missing", "width", "mode", "has_transparency"])
def test_verify_rejects_changed_or_missing_evidence(tmp_path, change):
    record = register(tmp_path, source_image(tmp_path))
    path = Path(record["path"])
    if change == "bytes":
        path.write_bytes(b"changed")
    elif change == "missing":
        path.unlink()
    else:
        record[change] = {"width": 9, "mode": "RGB", "has_transparency": False}[change]
    with pytest.raises(ValueError, match="图像资产校验失败") as error:
        verify_image_record(record)
    assert str(path) in str(error.value)


def test_fixed_directory_and_path_boundary(tmp_path):
    source = source_image(tmp_path)
    for paths in ({"sources": "design/sources"}, {"sources": "../outside"}, {"sources": ".smartui/assets"}):
        with pytest.raises(ValueError, match="固定"):
            register_image_file(str(tmp_path), paths, "coin", "硬币", str(source), PROVENANCE)
    with pytest.raises(ValueError, match="绝对路径"):
        register_image_file(str(tmp_path), {}, "coin", "硬币", "source.png", PROVENANCE)


def test_linked_destination_is_rejected(tmp_path):
    source = source_image(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    parent = tmp_path / "smart-ui/sources/images"
    parent.mkdir(parents=True)
    if os.name == "nt":
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "New-Item -ItemType Junction -Path $env:SMARTUI_TEST_LINK "
                        "-Target $env:SMARTUI_TEST_TARGET | Out-Null"],
                       env={**os.environ, "SMARTUI_TEST_LINK": str(parent / "coin"),
                            "SMARTUI_TEST_TARGET": str(external)}, check=True, capture_output=True)
    else:
        (parent / "coin").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接或目录联接"):
        register(tmp_path, source)
    assert not list(external.iterdir())
