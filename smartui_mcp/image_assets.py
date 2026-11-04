"""归档独立图像的不可变版本，不管理项目状态或资产选用。"""

from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from PIL import Image

from .comparisons import _check_output_size
from .layout import ProjectLayout
from .references import _open_image, _sha256


def _file_path(value: str, label: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"{label} 必须是绝对路径：{value!r}")
    path = Path(value)
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError(f"{label} 不能包含符号链接或目录联接：{parent}")
    return path.resolve(strict=True)


def _metadata(path: Path) -> dict:
    try:
        with _open_image(path) as image:
            _check_output_size(image.size, f"图像资产 {path}")
            # 完整解码才能发现截断文件；透明情况来自像素，而非仅根据模式猜测。
            image.load()
            with image.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
                transparent = alpha.getextrema()[0] < 255
            return {"width": image.width, "height": image.height, "mode": image.mode,
                    "has_transparency": transparent,
                    "extension": {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.format]}
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError(f"无法读取图像资产：{path}；{error}") from error


def _validate_provenance(value: dict) -> None:
    if not isinstance(value, dict):
        raise ValueError("provenance 必须是 JSON 对象")
    kind = value.get("kind")
    if kind not in ("generated", "edited", "imported"):
        raise ValueError("provenance.kind 必须是 generated、edited 或 imported")
    required = ("description",) if kind == "imported" else ("tool", "prompt")
    for field in required:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"provenance.{field} 必须是非空字符串")
    for field in ("tool", "prompt"):
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"provenance.{field} 必须是字符串")

    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("provenance 的 JSON 对象键必须是字符串")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("provenance 只能包含 JSON 值")

    try:
        check(value)
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError(f"provenance 必须包含有限的 JSON 值：{error}") from error


def register_image_file(project_root: str, paths: dict, asset_id: str, version: int,
                        source_path: str, provenance: dict) -> dict:
    """校验并按原始字节归档 PNG、JPEG 或 WebP；已有版本不能覆盖。"""
    root = _file_path(project_root, "project_root")
    if not root.is_dir():
        raise ValueError(f"project_root 必须是目录：{root}")
    if not isinstance(asset_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", asset_id):
        raise ValueError("asset_id 必须以英文字母或数字开头，且只能包含英文字母、数字、下划线、点和连字符")
    if type(version) is not int or version < 1:
        raise ValueError("version 必须是正整数")
    _validate_provenance(provenance)
    source = _file_path(source_path, "source_path")
    layout = ProjectLayout(root, paths)
    destination = layout.directory("sources") / "images" / asset_id / str(version)
    layout._check_managed_path(destination)
    if destination.exists():
        raise FileExistsError(f"图像资产版本已存在，不能覆盖：{destination}")
    digest = _sha256(source)
    metadata = _metadata(source)
    extension = metadata.pop("extension")
    result = {"version": version, "path": str(destination / f"image.{extension}"),
              "sha256": digest, **metadata, "provenance": deepcopy(provenance)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    layout._check_managed_path(destination)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)
        archived = staging / f"image.{extension}"
        with source.open("rb") as original, archived.open("xb") as output:
            shutil.copyfileobj(original, output)
        verify_image_version(result | {"path": str(archived)})
        layout._check_managed_path(destination)
        if destination.exists():
            raise FileExistsError(f"图像资产版本已存在，不能覆盖：{destination}")
        staging.rename(destination)
    return result


def verify_image_version(record: dict) -> None:
    """核对实际文件摘要、解码尺寸、模式与透明情况，不修改文件。"""
    if not isinstance(record, dict):
        raise ValueError("图像资产版本记录必须是对象")
    try:
        path = _file_path(record.get("path"), "图像资产 path")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("图像资产 sha256 必须是有效的 SHA-256")
        if _sha256(path) != digest:
            raise ValueError("图像资产 SHA-256 与记录不一致")
        metadata = _metadata(path)
        for field in ("width", "height", "mode", "has_transparency"):
            expected = record.get(field)
            if type(expected) is not type(metadata[field]) or expected != metadata[field]:
                raise ValueError(f"图像资产 {field} 与记录不一致：记录={expected!r}，实际={metadata[field]!r}")
    except (OSError, ValueError) as error:
        raise ValueError(f"图像资产版本校验失败：{record.get('path')}；{error}") from error
