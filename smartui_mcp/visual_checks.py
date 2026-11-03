"""为已记录的绘制产物生成统一背景的视觉检查图。"""

from contextlib import closing, contextmanager
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont

from .comparisons import LABEL_HEIGHT, PADDING, _check_output_size, _check_scale, _open_verified_image
from .references import _absolute_path, _box, _sha256, _source_rect


MAX_ITEMS = 16
RUN_FIELDS = ("run_id", "status", "style_version", "spec_sha256", "program_id", "program_version", "program_sha256")


def _snapshot_item(item: dict, index: int, path: Path) -> dict:
    prefix = f"items[{index - 1}]"
    if not isinstance(item, dict) or not {"component_id", "run", "label", "region", "scale"} <= item.keys():
        raise ValueError(f"{prefix} 必须包含 component_id、run、label、region 和 scale")
    component_id, label = item["component_id"], item["label"]
    if not isinstance(component_id, str) or not component_id.strip():
        raise ValueError(f"{prefix}.component_id 必须是非空字符串")
    if not isinstance(label, str):
        raise ValueError(f"{prefix}.label 必须是字符串")
    run = item["run"]
    if not isinstance(run, dict) or run.get("status") not in ("drawn", "verified"):
        raise ValueError(f"{prefix}.run 必须是绘制成功的记录")
    hashes = run.get("artifact_hashes")
    preview_hash = hashes.get("preview") if isinstance(hashes, dict) else None
    if not isinstance(preview_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", preview_hash):
        raise ValueError(f"{prefix}.run 缺少有效的预览图 SHA-256")
    if not isinstance(run.get("preview"), str) or not set(RUN_FIELDS) <= run.keys():
        raise ValueError(f"{prefix}.run 缺少预览图路径或版本信息")
    scale = item["scale"]
    _check_scale(scale, f"{prefix}.scale")
    source = _absolute_path(run["preview"], f"{prefix}.run.preview")
    with _open_verified_image(source, preview_hash, f"第 {index} 项产物预览图") as image, closing(image):
        # Pillow 裁切时会解码整张预览图，因此也限制源图的尺寸。
        _check_output_size(image.size, f"第 {index} 项源预览图")
        size = list(image.size)
        region = item["region"]
        rect = [0, 0, *size] if region is None else _source_rect(region, size, size, f"{prefix}.region")
        width, height = rect[2:]
        display_size = [math.floor(edge * scale + 0.5) for edge in (width, height)]
        _check_output_size((width, height), f"第 {index} 项裁切快照")
        _check_output_size(tuple(display_size), f"第 {index} 项按 scale={scale:g} 缩放后的图像")
        color_metadata = {
            "has_icc_profile": bool(image.info.get("icc_profile")),
            "gamma": image.info.get("gamma"),
            "srgb": image.info.get("srgb"),
        }
        crop = image.crop(_box(rect))
    with closing(crop):
        if crop.mode == "RGBA":
            crop.save(path, format="PNG")
        else:
            with closing(crop.convert("RGBA")) as rgba:
                rgba.save(path, format="PNG")
    return {
        "component_id": component_id,
        "label": label,
        "run": {key: run[key] for key in RUN_FIELDS},
        "source": str(source),
        "sha256": preview_hash,
        "source_size": size,
        "coordinate_size": size,
        "region_source_xywh": rect,
        "scale": scale,
        "display_size": display_size,
        "color_metadata": color_metadata,
        "image": path.stem,
    }


@contextmanager
def _compose(staging: Path, items: list[dict], columns: int, background: str):
    font = ImageFont.load_default()
    column_widths = [0] * columns
    row_heights = [0] * math.ceil(len(items) / columns)
    captions = []
    for position, item in enumerate(items):
        row, column = divmod(position, columns)
        width, height = item["display_size"]
        source_width, source_height = item["region_source_xywh"][2:]
        caption = f"{position + 1}.  {source_width}x{source_height} -> {width}x{height}  @ {item['scale']:g}x"
        captions.append(caption)
        column_widths[column] = max(column_widths[column], width, math.ceil(font.getlength(caption)))
        row_heights[row] = max(row_heights[row], height)
    size = (
        sum(column_widths) + PADDING * (columns + 1),
        sum(row_heights) + PADDING * (len(row_heights) + 1) + LABEL_HEIGHT * len(row_heights),
    )
    _check_output_size(size, "视觉检查总览")

    with closing(Image.new("RGB", size, background)) as overview:
        draw = ImageDraw.Draw(overview)
        with overview.crop((0, 0, 1, 1)) as sample, sample.convert("L") as gray:
            caption_color = "#FFFFFF" if gray.getpixel((0, 0)) < 128 else "#000000"
        for position, (item, caption) in enumerate(zip(items, captions, strict=True)):
            row, column = divmod(position, columns)
            x = sum(column_widths[:column]) + PADDING * (column + 1)
            y = sum(row_heights[:row]) + (PADDING + LABEL_HEIGHT) * (row + 1)
            width, height = item["display_size"]
            item["overview_rect_xywh"] = [x, y, width, height]
            draw.text((x, y - LABEL_HEIGHT), caption, fill=caption_color, font=font)
            with closing(Image.open(staging / f"{item['image']}.png")) as snapshot:
                if snapshot.size == (width, height):
                    overview.paste(snapshot, (x, y), snapshot)
                else:
                    with closing(snapshot.resize((width, height), Image.Resampling.LANCZOS)) as scaled:
                        overview.paste(scaled, (x, y), scaled)
        yield overview


def build_visual_check(
    project_root: str,
    check_id: str,
    items: list[dict],
    *,
    background: str,
    columns: int = 3,
    grayscale: bool = True,
) -> dict:
    """保存原尺寸裁切快照和检查总览，返回来源、坐标和文件摘要。"""
    root = _absolute_path(project_root, "project_root")
    if not root.is_dir():
        raise ValueError(f"project_root 必须是目录：{root}")
    if not isinstance(check_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", check_id):
        raise ValueError("check_id 必须以英文字母或数字开头，且只能包含英文字母、数字、下划线和连字符")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f"items 必须是包含 1 到 {MAX_ITEMS} 项的列表")
    if not isinstance(background, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", background):
        raise ValueError("background 必须是 #RRGGBB 格式的纯色")
    if type(columns) is not int or not 1 <= columns <= MAX_ITEMS:
        raise ValueError(f"columns 必须是 1 到 {MAX_ITEMS} 的整数")
    if type(grayscale) is not bool:
        raise ValueError("grayscale 必须是布尔值")
    columns = min(columns, len(items))
    background = background.upper()
    destination = root / ".smartui" / "visual-checks" / check_id
    if destination.exists():
        raise FileExistsError(f"视觉检查输出目录已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temp:
        staging = Path(temp)
        # 每次只解码和处理一张源图，用磁盘快照承接布局和绘制两个步骤。
        entries = [_snapshot_item(item, index, staging / f"item-{index}.png") for index, item in enumerate(items, 1)]
        image_keys = [item["image"] for item in entries] + ["overview"]
        grayscale_info = None
        with _compose(staging, entries, columns, background) as overview:
            overview.save(staging / "overview.png", format="PNG")
            if grayscale:
                with closing(overview.convert("L")) as gray:
                    gray.save(staging / "grayscale.png", format="PNG")
                image_keys.append("grayscale")
                grayscale_info = {
                    "method": "pillow_rgb_to_l",
                    "weights": [0.299, 0.587, 0.114],
                    "applied_after": "background_composite",
                }
            size = list(overview.size)
        images = {
            key: {"path": str(destination / f"{key}.png"), "sha256": _sha256(staging / f"{key}.png")}
            for key in image_keys
        }
        staging.rename(destination)
    return {
        "id": check_id,
        "items": entries,
        "images": images,
        "display": {
            "size": size,
            "columns": columns,
            "background": background,
            "resampling": "lanczos",
            "icc_conversion": False,
            "grayscale": grayscale_info,
        },
    }
