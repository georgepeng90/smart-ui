"""从成功组件运行导出有稳定身份、来源和几何信息的正式 PNG。"""

from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import re

from PIL import Image, __version__ as PILLOW_VERSION

from .comparisons import RUN_FIELDS, _check_output_size, _open_verified_image
from .image_assets import _file_path, verify_image_record
from .layout import ProjectLayout
from .references import _sha256, _temporary_directory
from .slicing import validate_geometry


ENCODER = {"library": "Pillow", "version": PILLOW_VERSION, "format": "PNG",
           "mode": "RGBA", "compress_level": 6, "optimize": False}
_FIELDS = {"asset_id", "name", "output_path", "region", "state", "purpose", "geometry"}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"{label} 必须以英文字母或数字开头，且只能包含英文字母、数字、下划线、点和连字符")


def _relative_file(layout, value, label):
    relative = layout._relative_directory(value, label)
    path = layout.root / relative
    layout._check_managed_path(path)
    return path, relative


def _production_path(layout, value):
    path, relative = _relative_file(layout, value, "output_path")
    if path.suffix.lower() != ".png":
        raise ValueError(f"output_path 必须是 PNG 文件路径：{value}")
    directory = layout.persistent("exports")
    if not path.is_relative_to(directory) or path.is_relative_to(directory / "manifests"):
        raise ValueError(f"output_path 必须位于 {layout.data_root}/exports/ 的图像输出目录：{value}")
    _check_parents(layout, path)
    if path.exists() and not path.is_file():
        raise ValueError(f"output_path 已存在但不是文件：{path}")
    return path, relative


def _check_parents(layout, path):
    for parent in path.parents:
        if parent == layout.root:
            break
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"文件的父路径不是目录：{parent}")


def _signature(record):
    return _digest(_json({key: record[key] for key in
                         ("name", "output_path", "state", "purpose", "geometry", "provenance")}))


