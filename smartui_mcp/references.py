"""只读参考图登记、原始像素取样与预览缓存。"""

from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from threading import Lock
from io import BytesIO
import os

from PIL import Image

from .layout import ProjectLayout


MAX_REFERENCE_PIXELS = 650_000_000
_IMAGE_LOCK = Lock()


def _absolute_path(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} 必须是绝对路径：{value}")
    return path.resolve(strict=True)


def _reference_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("reference_id 必须以英文字母或数字开头，且只能包含英文字母、数字、下划线、点和连字符")
    return value


def _sha256(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _open_image(source: Path):
    # 大型 PNG 需要明确的像素上限；整个解码过程串行，退出后恢复 Pillow 全局配置。
    with _IMAGE_LOCK:
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_REFERENCE_PIXELS
        try:
            with Image.open(source) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError(f"参考图只支持 PNG、JPEG、WebP：{source}；实际格式={image.format}")
                if image.width * image.height > MAX_REFERENCE_PIXELS:
                    raise ValueError(f"参考图像素数超过上限 {MAX_REFERENCE_PIXELS}：{source}；尺寸={image.size}")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError(f"参考图不支持动画：{source}")
                if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
                    raise ValueError(f"参考图不支持此像素模式：{source}；mode={image.mode}，请提供 8 位灰度或 RGB 图像")
                yield image
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit


def _preview(project_root: Path, image: Image.Image, filename: str) -> str:
    cache = project_root / ".smartui" / "cache" / "references"
    ProjectLayout(project_root)._check_managed_path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    # 调色板与灰度图转为可预览的 RGBA，保留透明度，不叠加背景。
    with image.convert("RGBA") as preview:
        with BytesIO() as stream:
            preview.save(stream, format="PNG")
            payload = stream.getvalue()
    path = cache / f"{filename}-{hashlib.sha256(payload).hexdigest()}.png"
    if not path.is_file() or path.read_bytes() != payload:
        with TemporaryDirectory(dir=_temporary_directory(project_root)) as temporary:
            prepared = Path(temporary) / "preview.png"
            prepared.write_bytes(payload)
            os.replace(prepared, path)
    return str(path)


def _temporary_directory(root: Path) -> Path:
    directory = root / ".smartui" / "temp"
    ProjectLayout(root)._check_managed_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def register_reference(project_root: str, reference_id: str, source_path: str) -> dict:
    """登记工程内外的参考图，按原始字节复制到项目受管目录。"""
    root = _absolute_path(project_root, "project_root")
    if not root.is_dir():
        raise ValueError(f"project_root 必须是目录：{root}")
    identifier = _reference_id(reference_id)
    source = _absolute_path(source_path, "source_path")
    layout = ProjectLayout(root)
    destination = layout.persistent(f"references/{identifier}")
    archived = destination / source.name
    layout._check_managed_path(archived)
    if destination.exists():
        raise ValueError(f"参考图目录已存在，不能覆盖：{destination}")
    with TemporaryDirectory(dir=_temporary_directory(root)) as temp:
        staging = Path(temp) / "reference"
        staging.mkdir()
        snapshot = staging / source.name
        shutil.copyfile(source, snapshot)
        digest = _sha256(snapshot)
        with _open_image(snapshot) as image:
            image.load()
            result = {
                "id": identifier,
                "source": str(archived),
                "sha256": digest,
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "color_metadata": {
                    "has_icc_profile": bool(image.info.get("icc_profile")),
                    "gamma": image.info.get("gamma"),
                    "srgb": image.info.get("srgb"),
                },
            }
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    return result


def _size(value: list[int], label: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int or v <= 0 for v in value):
        raise ValueError(f"{label} 必须是两个正整数：[width, height]")
    return value


def _source_rect(rect: list[int], coordinate_size: list[int], source_size: list[int], label: str) -> list[int]:
    if not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect):
        raise ValueError(f"{label} 必须是四个整数：[x, y, width, height]")
    x, y, width, height = rect
    cw, ch = coordinate_size
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > cw or y + height > ch:
        raise ValueError(f"{label} 必须位于 coordinate_size={coordinate_size} 内，且宽高为正：{rect}")
    sw, sh = source_size
    # 左上角向下取整、右下角向上取整，包含指定坐标矩形覆盖的所有源像素。
    left, top = x * sw // cw, y * sh // ch
    right = ((x + width) * sw + cw - 1) // cw
    bottom = ((y + height) * sh + ch - 1) // ch
    return [left, top, right - left, bottom - top]


