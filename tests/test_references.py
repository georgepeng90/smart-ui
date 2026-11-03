import hashlib
from pathlib import Path
import struct
import zlib

from PIL import Image, PngImagePlugin
import pytest

from smartui_mcp.references import _open_image, inspect_reference, register_reference


def make_reference(tmp_path, mode="RGB", size=(4, 4), color=(20, 40, 60)):
    source = tmp_path / "source.png"
    with Image.new(mode, size, color) as image:
        image.save(source)
    return source, register_reference(str(tmp_path), "reference", str(source))


def test_registration_preserves_readonly_source_and_reports_metadata(tmp_path):
    source = tmp_path / "source.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add(b"gAMA", struct.pack(">I", 45455))
    with Image.new("RGB", (40, 80), (20, 40, 60)) as image:
        image.save(source, pnginfo=metadata, icc_profile=b"test ICC metadata")
    before = source.read_bytes()
    before_stat = source.stat()
    source.chmod(0o444)
    try:
        result = register_reference(str(tmp_path), "slime", str(source))
    finally:
        source.chmod(0o666)
    assert source.read_bytes() == before
    assert source.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert result["source"] == str(source.resolve())
    assert result["sha256"] == hashlib.sha256(before).hexdigest()
    assert (result["width"], result["height"], result["mode"]) == (40, 80, "RGB")
    assert result["color_metadata"] == {"has_icc_profile": True, "gamma": 0.45455, "srgb": None}
    preview = Path(result["preview"])
    assert preview.is_absolute()
    assert preview.parent == tmp_path / ".smartui" / "cache" / "references"
    with Image.open(preview) as image:
        assert image.size == (40, 80)
    again = register_reference(str(tmp_path), "slime", str(source))
    assert again["preview"] != result["preview"]
    assert preview.is_file()


def test_srgb_rendering_intent_is_reported(tmp_path):
    source = tmp_path / "source.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add(b"sRGB", b"\x00")
    with Image.new("RGB", (4, 4)) as image:
        image.save(source, pnginfo=metadata)
    result = register_reference(str(tmp_path), "srgb", str(source))
    assert result["color_metadata"] == {"has_icc_profile": False, "gamma": None, "srgb": 0}


def test_samples_use_source_pixels_instead_of_downscaled_preview(tmp_path):
    source = tmp_path / "source.png"
    with Image.new("RGB", (2600, 2), "blue") as image:
        image.putpixel((1300, 0), (255, 0, 0))
        image.save(source)
    reference = register_reference(str(tmp_path), "detail", str(source))
    result = inspect_reference(str(tmp_path), reference, patches=[{"id": "pixel", "rect": [1300, 0, 1, 1]}])
    assert result["samples"] == [{
        "id": "pixel", "rect_source_xywh": [1300, 0, 1, 1],
        "rgb_median": [255, 0, 0], "hex": "#FF0000",
        "channel_range": [[255, 255], [0, 0], [0, 0]], "alpha_minmax": [255, 255],
    }]
    assert result["source_size"] == [2600, 2]
    assert result["coordinate_size"] == [2600, 2]
    with Image.open(result["preview"]) as preview:
        assert max(preview.size) == 1280


def test_coordinate_mapping_includes_covered_pixels_and_preview_region(tmp_path):
    _, reference = make_reference(tmp_path, size=(9, 15))
    result = inspect_reference(
        str(tmp_path), reference, region=[1, 1, 2, 2],
        patches=[{"id": "scaled", "rect": [1, 1, 2, 2]}], coordinate_size=[4, 6],
    )
    assert result["coordinate_size"] == [4, 6]
    assert result["region_source_xywh"] == [2, 2, 5, 6]
    assert result["samples"][0]["rect_source_xywh"] == [2, 2, 5, 6]
    with Image.open(result["preview"]) as preview:
        assert preview.size == (5, 6)


@pytest.mark.parametrize("rect", [[-1, 0, 1, 1], [0, 0, 0, 1], [0, 0, 1, -1], [3, 0, 2, 1], [0, 4, 1, 1], [0, 0, 1.0, 1], [False, 0, 1, 1], [0, 0, 1]])
def test_invalid_regions_and_patches_fail_without_clipping(tmp_path, rect):
    _, reference = make_reference(tmp_path)
    with pytest.raises(ValueError, match="region"):
        inspect_reference(str(tmp_path), reference, region=rect)
    with pytest.raises(ValueError, match="patch"):
        inspect_reference(str(tmp_path), reference, patches=[{"id": "bad", "rect": rect}])


@pytest.mark.parametrize("coordinates", [[0, 4], [4, -1], [4.0, 4], [True, 4], [4]])
def test_invalid_coordinate_size_fails(tmp_path, coordinates):
    _, reference = make_reference(tmp_path)
    with pytest.raises(ValueError, match="coordinate_size"):
        inspect_reference(str(tmp_path), reference, coordinate_size=coordinates)


