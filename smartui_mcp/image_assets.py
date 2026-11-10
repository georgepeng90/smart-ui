"""保存和验证独立图像的当前文件。"""

from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from PIL import Image

from .comparisons import _check_output_size
from .layout import ProjectLayout
from .references import _open_image, _sha256, _temporary_directory


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


def _replace_directory(layout, staging, destination, commit=None):
    """发布当前目录，保留原目录直到项目记录写入成功。"""
    layout._check_managed_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = staging.with_name("previous")
    published = False
    try:
        if destination.exists():
            if not destination.is_dir():
                raise ValueError(f"受管输出路径不是目录：{destination}")
            if layout.is_unity and destination.is_relative_to(layout.root / layout.data_root):
                for output in list(staging.rglob("*")):
                    if output.suffix == ".meta":
                        continue
                    current = destination / output.relative_to(staging)
                    layout._check_managed_path(current)
                    if not current.exists() or current.is_dir() != output.is_dir():
                        continue
                    meta = current.with_name(current.name + ".meta")
                    layout._check_managed_path(meta)
                    if meta.exists():
                        if not meta.is_file():
                            raise ValueError(f"Unity 资源的 .meta 路径必须是文件：{meta}")
                        shutil.copyfile(meta, output.with_name(meta.name))
            destination.rename(previous)
        staging.rename(destination)
        published = True
        if commit is not None:
            return commit()
    except BaseException as error:
        try:
            if published:
                destination.rename(staging)
            if previous.exists():
                previous.rename(destination)
        except OSError as rollback_error:
            recovery = staging.parent.with_name(staging.parent.name + "-failed")
            staging.parent.rename(recovery)
            raise OSError(f"发布失败：{error}；恢复失败：{rollback_error}；恢复文件保留于 {recovery}") from error
        raise


def register_image_file(project_root: str, paths: dict, asset_id: str, name: str,
                        source_path: str, provenance: dict, *, commit=None) -> dict:
    """校验并按原始字节保存 PNG、JPEG 或 WebP，替换同一资产的当前文件。"""
    root = _file_path(project_root, "project_root")
    if not root.is_dir():
        raise ValueError(f"project_root 必须是目录：{root}")
    if not isinstance(asset_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", asset_id):
        raise ValueError("asset_id 必须以英文字母或数字开头，且只能包含英文字母、数字、下划线、点和连字符")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name 必须是非空字符串")
    _validate_provenance(provenance)
    source = _file_path(source_path, "source_path")
    layout = ProjectLayout(root, paths)
    destination = layout.directory("sources") / "images" / asset_id
    layout._check_managed_path(destination)
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"受管图像路径不是目录：{destination}")
        entries = list(destination.iterdir())
        if layout.is_unity:
            for entry in entries[:]:
                if entry.suffix == ".meta" and entry.with_suffix("") in entries:
                    layout._check_managed_path(entry)
                    if not entry.is_file():
                        raise ValueError(f"Unity 资源的 .meta 路径必须是文件：{entry}")
                    entries.remove(entry)
        if len(entries) != 1 or entries[0].name not in {"image.png", "image.jpg", "image.webp"}:
            raise ValueError(f"受管图像目录必须只包含一个当前图像：{destination}")
        current = entries[0]
        layout._check_managed_path(current)
        if _metadata(current)["extension"] != current.suffix[1:]:
            raise ValueError(f"受管图像的扩展名与实际格式不一致：{current}")
    digest = _sha256(source)
    metadata = _metadata(source)
    extension = metadata.pop("extension")
    result = {"id": asset_id, "name": name, "path": str(destination / f"image.{extension}"),
              "sha256": digest, **metadata, "provenance": deepcopy(provenance)}
    with TemporaryDirectory(dir=_temporary_directory(root)) as temporary:
        staging = Path(temporary) / "current"
        staging.mkdir()
        prepared = staging / f"image.{extension}"
        with source.open("rb") as original, prepared.open("xb") as output:
            shutil.copyfileobj(original, output)
        verify_image_record(result | {"path": str(prepared)})
        return _replace_directory(layout, staging, destination,
                                  lambda: commit(result) if commit is not None else result)


def verify_image_record(record: dict) -> None:
    """核对实际文件摘要、解码尺寸、模式与透明情况，不修改文件。"""
    if not isinstance(record, dict):
        raise ValueError("图像资产记录必须是对象")
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
        provenance = record.get("provenance")
        if isinstance(provenance, dict) and provenance.get("kind") == "component_export":
            from .exports import validate_export_record
            validate_export_record(record)
        else:
            _validate_provenance(provenance)
    except (OSError, ValueError) as error:
        raise ValueError(f"图像资产校验失败：{record.get('path')}；{error}") from error
