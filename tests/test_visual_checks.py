import hashlib
from pathlib import Path

from PIL import Image
import pytest

from smartui_mcp.visual_checks import build_visual_check


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _item(path, *, label="默认状态", region=None, scale=1):
    return {
        "component_id": path.stem,
        "label": label,
        "region": region,
        "scale": scale,
        "run": {
            "run_id": f"{path.stem}-run",
            "status": "drawn",
            "style_sha256": "3" * 64,
            "spec_sha256": "1" * 64,
            "program_id": "drawing-program",
            "program_sha256": "2" * 64,
            "preview": str(path),
            "artifact_hashes": {"preview": _sha256(path)},
        },
    }


@pytest.fixture
def items(tmp_path):
    first, second, third = (tmp_path / f"preview-{index}.png" for index in range(3))
    with Image.new("RGBA", (3, 2), (200, 50, 70, 0)) as image:
        image.putpixel((1, 1), (250, 0, 0, 121))
        image.save(first)
    with Image.new("RGB", (4, 2), (25, 70, 210)) as image:
        image.save(second)
    with Image.new("RGBA", (8, 4), (190, 30, 100, 255)) as image:
        image.save(third)
    return [
        _item(first, label="半透明状态", region=[1, 1, 2, 1]),
        _item(second, label="放大状态", scale=2),
        _item(third, label="缩小状态", scale=0.5),
    ]


def test_visual_check_crops_scales_and_composites_before_grayscale(tmp_path, items):
    source_paths = [Path(item["run"]["preview"]) for item in items]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in source_paths]

    result = build_visual_check(str(tmp_path), "example", items, columns=2, background="#123456")

    entries = result["items"]
    images = {key: Path(info["path"]) for key, info in result["images"].items()}
    assert set(images) == {"item-1", "item-2", "item-3", "overview", "grayscale"}
    for key, path in images.items():
        assert path == tmp_path / "smart-ui" / "previews" / "checks" / "example" / f"{key}.png"
        assert result["images"][key]["sha256"] == _sha256(path)
    for entry, item, path in zip(entries, items, source_paths, strict=True):
        assert entry["label"] == item["label"]
        assert entry["component_id"] == item["component_id"]
        assert entry["source"] == str(path)
        assert entry["sha256"] == _sha256(path)
        assert entry["run"]["run_id"] == item["run"]["run_id"]
        assert entry["run"]["program_sha256"] == item["run"]["program_sha256"]
    assert entries[0]["region_source_xywh"] == [1, 1, 2, 1]
    assert entries[0]["source_size"] == [3, 2]
    assert [entry["display_size"] for entry in entries] == [[2, 1], [8, 4], [4, 2]]
    assert result["display"]["columns"] == 2
    assert result["display"]["background"] == "#123456"
    assert result["display"]["icc_conversion"] is False
    with Image.open(images["item-1"]) as crop:
        assert crop.mode == "RGBA"
        assert crop.size == (2, 1)
        assert [crop.getpixel((x, 0)) for x in range(2)] == [(250, 0, 0, 121), (200, 50, 70, 0)]
    x, y, _, _ = entries[0]["overview_rect_xywh"]
    second_x, second_y, second_width, second_height = entries[1]["overview_rect_xywh"]
    third_x, third_y, _, _ = entries[2]["overview_rect_xywh"]
    assert second_x > x + 2 and second_y == y
    assert third_x == x and third_y > y + second_height
    with Image.open(images["overview"]) as overview, Image.open(images["grayscale"]) as gray:
        assert overview.mode == "RGB" and gray.mode == "L"
        assert list(overview.size) == result["display"]["size"]
        assert overview.size == gray.size
        assert overview.getpixel((x, y)) == (128, 27, 45)
        assert overview.getpixel((x + 1, y)) == (18, 52, 86)
        assert overview.getpixel((second_x + second_width - 1, second_y + second_height - 1)) == (25, 70, 210)
        assert overview.getpixel((third_x, third_y)) == (190, 30, 100)
        # 这个像素先转灰再合成会得到 60，先合成再转灰得到 59。
        assert gray.getpixel((x, y)) == 59
        assert gray.getpixel((x + 1, y)) == 46
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in source_paths] == before


def test_updating_check_omits_grayscale_and_removed_items(tmp_path, items):
    original = build_visual_check(str(tmp_path), "example", items, background="#123456")
    result = build_visual_check(str(tmp_path), "example", items[:1], background="#abcDef", grayscale=False)

    assert result["display"]["columns"] == 1
    assert result["display"]["background"] == "#ABCDEF"
    assert result["display"]["grayscale"] is None
    assert set(result["images"]) == {"item-1", "overview"}
    assert result["images"]["overview"]["path"] == original["images"]["overview"]["path"]
    assert not Path(original["images"]["grayscale"]["path"]).exists()
    assert not Path(original["images"]["item-2"]["path"]).exists()
    assert not Path(original["images"]["item-3"]["path"]).exists()
    assert not list((tmp_path / ".smartui" / "temp").iterdir())


