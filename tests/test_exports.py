"""正式资产的来源、稳定身份、完整性与批量发布验证。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image, ImageCms
import pytest

from smartui_mcp.exports import _signature, prepare_exports, publish_exports
from smartui_mcp.image_assets import register_image_file, verify_image_record
from smartui_mcp.layout import ProjectLayout
from smartui_mcp.projects import spec_sha256
from smartui_mcp.production_design import asset_design_issue
from smartui_mcp.slicing import image_patches


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
    reference = {"width": 64, "height": 48}
    sprite = {"x": 2, "y": 1, "width": 6, "height": 4, "basis": "按钮规范要求主体 6×4，四边各外扩一像素",
              "outsets": {"left": 1, "right": 1, "top": 1, "bottom": 1}}
    spec = {"design": {"sprites": {key: deepcopy(sprite) for key in ("button-normal", "short", "second")},
                       "reference_size": reference}}
    run["spec_sha256"] = spec_sha256(spec)
    state = {"components": {"button": {"latest_run": run, "spec": spec}}, "images": {}, "exports": {},
             "ui_settings": {"reference_size": reference}}
    item = {"asset_id": "button-normal", "name": "普通按钮", "output_path": "smart-ui/exports/ui/button.png",
            "region": {"x": 2, "y": 1, "width": 8, "height": 6}, "state": "normal", "purpose": "按钮背景",
            "geometry": {"mode": "stretch", "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4}}}
    return state, item, source


def prepare(root, state, item):
    return prepare_exports(str(root), ProjectLayout(root).paths, state, "button", [item])


def make_unity_project(root, item):
    (root / "Assets").mkdir()
    version = root / "ProjectSettings/ProjectVersion.txt"
    version.parent.mkdir()
    version.write_text("m_EditorVersion: 6000.0.62f1\n", encoding="utf-8")
    item["output_path"] = "Assets/UI/button.png"


def save_state(state, result):
    state["exports"][result["id"]] = result
    for item in result["items"]:
        state["images"][item["asset_id"]] = item["record"]


def sample_fixture(tmp_path, mode, sample_size, *, icc_profile=None):
    state, item, source = fixture(tmp_path)
    left, right = (0, 0) if mode == "vertical" else (2, 2)
    top, bottom = (0, 0) if mode == "horizontal" else (1, 2)
    item["geometry"] = {"mode": mode, "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4},
                        "borders": {"left": left, "right": right, "top": top, "bottom": bottom}}
    item["slice_sample_size"] = dict(zip(("width", "height"), sample_size))
    with Image.new("RGBA", (12, 10)) as image:
        for y in range(6):
            row = top if sample_size[1] < 6 and top <= y < 6 - bottom else y
            for x in range(8):
                column = left if sample_size[0] < 8 and left <= x < 8 - right else x
                image.putpixel((x + 2, y + 1), (column * 23, row * 37, column + row, (column + row) * 16))
        image.save(source, icc_profile=icc_profile)
    state["components"]["button"]["latest_run"]["artifact_hashes"]["preview"] = hashlib.sha256(source.read_bytes()).hexdigest()
    return state, item, source


@pytest.mark.parametrize("mode,sample_size", [
    ("horizontal", (5, 6)), ("vertical", (8, 4)), ("nine_slice", (5, 6)),
    ("nine_slice", (8, 4)), ("nine_slice", (5, 4)), ("nine_slice", (6, 5)),
])
def test_slice_sample_reconstructs_original_region_pixels_and_offsets(tmp_path, mode, sample_size):
    state, item, source = sample_fixture(tmp_path, mode, sample_size)
    original_bytes = source.read_bytes()
    exported = publish_exports(prepare(tmp_path, state, item))["items"][0]["record"]
    verify_image_record(exported)
    assert (exported["width"], exported["height"]) == sample_size
    assert exported["has_transparency"] is True
    assert exported["provenance"]["source_geometry"] == item["geometry"]
    assert exported["provenance"]["slice_sample_size"] == item["slice_sample_size"]
    assert exported["provenance"]["region"] == item["region"]
    assert exported["geometry"]["borders"] == item["geometry"]["borders"]
    assert (exported["provenance"]["design"]["width"], exported["provenance"]["design"]["height"]) == (6, 4)
    assert exported["geometry"]["content_rect"] == {"x": 1, "y": 1, "width": sample_size[0] - 2, "height": sample_size[1] - 2}

    patches = image_patches(exported["geometry"], sample_size, (6, 4))
    assert patches["image_size"] == [8, 6]
    assert patches["content_offset"] == [-1, -1]
    with Image.open(exported["path"]) as sample, Image.open(source) as original, Image.new("RGBA", (8, 6)) as restored:
        for patch in patches["patches"]:
            x, y, width, height = patch["source"]
            dx, dy, dw, dh = patch["target"]
            with sample.crop((x, y, x + width, y + height)) as piece, piece.resize((dw, dh), Image.Resampling.NEAREST) as scaled:
                restored.paste(scaled, (dx, dy))
        assert restored.tobytes() == original.crop((2, 1, 10, 7)).tobytes()
    assert source.read_bytes() == original_bytes


def test_compact_slice_sample_preserves_the_original_png_color_profile(tmp_path):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    state, item, _ = sample_fixture(tmp_path, "nine_slice", (5, 4), icc_profile=profile)
    full = {key: value for key, value in item.items() if key != "slice_sample_size"}
    for definition, expected_size in ((full, (8, 6)), (item, (5, 4))):
        result = publish_exports(prepare(tmp_path, state, definition))
        save_state(state, result)
        with Image.open(result["items"][0]["record"]["path"]) as output:
            assert output.size == expected_size
            assert output.info.get("icc_profile") == profile


@pytest.mark.parametrize("patch,reason", [
    ({"slice_sample_size": None}, "正整数"), ({"slice_sample_size": {"width": True, "height": 6}}, "正整数"),
    ({"slice_sample_size": {"width": 5}}, "正整数"),
    ({"slice_sample_size": {"width": 9, "height": 6}}, "不能大于"),
    ({"slice_sample_size": {"width": 4, "height": 6}}, "正中心"),
    ({"slice_sample_size": {"width": 5, "height": 5}}, "horizontal"),
    ({"geometry": {"mode": "stretch", "content_rect": {"x": 1, "y": 1, "width": 6, "height": 4}}}, "只支持"),
])
def test_invalid_slice_sample_request_leaves_no_outputs(tmp_path, patch, reason):
    state, item, _ = sample_fixture(tmp_path, "horizontal", (5, 6))
    with pytest.raises(ValueError, match=rf"slice_sample_size.*{reason}|{reason}.*slice_sample_size"):
        prepare(tmp_path, state, item | patch)
    assert not (tmp_path / "smart-ui/exports").exists()


def test_nonuniform_center_rejects_entire_batch_even_for_transparent_rgb(tmp_path):
    state, item, source = sample_fixture(tmp_path, "horizontal", (5, 6))
    full = {key: value for key, value in item.items() if key != "slice_sample_size"}
    first = publish_exports(prepare(tmp_path, state, full))
    save_state(state, first)
    target, manifest = (tmp_path / item["output_path"]), Path(first["manifest"]["path"])
    original = target.read_bytes(), manifest.read_bytes()
    with Image.open(source) as image:
        updated = image.copy()
    with updated:
        for x in range(4, 8):
            updated.putpixel((x, 1), (34, 12, 67, 0))
        updated.putpixel((7, 1), (34, 12, 66, 0))
        updated.save(source)
    state["components"]["button"]["latest_run"]["artifact_hashes"]["preview"] = hashlib.sha256(source.read_bytes()).hexdigest()
    new = item | {"asset_id": "short", "output_path": "smart-ui/exports/ui/short.png"}
    with pytest.raises(ValueError, match=r"slice_sample_size\.width 无法无损收短"):
        prepare_exports(str(tmp_path), ProjectLayout(tmp_path).paths, state, "button", [full, new])
    assert (target.read_bytes(), manifest.read_bytes()) == original
    assert not (tmp_path / new["output_path"]).exists()


def test_sample_record_validates_source_geometry_size_and_signature(tmp_path):
    state, item, _ = sample_fixture(tmp_path, "nine_slice", (5, 4))
    record = publish_exports(prepare(tmp_path, state, item))["items"][0]["record"]
    wrong_size = deepcopy(record)
    wrong_size["provenance"]["slice_sample_size"]["width"] = 6
    with pytest.raises(ValueError, match="切片样片尺寸或几何"):
        verify_image_record(wrong_size)
    missing_geometry = deepcopy(record)
    del missing_geometry["provenance"]["source_geometry"]
    with pytest.raises(ValueError, match="source_geometry"):
        verify_image_record(missing_geometry)
    changed_origin = deepcopy(record)
    changed_origin["provenance"]["region"]["x"] += 1
    with pytest.raises(ValueError, match="制作结果与 1× 设计规格不一致"):
        verify_image_record(changed_origin)


def test_atlas_region_and_content_must_match_the_design_before_sampling(tmp_path):
    state, item, _ = sample_fixture(tmp_path, "nine_slice", (5, 4))
    for patch in ({"region": {**item["region"], "x": 1}},
                  {"geometry": {**item["geometry"], "content_rect": {"x": 1, "y": 1, "width": 5, "height": 4}}}):
        with pytest.raises(ValueError, match="1× 设计规格不一致"):
            prepare(tmp_path, state, item | patch)
    assert not (tmp_path / "smart-ui/exports").exists()


def test_existing_export_without_design_is_readable_and_requires_new_evidence(tmp_path):
    state, item, _ = fixture(tmp_path)
    exported = publish_exports(prepare(tmp_path, state, item))
    legacy = exported["items"][0]["record"]
    del legacy["provenance"]["design"]
    legacy["export_signature"] = _signature(legacy)
    verify_image_record(legacy)
    assert "缺少已核验" in asset_design_issue(state, legacy)
    save_state(state, exported)
    replacement = prepare(tmp_path, state, item)
    assert not replacement["items"][0]["write_output"]
    current = publish_exports(replacement)["items"][0]["record"]
    assert asset_design_issue(state, current) is None
    assert current["sha256"] == legacy["sha256"] and current["export_signature"] != legacy["export_signature"]


def test_exported_atlas_sprite_requires_its_complete_size_evidence(tmp_path):
    state, item, _ = fixture(tmp_path)
    record = publish_exports(prepare(tmp_path, state, item))["items"][0]["record"]
    design = record["provenance"]["design"]
    assert (design["x"], design["y"]) == (2, 1)
    assert asset_design_issue(state, record) is None
    del design["basis"]
    assert "规格待核对" in asset_design_issue(state, record)
    with pytest.raises(ValueError, match="provenance.design.*basis"):
        verify_image_record(record)


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


@pytest.mark.parametrize("unity", [False, True])
def test_unchanged_export_does_not_rewrite_png_manifest_or_adjacent_file(tmp_path, unity):
    state, item, _ = fixture(tmp_path)
    if unity:
        make_unity_project(tmp_path, item)
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


def test_unity_exports_png_to_assets_and_keeps_manifest_and_sources_outside_assets(tmp_path):
    state, item, _ = fixture(tmp_path)
    make_unity_project(tmp_path, item)
    result = publish_exports(prepare(tmp_path, state, item))
    target = Path(result["items"][0]["record"]["path"])
    assert target == tmp_path / "Assets/UI/button.png"
    assert Path(result["manifest"]["path"]) == tmp_path / "smart-ui/exports/manifests/button.json"
    manifest = json.loads(Path(result["manifest"]["path"]).read_bytes())
    assert manifest["items"][0]["record"]["path"] == "Assets/UI/button.png"
    assert result["source"]["preview"]["path"] == "smart-ui/previews/components/button.png"
    assert [path for path in (tmp_path / "Assets").rglob("*") if path.is_file()] == [target]


@pytest.mark.parametrize("path", ["smart-ui/exports/ui/button.png", "smart-ui/exports/manifests/button.png",
                                  "smart-ui/sources/button.png", "Assets/UI/button.jpg", "AssetsElse/button.png"])
def test_unity_png_output_must_be_inside_assets(tmp_path, path):
    state, item, _ = fixture(tmp_path)
    make_unity_project(tmp_path, item)
    item["output_path"] = path
    with pytest.raises(ValueError, match="output_path"):
        prepare(tmp_path, state, item)
    assert not (tmp_path / "smart-ui/exports").exists()
    assert not list((tmp_path / "Assets").iterdir())


@pytest.mark.parametrize("fail_commit", [False, True])
def test_unity_reexport_and_commit_rollback_preserve_png_meta(tmp_path, fail_commit):
    state, item, source = fixture(tmp_path)
    make_unity_project(tmp_path, item)
    first = publish_exports(prepare(tmp_path, state, item))
    save_state(state, first)
    target = tmp_path / item["output_path"]
    meta = target.with_suffix(".png.meta")
    meta.write_bytes(b"guid: retained-guid\n")
    metadata = meta.read_bytes(), meta.stat().st_mtime_ns
    manifest = Path(first["manifest"]["path"])
    previous = target.read_bytes(), manifest.read_bytes()
    with Image.new("RGBA", (12, 10), "red") as image:
        image.save(source)
    state["components"]["button"]["latest_run"]["artifact_hashes"]["preview"] = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = prepare(tmp_path, state, item)
    assert plan["items"][0]["write_output"]

    def commit(result):
        assert target.read_bytes() != previous[0]
        assert manifest.read_bytes() != previous[1]
        raise ValueError("项目记录发布失败")

    if fail_commit:
        with pytest.raises(ValueError, match="项目记录发布失败"):
            publish_exports(plan, commit=commit)
        assert (target.read_bytes(), manifest.read_bytes()) == previous
    else:
        publish_exports(plan)
        assert target.read_bytes() != previous[0]
    assert (meta.read_bytes(), meta.stat().st_mtime_ns) == metadata


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
        prepare_exports(str(tmp_path), ProjectLayout(tmp_path).paths, state, "button", [item, second])
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
    plan = prepare_exports(str(tmp_path), ProjectLayout(tmp_path).paths, state, "button", [item, second])
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
    plan = prepare_exports(str(tmp_path), ProjectLayout(tmp_path).paths, state, "button", [item, second])
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
        register_image_file(str(tmp_path), ProjectLayout(tmp_path).paths, "fake", "伪造图像", str(source),
                            {"kind": "component_export"})
