"""正式资产的来源、稳定身份、完整性与批量发布验证。"""

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil

from PIL import Image
import pytest

from smartui_mcp.exports import prepare_exports, publish_exports
from smartui_mcp.image_assets import register_image_file, verify_image_version


def fixture(tmp_path):
    source = tmp_path / "art/ui/sources/button-preview.png"
    source.parent.mkdir(parents=True)
    with Image.new("RGBA", (12, 10), (20, 40, 60, 100)) as image:
        image.putpixel((3, 2), (200, 100, 20, 255))
        image.save(source)
    run = {"run_id": "run-one", "status": "verified", "style_version": 1,
           "spec_sha256": "1" * 64, "program_id": "buttons", "program_version": 1,
           "program_sha256": "2" * 64, "preview": str(source),
           "artifact_hashes": {"preview": hashlib.sha256(source.read_bytes()).hexdigest()}}
    state = {"components": {"button": {"runs": [run]}}, "images": {}}
    item = {"asset_id": "button-normal", "name": "普通按钮", "output_path": "Assets/UI/button.png",
            "region": {"x": 2, "y": 1, "width": 8, "height": 6}, "state": "normal", "purpose": "按钮背景",
            "geometry": {"mode": "stretch", "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4}}}
    return state, item, source


def prepare(root, state, item, run_id="run-one"):
    return prepare_exports(str(root), {}, state, "button", run_id, [item])


def save_state(state, result):
    for item in result["items"]:
        asset = state["images"].setdefault(item["asset_id"], {"id": item["asset_id"], "versions": []})
        if not item["reused"]:
            asset["versions"].append(item["record"])


def test_prepare_is_read_only_and_publish_keeps_exact_region_and_evidence(tmp_path):
    state, item, source = fixture(tmp_path)
    before = deepcopy(state)
    files = set(tmp_path.rglob("*"))
    plan = prepare(tmp_path, state, item)
    assert state == before
    assert set(tmp_path.rglob("*")) == files
    result = publish_exports(plan)
    entry = result["items"][0]
    record = entry["record"]
    assert record["geometry"] == item["geometry"]
    assert record["provenance"]["kind"] == "component_export"
    assert record["provenance"]["source"]["preview"]["path"] == "art/ui/sources/button-preview.png"
    assert record["provenance"]["region"] == item["region"]
    assert record["provenance"]["encoder"]["compress_level"] == 6
    production = tmp_path / item["output_path"]
    assert Path(record["path"]).read_bytes() == production.read_bytes()
    assert Path(result["manifest"]["path"]).is_file()
    assert result["manifest"]["sha256"] == hashlib.sha256(Path(result["manifest"]["path"]).read_bytes()).hexdigest()
    with Image.open(production) as exported, Image.open(source) as original:
        assert exported.size == (8, 6)
        assert exported.tobytes() == original.crop((2, 1, 10, 7)).tobytes()
    verify_image_version(record)


def test_unchanged_export_reuses_version_and_does_not_write_production_or_meta(tmp_path):
    state, item, _ = fixture(tmp_path)
    result = publish_exports(prepare(tmp_path, state, item))
    save_state(state, result)
    target = tmp_path / item["output_path"]
    meta = target.with_suffix(".png.meta")
    meta.write_bytes(b"Unity GUID must stay")
    target.touch()
    stamp = target.stat().st_mtime_ns
    plan = prepare(tmp_path, state, item)
    assert plan["items"][0]["reused"] is True
    assert plan["items"][0]["write_output"] is False
    second = publish_exports(plan)
    assert second["items"][0]["version"] == 1
    assert target.stat().st_mtime_ns == stamp
    assert meta.read_bytes() == b"Unity GUID must stay"


def test_new_run_with_same_pixels_creates_source_version_without_rewriting_output(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    target = tmp_path / item["output_path"]
    stamp = target.stat().st_mtime_ns
    run = deepcopy(state["components"]["button"]["runs"][0])
    run["run_id"] = "run-two"
    state["components"]["button"]["runs"].append(run)
    plan = prepare(tmp_path, state, item, "run-two")
    assert plan["items"][0]["version"] == 2
    assert not plan["items"][0]["write_output"]
    result = publish_exports(plan)
    assert target.stat().st_mtime_ns == stamp
    assert result["items"][0]["record"]["provenance"]["source"]["run"]["run_id"] == "run-two"


def test_geometry_metadata_creates_version_and_cannot_be_mutated_in_place(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    item["geometry"]["mode"] = "contain"
    plan = prepare(tmp_path, state, item)
    assert plan["items"][0]["version"] == 2
    result = publish_exports(plan)
    record = result["items"][0]["record"]
    record["geometry"]["mode"] = "stretch"
    with pytest.raises(ValueError, match="导出摘要"):
        verify_image_version(record)


@pytest.mark.parametrize("change", ["unmanaged", "managed_changed", "managed_missing", "archive_changed", "source_changed", "source_missing"])
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
        elif change == "archive_changed":
            Path(result["items"][0]["record"]["path"]).write_bytes(b"changed")
        elif change == "source_changed":
            source.write_bytes(b"changed")
        else:
            source.unlink()
    before = target.read_bytes() if target.exists() else None
    with pytest.raises((ValueError, OSError)):
        prepare(tmp_path, state, item)
    assert (target.read_bytes() if target.exists() else None) == before


@pytest.mark.parametrize("path", [".smartui/output.png", "art/ui/sources/output.png", "art/ui/programs/output.png",
                                  "docs/ui/output.png", "../outside.png", "C:/outside.png", "Assets/output.jpg"])
def test_output_path_boundaries(tmp_path, path):
    state, item, _ = fixture(tmp_path)
    item["output_path"] = path
    with pytest.raises(ValueError):
        prepare(tmp_path, state, item)
    assert not (tmp_path / "Assets").exists()


def test_all_items_preflight_before_any_output(tmp_path):
    state, item, _ = fixture(tmp_path)
    second = deepcopy(item)
    second.update(asset_id="other", output_path="Assets/UI/other.png", state="")
    with pytest.raises(ValueError, match="state"):
        prepare_exports(str(tmp_path), {}, state, "button", "run-one", [item, second])
    assert not (tmp_path / "Assets").exists()
    assert not (tmp_path / ".smartui").exists()


def test_existing_identity_and_target_ownership_are_stable(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    renamed = deepcopy(item)
    renamed["output_path"] = "Assets/UI/renamed.png"
    with pytest.raises(ValueError, match="既有 output_path"):
        prepare(tmp_path, state, renamed)
    other = deepcopy(item)
    other["asset_id"] = "other"
    with pytest.raises(ValueError, match="其他资产"):
        prepare(tmp_path, state, other)


def test_publish_rechecks_all_targets_before_writing(tmp_path):
    state, item, _ = fixture(tmp_path)
    second = deepcopy(item)
    second.update(asset_id="second", output_path="Assets/UI/second.png")
    plan = prepare_exports(str(tmp_path), {}, state, "button", "run-one", [item, second])
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
    plan = prepare(tmp_path, state, item)
    real_replace = exports.os.replace

    def fail_output(source, target):
        if Path(target) == tmp_path / item["output_path"]:
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
    run = state["components"]["button"]["runs"][0]
    run["artifact_hashes"]["preview"] = hashlib.sha256(source.read_bytes()).hexdigest()
    run["run_id"] = "run-two"
    plan = prepare(tmp_path, state, item, "run-two")
    real_replace = exports.os.replace

    def fail_manifest_and_restore(source, target):
        if Path(target).name == "manifest.json" or Path(source).suffix == ".backup":
            raise OSError("disk failure")
        return real_replace(source, target)

    monkeypatch.setattr(exports.os, "replace", fail_manifest_and_restore)
    with pytest.raises(OSError, match="备份与清单保留"):
        publish_exports(plan)
    recovery = tmp_path / ".smartui/exports" / (plan["id"] + "-failed")
    assert (recovery / "0-output.backup").read_bytes() == original_output
    assert (recovery / "manifest.json").is_file()


def test_project_move_keeps_source_signature_and_relative_output(tmp_path):
    state, item, _ = fixture(tmp_path)
    save_state(state, publish_exports(prepare(tmp_path, state, item)))
    for asset in state["images"].values():
        for record in asset["versions"]:
            record["path"] = Path(record["path"]).relative_to(tmp_path).as_posix()
    run = state["components"]["button"]["runs"][0]
    run["preview"] = Path(run["preview"]).relative_to(tmp_path).as_posix()
    moved = tmp_path.parent / (tmp_path.name + "-moved")
    shutil.copytree(tmp_path, moved)
    plan = prepare(moved, state, item)
    assert plan["items"][0]["reused"]
    result = publish_exports(plan)
    assert Path(result["items"][0]["record"]["path"]).is_relative_to(moved)


def test_public_registration_cannot_claim_component_export(tmp_path):
    _, _, source = fixture(tmp_path)
    with pytest.raises(ValueError, match="provenance.kind"):
        register_image_file(str(tmp_path), {}, "fake", 1, str(source), {"kind": "component_export"})