def test_source_change_requires_registration_again(tmp_path):
    source, reference = make_reference(tmp_path)
    with Image.new("RGB", (4, 4), "red") as image:
        image.save(source)
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_reference(str(tmp_path), reference)


def test_alpha_and_stored_rgb_are_reported_without_background_compositing(tmp_path):
    source = tmp_path / "source.png"
    with Image.new("RGBA", (2, 1)) as image:
        image.putdata([(200, 20, 10, 0), (100, 40, 90, 128)])
        image.save(source)
    reference = register_reference(str(tmp_path), "alpha", str(source))
    result = inspect_reference(str(tmp_path), reference, patches=[{"id": "both", "rect": [0, 0, 2, 1]}])
    sample = result["samples"][0]
    assert sample["rgb_median"] == [150, 30, 50]
    assert sample["hex"] == "#961E32"
    assert sample["channel_range"] == [[100, 200], [20, 40], [10, 90]]
    assert sample["alpha_minmax"] == [0, 128]
    with Image.open(result["preview"]) as preview:
        assert preview.getpixel((0, 0)) == (200, 20, 10, 0)


def test_palette_transparency_and_fractional_median(tmp_path):
    source = tmp_path / "palette.png"
    with Image.new("P", (2, 1)) as image:
        image.putpalette([0, 0, 0, 1, 3, 5] + [0] * (768 - 6))
        image.putdata([0, 1])
        image.save(source, transparency=0)
    reference = register_reference(str(tmp_path), "palette", str(source))
    sample = inspect_reference(str(tmp_path), reference, patches=[{"id": "both", "rect": [0, 0, 2, 1]}])["samples"][0]
    assert sample["rgb_median"] == [0.5, 1.5, 2.5]
    assert sample["hex"] == "#010203"
    assert sample["alpha_minmax"] == [0, 255]


@pytest.mark.parametrize("suffix,format", [("jpg", "JPEG"), ("webp", "WEBP")])
def test_jpeg_and_webp_registration(tmp_path, suffix, format):
    source = tmp_path / f"source.{suffix}"
    with Image.new("RGB", (4, 4), "green") as image:
        image.save(source, format=format)
    reference = register_reference(str(tmp_path), "supported", str(source))
    assert (reference["width"], reference["height"]) == (4, 4)


def test_unsupported_format_animation_and_pixel_mode_fail(tmp_path):
    source = tmp_path / "source.bmp"
    with Image.new("RGB", (4, 4)) as image:
        image.save(source)
    with pytest.raises(ValueError, match="只支持 PNG"):
        register_reference(str(tmp_path), "unsupported", str(source))
    source = tmp_path / "animation.png"
    with Image.new("RGBA", (4, 4), "red") as first, Image.new("RGBA", (4, 4), "blue") as second:
        first.save(source, save_all=True, append_images=[second], duration=100, loop=0)
    with pytest.raises(ValueError, match="动画"):
        register_reference(str(tmp_path), "animated", str(source))
    source = tmp_path / "sixteen.png"
    with Image.new("I;16", (4, 4), 1000) as image:
        image.save(source)
    with pytest.raises(ValueError, match="像素模式"):
        register_reference(str(tmp_path), "sixteen", str(source))


def test_image_limit_is_scoped_and_restored_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    make_reference(tmp_path)
    assert Image.MAX_IMAGE_PIXELS == 1
    source = tmp_path / "source.bmp"
    with Image.new("RGB", (4, 4)) as image:
        image.save(source)
    with pytest.raises(ValueError):
        register_reference(str(tmp_path), "unsupported", str(source))
    assert Image.MAX_IMAGE_PIXELS == 1


def png_header(width, height):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))


def test_actual_large_reference_dimensions_pass_header_validation_without_decoding(tmp_path):
    source = tmp_path / "large-header.png"
    source.write_bytes(png_header(18432, 32768))
    previous_limit = Image.MAX_IMAGE_PIXELS
    with _open_image(source) as image:
        assert image.size == (18432, 32768)
    assert Image.MAX_IMAGE_PIXELS == previous_limit


def test_images_above_pixel_limit_fail_before_decoding(tmp_path):
    source = tmp_path / "too-large.png"
    source.write_bytes(png_header(26000, 26000))
    with pytest.warns(Image.DecompressionBombWarning), pytest.raises(ValueError, match="像素数超过上限"):
        register_reference(str(tmp_path), "too-large", str(source))


def test_paths_must_be_absolute_and_ids_must_be_safe(tmp_path):
    source, _ = make_reference(tmp_path)
    with pytest.raises(ValueError, match="绝对路径"):
        register_reference("relative", "sample", str(source))
    with pytest.raises(ValueError, match="绝对路径"):
        register_reference(str(tmp_path), "sample", "relative.png")
    with pytest.raises(FileNotFoundError):
        register_reference(str(tmp_path), "sample", str(tmp_path / "missing.png"))
    with pytest.raises(ValueError, match="reference_id"):
        register_reference(str(tmp_path), "../outside", str(source))