def _box(rect: list[int]) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    return x, y, x + width, y + height


def _median(histogram: list[int], count: int) -> int | float:
    positions = [(count - 1) // 2, count // 2]
    values = []
    cumulative = 0
    for value, frequency in enumerate(histogram):
        cumulative += frequency
        while positions and cumulative > positions[0]:
            values.append(value)
            positions.pop(0)
    median = sum(values) / 2
    return int(median) if median.is_integer() else median


def _sample(image: Image.Image, identifier: str, rect: list[int]) -> dict:
    with image.crop(_box(rect)) as crop, crop.convert("RGBA") as rgba:
        histogram = rgba.histogram()
        count = rgba.width * rgba.height
        rgb = [_median(histogram[channel * 256:(channel + 1) * 256], count) for channel in range(3)]
        extrema = rgba.getextrema()
    return {
        "id": identifier,
        "rect_source_xywh": rect,
        "rgb_median": rgb,
        "hex": "#" + "".join(f"{math.floor(value + 0.5):02X}" for value in rgb),
        "channel_range": [list(extrema[channel]) for channel in range(3)],
        "alpha_minmax": list(extrema[3]),
    }


def inspect_reference(
    project_root: str,
    reference: dict,
    region: list[int] | None = None,
    patches: list[dict] | None = None,
    coordinate_size: list[int] | None = None,
) -> dict:
    """取样未经 ICC 转换的源像素，RGB 包含透明像素的存储值，透明度另行报告。

    region 和 patches 都使用全图坐标；coordinate_size 省略时使用源图像素。
    region 仅控制预览范围。RGB 偶数样本中位数可为半整数，hex 四舍五入。
    """
    root = _absolute_path(project_root, "project_root")
    if not root.is_dir():
        raise ValueError(f"project_root 必须是目录：{root}")
    identifier = _reference_id(reference["id"])
    source = _absolute_path(reference["source"], "reference.source")
    digest = _sha256(source)
    if digest != reference["sha256"]:
        raise ValueError(f"参考图源文件 SHA-256 与登记记录不一致，请恢复原始文件：{source}")
    with _open_image(source) as image:
        source_size = [image.width, image.height]
        if source_size != [reference["width"], reference["height"]]:
            raise ValueError(f"参考图登记尺寸与源文件不一致：{source}")
        coordinates = source_size if coordinate_size is None else _size(coordinate_size, "coordinate_size")
        preview_rect = [0, 0, *source_size] if region is None else _source_rect(region, coordinates, source_size, "region")
        if patches is None:
            patches = []
        if not isinstance(patches, list):
            raise ValueError("patches 必须是包含 {id, rect} 的列表")
        mapped_patches = []
        for patch in patches:
            if not isinstance(patch, dict) or not isinstance(patch.get("id"), str) or not patch["id"].strip():
                raise ValueError("每个 patch 必须包含非空字符串 id 和 rect")
            mapped_patches.append((patch["id"], _source_rect(patch.get("rect"), coordinates, source_size, f"patch[{patch['id']}].rect")))
        samples = [_sample(image, patch_id, rect) for patch_id, rect in mapped_patches]
        filename = identifier
        if region is None:
            preview = _preview(root, image, filename)
        else:
            with image.crop(_box(preview_rect)) as crop:
                preview = _preview(root, crop, filename)
    return {
        "reference_id": identifier,
        "source_sha256": digest,
        "source_size": source_size,
        "coordinate_size": coordinates,
        "region_source_xywh": preview_rect,
        "preview": preview,
        "samples": samples,
    }
