"""将登记参考图与当前绘制产物生成并排对照。"""

from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import re
from tempfile import SpooledTemporaryFile, TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont

from .layout import ProjectLayout
from .references import _absolute_path, _box, _open_image, _sha256, _size, _source_rect, _temporary_directory


MAX_OUTPUT_EDGE = 8192
MAX_OUTPUT_PIXELS = 32_000_000
SOURCE_SNAPSHOT_MEMORY_LIMIT = 16 * 1024 * 1024
CHECKER_SIZE = 12
CHECKER_COLORS = ("#FFFFFF", "#DDDDDD")
PADDING = 12
LABEL_HEIGHT = 20
RUN_FIELDS = ("run_id", "style_sha256", "program_id", "program_sha256", "spec_sha256")


def _check_output_size(size: tuple[int, int], label: str) -> None:
    width, height = size
    if width < 1 or height < 1:
        raise ValueError(f"{label}的输出宽高必须至少为 1 像素：{width}×{height}")
    if max(size) > MAX_OUTPUT_EDGE or width * height > MAX_OUTPUT_PIXELS:
        raise ValueError(f"{label}的输出尺寸超过上限（每边 {MAX_OUTPUT_EDGE} 像素，总计 {MAX_OUTPUT_PIXELS} 像素）：{width}×{height}")


def _check_scale(scale: float, label: str) -> None:
    if type(scale) not in (int, float) or not 0 < scale < math.inf:
        raise ValueError(f"{label} 必须是有限正数")
    if scale > MAX_OUTPUT_EDGE:
        raise ValueError(f"{label} 超过上限 {MAX_OUTPUT_EDGE}")


@contextmanager
def _open_verified_image(source: Path, expected_hash: str, label: str, temp_dir: Path):
    try:
        with SpooledTemporaryFile(max_size=SOURCE_SNAPSHOT_MEMORY_LIMIT, dir=temp_dir) as snapshot:
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    snapshot.write(chunk)
            if digest.hexdigest() != expected_hash:
                raise ValueError("SHA-256 与记录不一致")
            # 摘要和解码来自同一份字节，源路径随后被替换也不会改变这次裁切。
            snapshot.seek(0)
            with _open_image(snapshot) as image:
                yield image
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError(f"无法生成{label}的裁切图：{source}；{error}") from error


@contextmanager
def _crop_source(
    source: Path, expected_hash: str, region: list[int] | None, scale: float, *,
    label: str, temp_dir: Path, coordinate_size: list[int] | None = None, expected_size: list[int] | None = None,
):
    with _open_verified_image(source, expected_hash, label, temp_dir) as image:
        source_size = [image.width, image.height]
        if expected_size is not None and source_size != expected_size:
            raise ValueError(f"参考图登记尺寸与源文件不一致，记录 {expected_size}，实际 {source_size}")
        coordinates = source_size if coordinate_size is None else coordinate_size
        rect = [0, 0, *source_size] if region is None else _source_rect(region, coordinates, source_size, f"{label}的 region")
        width, height = rect[2:]
        display_size = (math.floor(width * scale + 0.5), math.floor(height * scale + 0.5))
        _check_output_size((width, height), f"{label}裁切快照")
        _check_output_size(display_size, f"{label}按 scale={scale:g} 缩放后的图像")
        metadata = {
            "source": str(source),
            "sha256": expected_hash,
            "source_size": source_size,
            "coordinate_size": coordinates,
            "region_source_xywh": rect,
            "scale": scale,
            "display_size": list(display_size),
            "color_metadata": {
                "has_icc_profile": bool(image.info.get("icc_profile")),
                "gamma": image.info.get("gamma"),
                "srgb": image.info.get("srgb"),
            },
        }
        with image.crop(_box(rect)) as crop:
            rgba = crop.convert("RGBA")

    # 释放参考图解码锁后才交付裁切图，两个输入可依次解码。
    with rgba:
        yield rgba, metadata


