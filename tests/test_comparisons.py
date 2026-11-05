from contextlib import contextmanager
import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from smartui_mcp import comparisons
from smartui_mcp.comparisons import RUN_FIELDS, build_comparison
from smartui_mcp.references import register_reference


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def inputs(tmp_path):
    reference_path = tmp_path / "reference.png"
    artifact_path = tmp_path / "run-preview.png"
    with Image.new("RGBA", (9, 15), (30, 80, 120, 0)) as image:
        image.putpixel((2, 2), (120, 200, 250, 128))
        image.save(reference_path)
    with Image.new("RGBA", (50, 20), (220, 80, 60, 0)) as image:
        image.save(artifact_path)
    reference = register_reference(str(tmp_path), "source", str(reference_path))
    run = {
        "run_id": "drawing-run",
        "status": "verified",
        "style_sha256": "3" * 64,
        "spec_sha256": "1" * 64,
        "program_id": "drawing-program",
        "program_sha256": "2" * 64,
        "preview": str(artifact_path),
        "artifact_hashes": {"preview": sha256(artifact_path)},
    }
    return reference, run


def test_comparison_uses_source_regions_and_independent_display_scales(tmp_path, inputs):
    reference, run = inputs
    source_paths = [Path(reference["source"]), Path(run["preview"])]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in source_paths]

    result = build_comparison(
        str(tmp_path), "example", reference, run,
        reference_region=[1, 1, 2, 2], reference_coordinate_size=[4, 6], reference_scale=3,
        artifact_region=[1, 2, 40, 12], artifact_scale=0.5,
    )

    assert result["id"] == "example"
    assert result["reference"]["id"] == "source"
    assert result["reference"]["sha256"] == reference["sha256"]
    assert result["reference"]["source_size"] == [9, 15]
    assert result["reference"]["coordinate_size"] == [4, 6]
    assert result["reference"]["region_source_xywh"] == [2, 2, 5, 6]
    assert result["reference"]["scale"] == 3
    assert result["reference"]["display_size"] == [15, 18]
    assert result["reference"]["color_metadata"] == reference["color_metadata"]
    assert result["artifact"]["source_size"] == [50, 20]
    assert result["artifact"]["region_source_xywh"] == [1, 2, 40, 12]
    assert result["artifact"]["scale"] == 0.5
    assert result["artifact"]["display_size"] == [20, 6]
    assert result["artifact"]["sha256"] == run["artifact_hashes"]["preview"]
    for key in RUN_FIELDS:
        assert result["artifact"][key] == run[key]

    images = {key: Path(image["path"]) for key, image in result["images"].items()}
    for key, path in images.items():
        assert path.parent == tmp_path / "smart-ui" / "previews" / "comparisons" / "example"
        assert sha256(path) == result["images"][key]["sha256"]
    with Image.open(images["reference"]) as image:
        assert image.mode == "RGBA"
        assert image.size == (5, 6)
        assert image.getpixel((0, 0)) == (120, 200, 250, 128)
        assert image.getpixel((4, 5)) == (30, 80, 120, 0)
    with Image.open(images["artifact"]) as image:
        assert image.mode == "RGBA"
        assert image.size == (40, 12)
        assert image.getpixel((0, 0)) == (220, 80, 60, 0)
    display = result["display"]
    assert display["icc_conversion"] is False
    x, y = display["artifact_xy"]
    assert x > display["reference_xy"][0]
    assert y == display["reference_xy"][1]
    with Image.open(images["comparison"]) as image:
        assert image.mode == "RGB"
        assert list(image.size) == display["size"]
        assert image.getpixel((x, y)) == (255, 255, 255)
        assert image.getpixel((x + display["background"]["cell_size"], y)) == (221, 221, 221)
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in source_paths] == before


@pytest.mark.parametrize("target", ["reference", "artifact"])
def test_changed_source_hash_prevents_comparison(tmp_path, inputs, target):
    reference, run = inputs
    source = reference["source"] if target == "reference" else run["preview"]
    with Image.new("RGBA", (9, 15), "red") as image:
        image.save(source)

    with pytest.raises(ValueError, match="SHA-256"):
        build_comparison(str(tmp_path), "example", reference, run)
    assert not (tmp_path / "smart-ui" / "previews" / "comparisons").exists()


