"""验证独立图像原字节归档、版本完整性和工程路径边界。"""

import hashlib
import os
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from smartui_mcp.image_assets import register_image_file, verify_image_version


PROVENANCE = {"kind": "generated", "tool": "合成测试", "prompt": "透明图标", "actual_result": {"seed": None}}


def source_image(tmp_path, mode="RGBA", color=(20, 40, 60, 0), format="PNG"):
    source = tmp_path / "source.data"
    with Image.new(mode, (8, 6), color) as image:
        image.save(source, format=format)
    return source


def register(tmp_path, source):
    return register_image_file(str(tmp_path), {}, "coin", 1, str(source), PROVENANCE)


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
    assert path == tmp_path / f"art/ui/sources/images/coin/1/image.{extension}"
    assert path.read_bytes() == source.read_bytes() == original
    assert record["sha256"] == hashlib.sha256(original).hexdigest()
    assert (record["width"], record["height"]) == (8, 6)
    assert record["has_transparency"] is transparent
    assert record["provenance"] == PROVENANCE
    assert record["provenance"] is not PROVENANCE
    source.unlink()
    verify_image_version(record)


def test_version_conflict_preserves_existing_file(tmp_path):
    source = source_image(tmp_path)
    record = register(tmp_path, source)
    before = Path(record["path"]).read_bytes()
    with pytest.raises(FileExistsError, match="版本已存在"):
        register(tmp_path, source)
    assert Path(record["path"]).read_bytes() == before


def test_palette_transparency_is_preserved(tmp_path):
    source = tmp_path / "palette.png"
    with Image.new("P", (4, 4), 0) as image:
        image.save(source, transparency=0)
    record = register(tmp_path, source)
    assert record["mode"] == "P"
    assert record["has_transparency"] is True
    assert Path(record["path"]).read_bytes() == source.read_bytes()


def test_changed_copy_does_not_publish_version(tmp_path, monkeypatch):
    from smartui_mcp import image_assets

    source = source_image(tmp_path)
    original_copy = image_assets.shutil.copyfileobj

    def changed_copy(original, output):
        original_copy(original, output)
        output.write(b"changed during copy")

    monkeypatch.setattr(image_assets.shutil, "copyfileobj", changed_copy)
    with pytest.raises(ValueError, match="SHA-256"):
        register(tmp_path, source)
    parent = tmp_path / "art/ui/sources/images/coin"
    assert not list(parent.iterdir())


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
        register_image_file(str(tmp_path), {}, "coin", 1, str(source), provenance)
    assert not (tmp_path / "art").exists()


def test_imported_image_needs_description_without_generated_prompt(tmp_path):
    source = source_image(tmp_path)
    provenance = {"kind": "imported", "description": "从已有基础 UI 导出的 PNG"}
    record = register_image_file(str(tmp_path), {}, "button", 1, str(source), provenance)
    assert record["provenance"] == provenance
    assert "prompt" not in record["provenance"]
    assert "tool" not in record["provenance"]
    verify_image_version(record)


@pytest.mark.parametrize(("identifier", "version"), [("../coin", 1), ("coin", 0), ("coin", True)])
def test_invalid_identifier_or_version_does_not_write(tmp_path, identifier, version):
    source = source_image(tmp_path)
    with pytest.raises(ValueError):
        register_image_file(str(tmp_path), {}, identifier, version, str(source), PROVENANCE)
    assert not (tmp_path / "art").exists()


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
    assert not (tmp_path / "art").exists()


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
    with pytest.raises(ValueError, match="版本校验失败") as error:
        verify_image_version(record)
    assert str(path) in str(error.value)


def test_custom_directory_and_path_boundary(tmp_path):
    source = source_image(tmp_path)
    record = register_image_file(str(tmp_path), {"sources": "design/sources"}, "coin", 1, str(source), PROVENANCE)
    assert Path(record["path"]).is_relative_to(tmp_path / "design/sources")
    for paths in ({"sources": "../outside"}, {"sources": ".smartui/assets"}):
        with pytest.raises(ValueError, match="paths.sources"):
            register_image_file(str(tmp_path), paths, "other", 1, str(source), PROVENANCE)
    with pytest.raises(ValueError, match="绝对路径"):
        register_image_file(str(tmp_path), {}, "other", 1, "source.png", PROVENANCE)


def test_linked_destination_is_rejected(tmp_path):
    source = source_image(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    parent = tmp_path / "art/ui/sources/images"
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