def _compose(reference: Image.Image, artifact: Image.Image, ref: dict, out: dict) -> tuple[Image.Image, dict]:
    font = ImageFont.load_default()
    panels = []
    for name, image, metadata in (("REF", reference, ref), ("OUTPUT", artifact, out)):
        width, height = metadata["display_size"]
        caption = f"{name}  {image.width}x{image.height} -> {width}x{height}  @ {metadata['scale']:g}x"
        panels.append((image, caption, width, height, max(width, math.ceil(font.getlength(caption)))))
    size = (sum(panel[4] for panel in panels) + PADDING * 3,
            max(panel[3] for panel in panels) + PADDING * 2 + LABEL_HEIGHT)
    _check_output_size(size, "对照预览")

    comparison = Image.new("RGB", size, "#F4F4F4")
    draw = ImageDraw.Draw(comparison)
    left, top = PADDING, PADDING + LABEL_HEIGHT
    offsets = []
    for image, caption, width, height, panel_width in panels:
        offsets.append([left, top])
        draw.text((left, PADDING), caption, fill="#202020", font=font)
        draw.rectangle((left, top, left + width - 1, top + height - 1), fill=CHECKER_COLORS[0])
        for y in range(0, height, CHECKER_SIZE):
            first_dark_x = (1 - y // CHECKER_SIZE % 2) * CHECKER_SIZE
            for x in range(first_dark_x, width, CHECKER_SIZE * 2):
                draw.rectangle((left + x, top + y, left + min(x + CHECKER_SIZE, width) - 1,
                                top + min(y + CHECKER_SIZE, height) - 1), fill=CHECKER_COLORS[1])
        with image.resize((width, height), Image.Resampling.LANCZOS) as scaled:
            comparison.paste(scaled, (left, top), scaled)
        left += panel_width + PADDING
    return comparison, {
        "size": list(size),
        "reference_xy": offsets[0],
        "artifact_xy": offsets[1],
        "background": {"type": "checkerboard", "cell_size": CHECKER_SIZE, "colors": list(CHECKER_COLORS)},
        "resampling": "lanczos",
        "icc_conversion": False,
    }


def build_comparison(
    project_root: str,
    comparison_id: str,
    reference: dict,
    run: dict,
    *,
    reference_region: list[int] | None = None,
    reference_coordinate_size: list[int] | None = None,
    artifact_region: list[int] | None = None,
    reference_scale: float = 1.0,
    artifact_scale: float = 1.0,
    paths=None,
    commit=None,
) -> dict:
    """保存当前对照的 RGBA 裁切图和总览，返回文件摘要及坐标。"""
    from .image_assets import _replace_directory

    root = _absolute_path(project_root, "project_root")
    if not root.is_dir():
        raise ValueError(f"project_root 必须是目录：{root}")
    if not isinstance(comparison_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", comparison_id):
        raise ValueError("comparison_id 必须以英文字母或数字开头，且只能包含英文字母、数字、点、下划线和连字符")
    if run.get("status") not in ("drawn", "verified"):
        raise ValueError(f"只有绘制成功的记录可用于对照：run_id={run.get('run_id')}")
    preview_hash = run.get("artifact_hashes", {}).get("preview")
    if not preview_hash:
        raise ValueError(f"绘制记录缺少预览图的 SHA-256：run_id={run['run_id']}")
    if not set(RUN_FIELDS) <= run.keys():
        raise ValueError(f"绘制记录缺少产物来源：run_id={run.get('run_id')}")
    run_metadata = {key: run[key] for key in RUN_FIELDS}
    reference_id = reference["id"]
    _check_scale(reference_scale, "reference_scale")
    _check_scale(artifact_scale, "artifact_scale")
    if reference_coordinate_size is not None:
        reference_coordinate_size = _size(reference_coordinate_size, "reference_coordinate_size")
    reference_path = _absolute_path(reference["source"], "reference.source")
    artifact_path = _absolute_path(run["preview"], "run.preview")
    layout = ProjectLayout(root, paths)
    destination = layout.persistent(f"previews/comparisons/{comparison_id}")
    temp_dir = _temporary_directory(root)

    with (
        _crop_source(reference_path, reference["sha256"], reference_region, reference_scale, label="参考图", temp_dir=temp_dir,
                     coordinate_size=reference_coordinate_size, expected_size=[reference["width"], reference["height"]]) as (ref_image, ref),
        _crop_source(artifact_path, preview_hash, artifact_region, artifact_scale, label="产物预览图", temp_dir=temp_dir) as (out_image, out),
    ):
        comparison, display = _compose(ref_image, out_image, ref, out)
        with comparison, TemporaryDirectory(dir=temp_dir) as temp:
            staging = Path(temp) / "current"
            staging.mkdir()
            images = {}
            for key, image in (("reference", ref_image), ("artifact", out_image), ("comparison", comparison)):
                path = staging / f"{key}.png"
                image.save(path, format="PNG")
                images[key] = {"path": str(destination / path.name), "sha256": _sha256(path)}
            record = {
                "id": comparison_id,
                "reference": {"id": reference_id, **ref},
                "artifact": {**run_metadata, **out},
                "images": images,
                "display": display,
            }
            return _replace_directory(layout, staging, destination,
                                      lambda: commit(record) if commit is not None else record)
