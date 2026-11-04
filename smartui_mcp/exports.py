"""从成功组件运行导出有稳定身份、来源和几何信息的正式 PNG。"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from PIL import Image, __version__ as PILLOW_VERSION

from .comparisons import _check_output_size, _open_verified_image
from .image_assets import _file_path, verify_image_version
from .layout import ProjectLayout
from .references import _sha256
from .slicing import validate_geometry
from .visual_checks import RUN_FIELDS


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
    for protected in [layout.root / ".smartui", *(layout.directory(key) for key in layout.paths)]:
        if path.is_relative_to(protected):
            raise ValueError(f"output_path 不能写入工具记录或制作资料目录：{value}")
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
    """核对正式导出记录的版本元数据，不能用普通图像登记伪造此来源。"""
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
    if not isinstance(run, dict) or set(run) != set(RUN_FIELDS) or run.get("status") not in ("drawn", "verified"):
        raise ValueError("正式资产来源必须是完整的成功运行记录")
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


def _source(layout, state, component_id, run_id):
    _identifier(component_id, "component_id")
    _identifier(run_id, "run_id")
    component = state.get("components", {}).get(component_id)
    runs = component.get("runs", []) if isinstance(component, dict) else []
    run = next((item for item in runs if item.get("run_id") == run_id), None)
    if run is None or run.get("status") not in ("drawn", "verified"):
        raise ValueError(f"run_id 必须对应此组件已保存的成功产物：{component_id}/{run_id}")
    if not set(RUN_FIELDS) <= run.keys():
        raise ValueError(f"组件运行缺少完整版本信息：{component_id}/{run_id}")
    hashes = run.get("artifact_hashes")
    digest = hashes.get("preview") if isinstance(hashes, dict) else None
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError(f"组件运行缺少有效预览 SHA-256：{component_id}/{run_id}")
    path = layout.resolve(run.get("preview"))
    layout._check_managed_path(path)
    return path, {"component_id": component_id, "run": {key: deepcopy(run[key]) for key in RUN_FIELDS},
                  "preview": {"path": layout.relative(path), "sha256": digest},
                  "artifact_hashes": deepcopy(hashes)}


def prepare_exports(project_root, paths, state, component_id, run_id, exports):
    """全部校验并在内存生成 PNG；不改变状态，不创建目录或写任何文件。"""
    root = _file_path(project_root, "project_root")
    layout = ProjectLayout(root, paths)
    if not isinstance(exports, list) or not 1 <= len(exports) <= 64:
        raise ValueError("exports 必须包含 1 到 64 项导出定义")
    source_path, source = _source(layout, state, component_id, run_id)
    items, identifiers, outputs = [], set(), set()
    # 目标的所有权属于资产身份，最新导出记录描述磁盘上应存在的版本。
    owners = {}
    for asset_id, asset in state.get("images", {}).items():
        for record in asset["versions"]:
            if record.get("provenance", {}).get("kind") == "component_export":
                path, _ = _production_path(layout, record["output_path"])
                previous = owners.get(path)
                if previous and previous[0] != asset_id:
                    raise ValueError(f"不同资产占用了同一生产路径：{path}")
                owners[path] = (asset_id, record)
    with _open_verified_image(source_path, source["preview"]["sha256"], "正式导出来源预览") as original:
        if original.format != "PNG":
            raise ValueError(f"正式导出来源必须是 PNG 预览：{source_path}")
        _check_output_size(original.size, "正式导出来源")
        original.load()
        # 释放源图读取锁后再检查历史图像，避免嵌套获取 Pillow 全局配置锁。
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
            if owner and owner[0] != asset_id:
                raise ValueError(f"output_path 已由其他资产管理：{relative}；{owner[0]}")
            asset = state.get("images", {}).get(asset_id)
            versions = asset["versions"] if asset else []
            prior_exports = [v for v in versions if v.get("provenance", {}).get("kind") == "component_export"]
            if prior_exports and any(v["output_path"] != relative for v in prior_exports):
                raise ValueError(f"正式资产身份必须保持既有 output_path：{asset_id}")
            expected_target = None
            if owner:
                previous = deepcopy(owner[1])
                previous["path"] = str(layout.resolve(previous["path"]))
                verify_image_version(previous)
                expected_target = previous["sha256"]
                if not target.is_file() or _sha256(target) != expected_target:
                    raise ValueError(f"受管生产输出缺失或 SHA-256 与记录不一致：{target}")
            elif target.exists():
                raise ValueError(f"不能覆盖未受管的已有生产输出：{target}")
            rect = _region(item["region"], image.size)
            geometry = validate_geometry(item["geometry"], rect["width"], rect["height"])
            x, y, width, height = (rect[key] for key in ("x", "y", "width", "height"))
            with image.crop((x, y, x + width, y + height)) as crop, crop.convert("RGBA") as rgba:
                output = BytesIO()
                rgba.save(output, format="PNG", compress_level=ENCODER["compress_level"], optimize=ENCODER["optimize"])
                png = output.getvalue()
                transparent = rgba.getchannel("A").getextrema()[0] < 255
            record = {"name": item["name"], "output_path": relative, "state": item["state"],
                      "purpose": item["purpose"], "geometry": geometry,
                      "provenance": {"kind": "component_export", "source": deepcopy(source),
                                     "region": rect, "encoder": deepcopy(ENCODER)}}
            signature = _signature(record)
            # 仅最新图像版本完全相同才复用；回到历史设置也是一次显式新版本。
            reused = bool(versions and versions[-1].get("export_signature") == signature)
            if reused:
                record = deepcopy(versions[-1])
                record["path"] = str(layout.resolve(record["path"]))
                verify_image_version(record)
                png = Path(record["path"]).read_bytes()
            else:
                version = len(versions) + 1
                archived = layout.directory("sources") / "images" / asset_id / str(version) / "image.png"
                layout._check_managed_path(archived)
                _check_parents(layout, archived)
                if archived.parent.exists():
                    raise FileExistsError(f"图像资产版本已存在，不能覆盖：{archived.parent}")
                # 相同图像内容沿用既有原字节，仍保留新来源/几何版本。
                digest = _digest(png)
                for previous in reversed(prior_exports):
                    if previous["sha256"] == digest:
                        old = deepcopy(previous)
                        old["path"] = str(layout.resolve(old["path"]))
                        verify_image_version(old)
                        png = Path(old["path"]).read_bytes()
                        break
                record.update(version=version, path=str(archived), sha256=_digest(png), width=width,
                              height=height, mode="RGBA", has_transparency=transparent,
                              export_signature=signature, created_at=datetime.now(timezone.utc).isoformat())
            validate_export_record(record)
            items.append({"asset_id": asset_id, "name": item["name"], "output_path": relative,
                          "version": record["version"], "reused": reused, "record": record,
                          "png_bytes": png, "expected_output_sha256": expected_target,
                          "write_output": expected_target != record["sha256"]})
    return {"id": "export-" + uuid4().hex[:12], "project_root": str(root), "paths": deepcopy(paths),
            "source": source, "items": items}


def publish_exports(plan):
    """在项目锁内发布已准备的整批导出；预检失败时不改生产输出。"""
    layout = ProjectLayout(Path(plan["project_root"]), plan["paths"])
    source = plan["source"]["preview"]
    source_path = layout.resolve(source["path"])
    layout._check_managed_path(source_path)
    if _sha256(source_path) != source["sha256"]:
        raise ValueError(f"导出预检后来源预览发生变化：{source_path}")
    destination = layout.root / ".smartui/exports" / plan["id"]
    layout._check_managed_path(destination)
    _check_parents(layout, destination / "manifest.json")
    if destination.exists():
        raise FileExistsError(f"导出记录已存在：{destination}")
    result = {"id": plan["id"], "source": deepcopy(plan["source"]), "items": []}
    for item in plan["items"]:
        target, _ = _production_path(layout, item["output_path"])
        expected = item["expected_output_sha256"]
        if (expected is None and target.exists()) or (expected is not None and
                (not target.is_file() or _sha256(target) != expected)):
            raise ValueError(f"导出预检后生产输出发生变化：{target}")
        record = item["record"]
        validate_export_record(record)
        if _digest(item["png_bytes"]) != record["sha256"]:
            raise ValueError(f"待发布 PNG 与导出记录摘要不一致：{item['asset_id']}")
        archive = layout.resolve(record["path"])
        layout._check_managed_path(archive)
        _check_parents(layout, archive)
        if item["reused"]:
            verify_image_version(record)
        elif archive.parent.exists():
            raise FileExistsError(f"图像资产版本已存在，不能覆盖：{archive.parent}")
        result["items"].append({key: deepcopy(item[key]) for key in
                                ("asset_id", "name", "output_path", "version", "reused", "record")})
    disk_record = deepcopy(result)
    for item in disk_record["items"]:
        item["record"]["path"] = layout.relative(item["record"]["path"])
    manifest_bytes = _json(disk_record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 所有 PNG 和回滚字节先写入暂存区，之后才改生产文件。
    with TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)
        writes = []
        for index, item in enumerate(plan["items"]):
            archive = layout.resolve(item["record"]["path"])
            target, _ = _production_path(layout, item["output_path"])
            for label, path, enabled in (("archive", archive, not item["reused"]),
                                          ("output", target, item["write_output"])):
                if enabled:
                    prepared = staging / f"{index}-{label}.png"
                    prepared.write_bytes(item["png_bytes"])
                    backup = staging / f"{index}-{label}.backup" if path.exists() else None
                    if backup:
                        backup.write_bytes(path.read_bytes())
                    writes.append((prepared, path, backup, label == "archive"))
        (staging / "manifest.json").write_bytes(manifest_bytes)
        completed = []
        try:
            for prepared, path, backup, archive in writes:
                layout._check_managed_path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(prepared, path)
                completed.append((path, backup, archive))
            destination.mkdir()
            os.replace(staging / "manifest.json", destination / "manifest.json")
        except (OSError, ValueError) as error:
            rollback_errors = []
            for path, backup, archive in reversed(completed):
                try:
                    if backup:
                        os.replace(backup, path)
                    else:
                        path.unlink()
                        if archive and not any(path.parent.iterdir()):
                            path.parent.rmdir()
                except OSError as rollback_error:
                    rollback_errors.append(f"{path}：{rollback_error}")
            if destination.exists() and not any(destination.iterdir()):
                destination.rmdir()
            if rollback_errors:
                recovery = destination.with_name(destination.name + "-failed")
                staging.rename(recovery)
                detail = f"；回滚失败，备份与清单保留于 {recovery}：" + "；".join(rollback_errors)
            else:
                detail = "；已恢复本次修改的文件"
            raise OSError(f"正式导出发布失败：{error}{detail}") from error
    result["manifest"] = {"path": str(destination / "manifest.json"), "sha256": _digest(manifest_bytes)}
    return result