@pytest.mark.parametrize("memory_limit", [1, comparisons.SOURCE_SNAPSHOT_MEMORY_LIMIT])
def test_decode_uses_the_same_bytes_as_hash_check(tmp_path, inputs, monkeypatch, memory_limit):
    reference, run = inputs
    source_path = Path(reference["source"])
    original = source_path.read_bytes()
    with Image.new("RGBA", (9, 15), "blue") as image, BytesIO() as buffer:
        image.save(buffer, format="PNG")
        replacement = buffer.getvalue()
    open_image = comparisons._open_image
    replaced = False

    @contextmanager
    def replace_reference_during_decode(source):
        nonlocal replaced
        is_reference = not replaced
        if is_reference:
            source_path.write_bytes(replacement)
            replaced = True
        try:
            with open_image(source) as image:
                yield image
        finally:
            if is_reference:
                source_path.write_bytes(original)

    monkeypatch.setattr(comparisons, "SOURCE_SNAPSHOT_MEMORY_LIMIT", memory_limit)
    monkeypatch.setattr(comparisons, "_open_image", replace_reference_during_decode)
    result = build_comparison(str(tmp_path), "example", reference, run)

    assert result["reference"]["sha256"] == sha256(source_path)
    with Image.open(result["images"]["reference"]["path"]) as image:
        assert image.getpixel((2, 2)) == (120, 200, 250, 128)


@pytest.mark.parametrize("field, value, message", [
    ("reference_width", 8, "登记尺寸"),
    ("status", "failed", "绘制成功"),
    ("artifact_hashes", {}, "缺少预览图的 SHA-256"),
])
def test_ineligible_inputs_are_rejected(tmp_path, inputs, field, value, message):
    reference, run = inputs
    if field == "reference_width":
        reference["width"] = value
    else:
        run[field] = value

    with pytest.raises(ValueError, match=message):
        build_comparison(str(tmp_path), "example", reference, run)


@pytest.mark.parametrize("options, message", [
    ({"reference_coordinate_size": [0, 15]}, "reference_coordinate_size"),
    ({"reference_region": [0, 0, 2, 1], "reference_coordinate_size": [1, 1]}, "region"),
    ({"artifact_region": [49, 0, 2, 1]}, "region"),
    ({"artifact_region": [0, 0, True, 1]}, "region"),
    ({"reference_scale": float("nan")}, "reference_scale"),
    ({"reference_scale": float("inf")}, "reference_scale"),
    ({"artifact_scale": 0}, "artifact_scale"),
    ({"artifact_scale": -1}, "artifact_scale"),
    ({"artifact_scale": True}, "artifact_scale"),
    ({"artifact_scale": "1"}, "artifact_scale"),
    ({"artifact_scale": 10**400}, "artifact_scale"),
    ({"reference_scale": 0.001}, "至少为 1 像素"),
    ({"reference_scale": 1000}, "输出尺寸超过上限"),
    ({"reference_scale": 500, "reference_region": [0, 0, 9, 1],
      "artifact_scale": 500, "artifact_region": [0, 0, 8, 1]}, "对照预览的输出尺寸超过上限"),
])
def test_invalid_options_fail_without_clipping_or_creating_outputs(tmp_path, inputs, options, message):
    with pytest.raises(ValueError, match=message):
        build_comparison(str(tmp_path), "example", *inputs, **options)
    assert not (tmp_path / "smart-ui" / "previews" / "comparisons").exists()


def test_same_id_replaces_comparison_after_successful_staging(tmp_path, inputs, monkeypatch):
    original = build_comparison(str(tmp_path), "example", *inputs)
    save = Image.Image.save

    def fail_artifact(image, path, *args, **kwargs):
        if Path(path).name == "artifact.png":
            raise OSError("无法写入产物裁切图")
        return save(image, path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Image.Image, "save", fail_artifact)
        with pytest.raises(OSError, match="无法写入产物裁切图"):
            build_comparison(str(tmp_path), "example", *inputs, reference_scale=2)
    for info in original["images"].values():
        assert sha256(Path(info["path"])) == info["sha256"]

    updated = build_comparison(str(tmp_path), "example", *inputs, reference_scale=2)
    assert updated["id"] == original["id"]
    assert updated["images"]["comparison"]["path"] == original["images"]["comparison"]["path"]
    assert updated["images"]["comparison"]["sha256"] != original["images"]["comparison"]["sha256"]
    assert not list((tmp_path / ".smartui" / "temp").iterdir())