def validate_export_record(record):
    """核对正式导出记录的来源与几何摘要。"""
    provenance = record.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("kind") != "component_export":
        raise ValueError("正式资产 provenance.kind 必须是 component_export")
    for key in ("name", "output_path", "state", "purpose"):
        _text(record.get(key), f"正式资产 {key}")
    source = provenance.get("source")
    if not isinstance(source, dict) or set(source) != {"component_id", "run", "preview", "artifact_hashes"}:
        raise ValueError("正式资产来源必须包含组件、运行、预览和产物摘要")
    _identifier(source["component_id"], "source.component_id")
    run = source["run"]
    if not isinstance(run, dict) or set(run) != set(RUN_FIELDS):
        raise ValueError("正式资产来源必须包含完整的运行标识和输入摘要")
    for key in ("run_id", "program_id"):
        _identifier(run[key], f"source.run.{key}")
    for key in ("style_sha256", "program_sha256", "spec_sha256"):
        if not isinstance(run[key], str) or not re.fullmatch(r"[a-f0-9]{64}", run[key]):
            raise ValueError(f"source.run.{key} 必须是有效的 SHA-256")
    preview = source["preview"]
    if not isinstance(preview, dict) or set(preview) != {"path", "sha256"}:
        raise ValueError("正式资产来源缺少预览路径或摘要")
    ProjectLayout._relative_directory(preview["path"], "source.preview.path")
    hashes = source["artifact_hashes"]
    if (not isinstance(hashes, dict) or hashes.get("preview") != preview["sha256"] or
            not isinstance(preview["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", preview["sha256"])):
        raise ValueError("正式资产来源预览摘要无效或不一致")
    region = provenance.get("region")
    _region(region, None)
    if (region["width"], region["height"]) != (record["width"], record["height"]):
        raise ValueError("正式资产来源区域与图像尺寸不一致")
    if not isinstance(provenance.get("encoder"), dict) or set(provenance["encoder"]) != set(ENCODER):
        raise ValueError("正式资产缺少完整 PNG 编码设置")
    validate_geometry(record.get("geometry"), record["width"], record["height"])
    if record.get("export_signature") != _signature(record):
        raise ValueError("正式资产几何、来源或元数据与导出摘要不一致")


def _region(value, size):
    keys = {"x", "y", "width", "height"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("region 必须恰好包含 x、y、width、height；整图导出也必须显式提供区域")
    for key in keys:
        if type(value[key]) is not int or value[key] < (1 if key in ("width", "height") else 0):
            raise ValueError(f"region.{key} 必须是有效的整数像素")
    if size and (value["x"] + value["width"] > size[0] or value["y"] + value["height"] > size[1]):
        raise ValueError("region 必须完整位于来源预览图内")
    return deepcopy(value)


def _source(layout, state, component_id):
    _identifier(component_id, "component_id")
    component = state.get("components", {}).get(component_id)
    run = component.get("latest_run") if isinstance(component, dict) else None
    if not isinstance(run, dict) or run.get("status") not in ("drawn", "verified"):
        raise ValueError(f"组件缺少当前成功绘制产物：{component_id}")
    if not set(RUN_FIELDS) <= run.keys():
        raise ValueError(f"组件当前运行缺少完整来源信息：{component_id}")
    hashes = run.get("artifact_hashes")
    digest = hashes.get("preview") if isinstance(hashes, dict) else None
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError(f"组件当前运行缺少有效预览 SHA-256：{component_id}")
    path = layout.resolve(run.get("preview"))
    layout._check_managed_path(path)
    return path, {"component_id": component_id, "run": {key: deepcopy(run[key]) for key in RUN_FIELDS},
                  "preview": {"path": layout.relative(path), "sha256": digest},
                  "artifact_hashes": deepcopy(hashes)}


def _manifest_path(layout, component_id):
    _identifier(component_id, "component_id")
    destination = layout.persistent(f"exports/manifests/{component_id}.json")
    _check_parents(layout, destination)
    return destination


def _check_existing(path, expected, label):
    if expected is None:
        if path.exists():
            raise ValueError(f"{label}已存在且未登记：{path}")
    elif not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"{label}缺失或 SHA-256 与记录不一致：{path}")


def prepare_exports(project_root, paths, state, component_id, exports):
    """校验输入并准备整批 PNG，不改动项目记录和正式文件。"""
    root = _file_path(project_root, "project_root")
    layout = ProjectLayout(root, paths)
    if not isinstance(exports, list) or not 1 <= len(exports) <= 64:
        raise ValueError("exports 必须包含 1 到 64 项导出定义")
    source_path, source = _source(layout, state, component_id)
    manifest_path = _manifest_path(layout, component_id)
    previous_export = state.get("exports", {}).get(component_id)
    manifest_hash = None
    if previous_export is not None:
        previous_manifest = previous_export["manifest"]
        if layout.resolve(previous_manifest["path"]) != manifest_path:
            raise ValueError(f"当前导出清单路径与组件不符：{manifest_path}")
        manifest_hash = previous_manifest["sha256"]
    _check_existing(manifest_path, manifest_hash, "正式导出清单")
    items, identifiers, outputs = [], set(), set()
    owners = {}
    images = state.get("images", {})
    for asset_id, record in images.items():
        if record.get("provenance", {}).get("kind") == "component_export":
            path, _ = _production_path(layout, record["output_path"])
            if path in owners:
                raise ValueError(f"不同资产占用了同一生产路径：{path}")
            owners[path] = asset_id
    with _open_verified_image(source_path, source["preview"]["sha256"], "正式导出来源预览",
                              _temporary_directory(root)) as original:
        if original.format != "PNG":
            raise ValueError(f"正式导出来源必须是 PNG 预览：{source_path}")
        _check_output_size(original.size, "正式导出来源")
        original.load()
        # 释放源图读取锁后再检查现有图像。
        snapshot = original.copy()
    with snapshot as image:
        for index, item in enumerate(exports):
            if not isinstance(item, dict) or set(item) != _FIELDS:
                raise ValueError(f"exports[{index}] 必须恰好包含 {sorted(_FIELDS)}")
            asset_id = item["asset_id"]
            _identifier(asset_id, f"exports[{index}].asset_id")
            for key in ("name", "state", "purpose"):
                _text(item[key], f"exports[{index}].{key}")
            target, relative = _production_path(layout, item["output_path"])
            if asset_id in identifiers or target in outputs:
                raise ValueError(f"同一批次不能重复 asset_id 或 output_path：{asset_id}/{relative}")
            identifiers.add(asset_id)
            outputs.add(target)
            if target == source_path:
                raise ValueError("output_path 不能覆盖来源预览")
            owner = owners.get(target)
            if owner is not None and owner != asset_id:
                raise ValueError(f"output_path 已由其他资产管理：{relative}；{owner}")
            previous = images.get(asset_id)
            expected_target = None
            if previous is not None:
                if previous.get("provenance", {}).get("kind") != "component_export":
                    raise ValueError(f"asset_id 已用于登记独立图像：{asset_id}")
                if previous["output_path"] != relative or layout.resolve(previous["path"]) != target:
                    raise ValueError(f"正式资产身份必须保持既有 output_path：{asset_id}")
                verify_image_record(previous | {"path": str(target)})
                owner_component = previous["provenance"]["source"]["component_id"]
                if owner_component != component_id:
                    raise ValueError(f"asset_id 已由其他组件导出：{asset_id}；{owner_component}")
                expected_target = previous["sha256"]
            _check_existing(target, expected_target, "正式导出输出")
            rect = _region(item["region"], image.size)
            geometry = validate_geometry(item["geometry"], rect["width"], rect["height"])
            x, y, width, height = (rect[key] for key in ("x", "y", "width", "height"))
            with image.crop((x, y, x + width, y + height)) as crop, crop.convert("RGBA") as rgba:
                output = BytesIO()
                rgba.save(output, format="PNG", compress_level=ENCODER["compress_level"], optimize=ENCODER["optimize"])
                png = output.getvalue()
                transparent = rgba.getchannel("A").getextrema()[0] < 255
            record = {"id": asset_id, "name": item["name"], "output_path": relative, "path": str(target),
                      "state": item["state"], "purpose": item["purpose"], "geometry": geometry,
                      "sha256": _digest(png), "width": width, "height": height,
                      "mode": "RGBA", "has_transparency": transparent,
                      "provenance": {"kind": "component_export", "source": deepcopy(source),
                                     "region": rect, "encoder": deepcopy(ENCODER)}}
            record["export_signature"] = _signature(record)
            validate_export_record(record)
            items.append({"asset_id": asset_id, "name": item["name"], "output_path": relative,
                          "record": record,
                          "png_bytes": png, "expected_output_sha256": expected_target,
                          "write_output": expected_target != record["sha256"]})
    return {"id": component_id, "project_root": str(root), "paths": deepcopy(paths),
            "source": source, "items": items, "expected_manifest_sha256": manifest_hash}


def publish_exports(plan, *, commit=None):
    """在项目锁内发布已准备的整批导出；预检失败时不改生产输出。"""
    layout = ProjectLayout(Path(plan["project_root"]), plan["paths"])
    source = plan["source"]["preview"]
    source_path = layout.resolve(source["path"])
    layout._check_managed_path(source_path)
    if _sha256(source_path) != source["sha256"]:
        raise ValueError(f"导出预检后来源预览发生变化：{source_path}")
    destination = _manifest_path(layout, plan["id"])
    expected_manifest = plan["expected_manifest_sha256"]
    _check_existing(destination, expected_manifest, "预检后的正式导出清单")
    result = {"id": plan["id"], "source": deepcopy(plan["source"]), "items": []}
    for item in plan["items"]:
        target, _ = _production_path(layout, item["output_path"])
        expected = item["expected_output_sha256"]
        _check_existing(target, expected, "预检后的正式导出输出")
        record = item["record"]
        validate_export_record(record)
        if layout.resolve(record["path"]) != target or record["output_path"] != item["output_path"]:
            raise ValueError(f"待发布 PNG 的记录路径与输出路径不一致：{item['asset_id']}")
        if _digest(item["png_bytes"]) != record["sha256"]:
            raise ValueError(f"待发布 PNG 与导出记录摘要不一致：{item['asset_id']}")
        result["items"].append({key: deepcopy(item[key]) for key in
                                ("asset_id", "name", "output_path", "record")})
    disk_record = deepcopy(result)
    for item in disk_record["items"]:
        item["record"]["path"] = layout.relative(item["record"]["path"])
    manifest_bytes = _json(disk_record)
    manifest_hash = _digest(manifest_bytes)
    result["manifest"] = {"path": str(destination), "sha256": manifest_hash}
    # 所有 PNG 和回滚字节先写入暂存区，之后才改生产文件。
    with TemporaryDirectory(dir=_temporary_directory(layout.root)) as temporary:
        staging = Path(temporary)
        writes = []
        for index, item in enumerate(plan["items"]):
            if item["write_output"]:
                target, _ = _production_path(layout, item["output_path"])
                prepared = staging / f"{index}-output.png"
                prepared.write_bytes(item["png_bytes"])
                backup = staging / f"{index}-output.backup" if target.exists() else None
                if backup:
                    backup.write_bytes(target.read_bytes())
                writes.append((prepared, target, backup))
        manifest = staging / "manifest.json"
        manifest.write_bytes(manifest_bytes)
        if manifest_hash != expected_manifest:
            backup = staging / "manifest.backup" if destination.exists() else None
            if backup:
                backup.write_bytes(destination.read_bytes())
            writes.append((manifest, destination, backup))
        completed = []
        committing = False
        try:
            for prepared, path, backup in writes:
                layout._check_managed_path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(prepared, path)
                completed.append((path, backup))
            committing = commit is not None
            return commit(result) if committing else result
        except BaseException as error:
            rollback_errors = []
            for path, backup in reversed(completed):
                try:
                    if backup:
                        os.replace(backup, path)
                    else:
                        path.unlink()
                except OSError as rollback_error:
                    rollback_errors.append(f"{path}：{rollback_error}")
            if rollback_errors:
                recovery = staging.with_name(staging.name + "-failed")
                retained = "备份与清单" if manifest.exists() else "备份"
                staging.rename(recovery)
                detail = f"；回滚失败，{retained}保留于 {recovery}：" + "；".join(rollback_errors)
                raise OSError(f"正式导出发布失败：{error}{detail}") from error
            if not committing and isinstance(error, (OSError, ValueError)):
                raise OSError(f"正式导出发布失败：{error}；已恢复本次修改的文件") from error
            raise
