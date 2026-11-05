"""正式资产的来源、稳定身份、完整性与批量发布验证。"""

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil

from PIL import Image
import pytest

from smartui_mcp.exports import prepare_exports, publish_exports
from smartui_mcp.image_assets import register_image_file, verify_image_record


def fixture(tmp_path):
    source = tmp_path / "smart-ui/previews/components/button.png"
    source.parent.mkdir(parents=True)
    with Image.new("RGBA", (12, 10), (20, 40, 60, 100)) as image:
        image.putpixel((3, 2), (200, 100, 20, 255))
        image.save(source)
    run = {"run_id": "run-one", "status": "verified", "style_sha256": "3" * 64,
           "spec_sha256": "1" * 64, "program_id": "buttons",
           "program_sha256": "2" * 64, "preview": str(source),
           "artifact_hashes": {"preview": hashlib.sha256(source.read_bytes()).hexdigest()}}
    state = {"components": {"button": {"latest_run": run}}, "images": {}, "exports": {}}
    item = {"asset_id": "button-normal", "name": "普通按钮", "output_path": "smart-ui/exports/ui/button.png",
            "region": {"x": 2, "y": 1, "width": 8, "height": 6}, "state": "normal", "purpose": "按钮背景",
            "geometry": {"mode": "stretch", "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4}}}
    return state, item, source


def prepare(root, state, item):
    return prepare_exports(str(root), {}, state, "button", [item])


def save_state(state, result):
    state["exports"][result["id"]] = result
    for item in result["items"]:
        state["images"][item["asset_id"]] = item["record"]


def test_prepare_is_read_only_and_publish_keeps_exact_region_and_evidence(tmp_path):
    state, item, source = fixture(tmp_path)
    before = deepcopy(state)
    files = set((tmp_path / "smart-ui").rglob("*"))
    plan = prepare(tmp_path, state, item)
    assert state == before
    assert set((tmp_path / "smart-ui").rglob("*")) == files
    assert not list((tmp_path / ".smartui/temp").iterdir())
    result = publish_exports(plan)
    entry = result["items"][0]
    record = entry["record"]
    assert record["geometry"] == item["geometry"]
    assert record["provenance"]["kind"] == "component_export"
    assert record["provenance"]["source"]["preview"]["path"] == "smart-ui/previews/components/button.png"
    assert record["provenance"]["region"] == item["region"]
    assert record["provenance"]["encoder"]["compress_level"] == 6
    production = tmp_path / item["output_path"]
    assert Path(record["path"]) == production
    assert record["id"] == item["asset_id"]
    assert "version" not in record
    assert result["id"] == "button"
    assert Path(result["manifest"]["path"]) == tmp_path / "smart-ui/exports/manifests/button.json"
    assert result["manifest"]["sha256"] == hashlib.sha256(Path(result["manifest"]["path"]).read_bytes()).hexdigest()
    with Image.open(production) as exported, Image.open(source) as original:
        assert exported.size == (8, 6)
        assert exported.tobytes() == original.crop((2, 1, 10, 7)).tobytes()
    verify_image_record(record)


def test_unchanged_export_does_not_rewrite_png_manifest_or_adjacent_file(tmp_path):
    state, item, _ = fixture(tmp_path)
    result = publish_exports(prepare(tmp_path, state, item))
    save_state(state, result)
    target = tmp_path / item["output_path"]
    meta = target.with_suffix(".png.meta")
    meta.write_bytes(b"adjacent file")
    manifest = Path(result["manifest"]["path"])
    manifest.touch()
    manifest_stamp = manifest.stat().st_mtime_ns
    target.touch()
    stamp = target.stat().st_mtime_ns
    plan = prepare(tmp_path, state, item)
    assert plan["items"][0]["write_output"] is False
    second = publish_exports(plan)
    assert second == result
    assert manifest.stat().st_mtime_ns == manifest_stamp
    assert target.stat().st_mtime_ns == stamp
    assert meta.read_bytes() == b"adjacent file"


def test_new_run_with_same_pixels_updates_current_source_without_rewriting_png(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    target = tmp_path / item["output_path"]
    stamp = target.stat().st_mtime_ns
    run = deepcopy(state["components"]["button"]["latest_run"])
    run["run_id"] = "run-two"
    state["components"]["button"]["latest_run"] = run
    plan = prepare(tmp_path, state, item)
    assert not plan["items"][0]["write_output"]
    result = publish_exports(plan)
    assert target.stat().st_mtime_ns == stamp
    assert result["items"][0]["record"]["provenance"]["source"]["run"]["run_id"] == "run-two"
    assert list((tmp_path / "smart-ui/exports/manifests").iterdir()) == [Path(result["manifest"]["path"])]


def test_current_geometry_metadata_cannot_be_mutated_in_place(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    item["geometry"]["mode"] = "contain"
    plan = prepare(tmp_path, state, item)
    result = publish_exports(plan)
    record = result["items"][0]["record"]
    record["geometry"]["mode"] = "stretch"
    with pytest.raises(ValueError, match="导出摘要"):
        verify_image_record(record)


@pytest.mark.parametrize("change", ["unmanaged", "managed_changed", "managed_missing", "source_changed", "source_missing"])
def test_invalid_evidence_never_overwrites_output(tmp_path, change):
    state, item, source = fixture(tmp_path)
    target = tmp_path / item["output_path"]
    if change == "unmanaged":
        target.parent.mkdir(parents=True)
        target.write_bytes(b"user file")
    else:
        result = publish_exports(prepare(tmp_path, state, item))
        save_state(state, result)
        if change == "managed_changed":
            target.write_bytes(b"user edit")
        elif change == "managed_missing":
            target.unlink()
        elif change == "source_changed":
            source.write_bytes(b"changed")
        else:
            source.unlink()
    before = target.read_bytes() if target.exists() else None
    with pytest.raises((ValueError, OSError)):
        prepare(tmp_path, state, item)
    assert (target.read_bytes() if target.exists() else None) == before


@pytest.mark.parametrize("path", [".smartui/output.png", "art/ui/sources/output.png", "art/ui/programs/output.png",
                                  "Assets/UI/button.png", "../outside.png", "C:/outside.png",
                                  "smart-ui/exports/output.jpg", "smart-ui/exports/manifests/output.png"])
def test_output_path_boundaries(tmp_path, path):
    state, item, _ = fixture(tmp_path)
    item["output_path"] = path
    with pytest.raises(ValueError):
        prepare(tmp_path, state, item)
    assert not (tmp_path / "smart-ui/exports").exists()


def test_all_items_preflight_before_any_output(tmp_path):
    state, item, _ = fixture(tmp_path)
    second = deepcopy(item)
    second.update(asset_id="other", output_path="smart-ui/exports/ui/other.png", state="")
    with pytest.raises(ValueError, match="state"):
        prepare_exports(str(tmp_path), {}, state, "button", [item, second])
    assert not (tmp_path / "smart-ui/exports").exists()
    assert not list((tmp_path / ".smartui/temp").iterdir())


def test_existing_identity_and_target_ownership_are_stable(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    renamed = deepcopy(item)
    renamed["output_path"] = "smart-ui/exports/ui/renamed.png"
    with pytest.raises(ValueError, match="既有 output_path"):
        prepare(tmp_path, state, renamed)
    other = deepcopy(item)
    other["asset_id"] = "other"
    with pytest.raises(ValueError, match="其他资产"):
        prepare(tmp_path, state, other)


def test_publish_rechecks_all_targets_before_writing(tmp_path):
    state, item, _ = fixture(tmp_path)
    second = deepcopy(item)
    second.update(asset_id="second", output_path="smart-ui/exports/ui/second.png")
    plan = prepare_exports(str(tmp_path), {}, state, "button", [item, second])
    target = tmp_path / second["output_path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"new user file")
    with pytest.raises(ValueError, match="预检后"):
        publish_exports(plan)
    assert not (tmp_path / item["output_path"]).exists()
    assert target.read_bytes() == b"new user file"


def test_publish_rolls_back_completed_writes_when_a_later_write_fails(tmp_path, monkeypatch):
    from smartui_mcp import exports
    state, item, _ = fixture(tmp_path)
    second = deepcopy(item)
    second.update(asset_id="second", output_path="smart-ui/exports/ui/second.png")
    plan = prepare_exports(str(tmp_path), {}, state, "button", [item, second])
    real_replace = exports.os.replace

    def fail_output(source, target):
        if Path(target) == tmp_path / second["output_path"]:
            raise OSError("disk failure")
        return real_replace(source, target)

    monkeypatch.setattr(exports.os, "replace", fail_output)
    with pytest.raises(OSError, match="已恢复"):
        publish_exports(plan)
    assert not (tmp_path / item["output_path"]).exists()
    assert not Path(plan["items"][0]["record"]["path"]).exists()


def test_failed_restore_preserves_original_bytes_in_recovery_directory(tmp_path, monkeypatch):
    from smartui_mcp import exports
    state, item, source = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    original_output = (tmp_path / item["output_path"]).read_bytes()
    with Image.new("RGBA", (12, 10), "red") as image:
        image.save(source)
    run = state["components"]["button"]["latest_run"]
    run["artifact_hashes"]["preview"] = hashlib.sha256(source.read_bytes()).hexdigest()
    run["run_id"] = "run-two"
    plan = prepare(tmp_path, state, item)
    real_replace = exports.os.replace

    def fail_manifest_and_restore(source, target):
        if Path(target).name == "button.json" or Path(source).suffix == ".backup":
            raise OSError("disk failure")
        return real_replace(source, target)

    monkeypatch.setattr(exports.os, "replace", fail_manifest_and_restore)
    with pytest.raises(OSError, match="备份与清单保留"):
        publish_exports(plan)
    recovery, = (tmp_path / ".smartui/temp").glob("*-failed")
    assert (recovery / "0-output.backup").read_bytes() == original_output
    assert (recovery / "manifest.json").is_file()


def test_project_move_keeps_source_signature_and_relative_output(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    record = state["images"][item["asset_id"]]
    record["path"] = Path(record["path"]).relative_to(tmp_path).as_posix()
    manifest = state["exports"]["button"]["manifest"]
    manifest["path"] = Path(manifest["path"]).relative_to(tmp_path).as_posix()
    run = state["components"]["button"]["latest_run"]
    run["preview"] = Path(run["preview"]).relative_to(tmp_path).as_posix()
    moved = tmp_path.parent / (tmp_path.name + "-moved")
    shutil.copytree(tmp_path, moved)
    plan = prepare(moved, state, item)
    assert not plan["items"][0]["write_output"]
    result = publish_exports(plan)
    assert Path(result["items"][0]["record"]["path"]).is_relative_to(moved)


def test_public_registration_cannot_claim_component_export(tmp_path):
    _, _, source = fixture(tmp_path)
    with pytest.raises(ValueError, match="provenance.kind"):
        register_image_file(str(tmp_path), {}, "fake", "伪造图像", str(source), {"kind": "component_export"})