def test_changed_preview_hash_discards_all_staged_items(tmp_path, items):
    with Image.new("RGBA", (4, 2), "red") as image:
        image.save(items[1]["run"]["preview"])

    with pytest.raises(ValueError, match="SHA-256 与记录不一致"):
        build_visual_check(str(tmp_path), "example", items, background="#123456")

    assert not (tmp_path / "smart-ui" / "previews" / "checks").exists()


@pytest.mark.parametrize("field, value, message", [
    ("scale", True, "scale"),
    ("scale", float("nan"), "scale"),
    ("scale", 0, "scale"),
    ("scale", 0.1, "至少为 1 像素"),
    ("region", [0, 0, True, 1], "region"),
    ("region", [0, 0, 5, 1], "region"),
    ("label", None, "label"),
    ("status", "failed", "绘制成功"),
    ("artifact_hashes", {}, "SHA-256"),
])
def test_invalid_items_do_not_publish(tmp_path, items, field, value, message):
    target = items[0]["run"] if field in {"status", "artifact_hashes"} else items[0]
    target[field] = value

    with pytest.raises(ValueError, match=message):
        build_visual_check(str(tmp_path), "example", items, background="#123456")

    assert not (tmp_path / "smart-ui" / "previews" / "checks" / "example").exists()


@pytest.mark.parametrize("options, message", [
    ({"background": "red"}, "background"),
    ({"background": "#AABBCC80"}, "background"),
    ({"columns": True}, "columns"),
    ({"columns": 0}, "columns"),
    ({"columns": 17}, "columns"),
    ({"grayscale": 1}, "grayscale"),
])
def test_invalid_display_options_do_not_publish(tmp_path, items, options, message):
    with pytest.raises(ValueError, match=message):
        build_visual_check(str(tmp_path), "example", items, **({"background": "#123456"} | options))

    assert not (tmp_path / "smart-ui" / "previews" / "checks").exists()


@pytest.mark.parametrize("count", [0, 17])
def test_item_count_is_bounded(tmp_path, items, count):
    with pytest.raises(ValueError, match="items"):
        build_visual_check(str(tmp_path), "example", items[:1] * count, background="#123456")


@pytest.mark.parametrize("scale, count, columns, message", [
    (820, 1, 1, "缩放后的图像的输出尺寸超过上限"),
    (570, 1, 1, "缩放后的图像的输出尺寸超过上限"),
    (450, 2, 2, "视觉检查总览的输出尺寸超过上限"),
    (300, 4, 2, "视觉检查总览的输出尺寸超过上限"),
])
def test_size_limits_apply_to_items_and_grid_before_resizing(tmp_path, monkeypatch, scale, count, columns, message):
    path = tmp_path / "preview.png"
    with Image.new("RGBA", (10, 10), "red") as image:
        image.save(path)

    def fail_resize(*args, **kwargs):
        pytest.fail("输出尺寸超限时不应缩放图像")

    monkeypatch.setattr(Image.Image, "resize", fail_resize)
    with pytest.raises(ValueError, match=message):
        build_visual_check(str(tmp_path), "example", [_item(path, scale=scale)] * count,
                           columns=columns, background="#123456")

    assert not (tmp_path / "smart-ui" / "previews" / "checks").exists()


def test_source_size_limit_applies_before_decoding_even_for_a_small_crop(tmp_path, monkeypatch):
    path = tmp_path / "preview.png"
    with Image.new("RGBA", (8193, 1), "red") as image:
        image.save(path)

    def fail_crop(*args, **kwargs):
        pytest.fail("源图尺寸超限时不应解码并裁切源图")

    monkeypatch.setattr(Image.Image, "crop", fail_crop)
    with pytest.raises(ValueError, match="源预览图的输出尺寸超过上限"):
        build_visual_check(str(tmp_path), "example", [_item(path, region=[0, 0, 1, 1])], background="#123456")

    assert not (tmp_path / "smart-ui" / "previews" / "checks").exists()


def test_failed_grayscale_write_does_not_publish_partial_output(tmp_path, items, monkeypatch):
    save = Image.Image.save

    def fail_grayscale(image, path, *args, **kwargs):
        if Path(path).name == "grayscale.png":
            raise OSError("无法写入灰度图")
        return save(image, path, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", fail_grayscale)
    with pytest.raises(OSError, match="无法写入灰度图"):
        build_visual_check(str(tmp_path), "example", items, background="#123456")

    assert not (tmp_path / "smart-ui" / "previews" / "checks").exists()
