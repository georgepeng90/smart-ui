"""用合成文件验证 Unity 控件登记与交付协议。"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from smartui_mcp.projects import ProjectError, ProjectStore, spec_sha256, style_sha256
from smartui_mcp.unity import DOCUMENT, REGISTRY, UnityStore
from test_compositions import text_node


BUTTON_GUID = "a" * 32
WRAPPER_GUID = "b" * 32
BUTTON_PATH = "Assets/Button.prefab"
WRAPPER_PATH = "Assets/ButtonWrapper.prefab"


def write_prefab(root, path, guid, *, name="SyntheticButton", file_id=700, nested_guid=None):
    # 仅供文本协议校验的合成对象和引用。
    (root / "Assets").mkdir(exist_ok=True)
    prefab = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
    if file_id is not None:
        prefab += f"--- !u!114 &{file_id}\nMonoBehaviour:\n  m_Name: {name}\n"
    if nested_guid is not None:
        prefab += "--- !u!1001 &800\nPrefabInstance:\n"
        prefab += f"  m_SourcePrefab: {{fileID: 100100000, guid: {nested_guid}, type: 3}}\n"
    (root / path).write_text(prefab, encoding="utf-8")
    (root / (path + ".meta")).write_text(f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8")


def button_definition():
    return {
        "name": "主按钮", "description": "用于页面主操作", "control_type": "button",
        "base_control_id": None, "dependencies": [], "source_component_ids": [],
        "asset_ids": [], "text_style_ids": [], "preview_asset_id": None,
        "layout": {"min_width": 20, "min_height": 10, "max_width": None, "max_height": None},
        "slots": {"label": {"kind": "text", "component_type": "Label", "required": True, "description": "文案"}},
        "parameters": {"caption": {"slot_id": "label", "property": "text", "value_type": "string",
                                  "required": True, "description": "页面文案"}},
        "states": {},
    }


def handoff_request(control_id="button"):
    return {
        "schema_version": 1, "adapter_id": "test", "adapter_version": "1",
        "pages": [
            {"id": page_id, "name": page_name, "width": 320, "height": 200, "instances": [
                {"id": "primary", "control_id": control_id, "x": 10, "y": 12, "width": 100, "height": 36,
                 "parameters": {"caption": caption}, "bindings": {}},
            ]}
            for page_id, page_name, caption in (("home", "主页", "开始"), ("shop", "商店", "购买"))
        ],
    }


def persistent_files(root):
    return {path.relative_to(root): path.read_bytes() for path in (root / "smart-ui").rglob("*") if path.is_file()}


def test_usage_description_does_not_invalidate_prefab_or_page_inputs(project, registered_button):
    root, _, store = project
    first = store.prepare_handoff(str(root), registered_button["revision"], "menu", handoff_request())
    definition = button_definition()
    definition["description"] = "更明确的使用说明"
    definition["slots"]["label"]["description"] = "展示页面操作名称"
    updated = store.define_control(str(root), first["revision"], "button", definition)
    assert updated["controls"]["button"]["file_status"] == "file_checked"
    prepared = store.prepare_handoff(str(root), updated["revision"], "menu", handoff_request())
    assert set(prepared["page_changes"].values()) == {"unchanged"}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "game"
    projects = ProjectStore()
    projects.create(str(root), "game", "测试项目")
    return root, projects, UnityStore(projects)


@pytest.fixture
def registered_button(project):
    root, _, store = project
    write_prefab(root, BUTTON_PATH, BUTTON_GUID)
    draft = store.define_control(str(root), None, "button", button_definition())
    return store.register_prefab(str(root), draft["revision"], "button", BUTTON_PATH, BUTTON_GUID, {"label": 700})


def test_unconfigured_catalog_is_read_only_and_registration_survives_project_move(project, tmp_path):
    root, projects, store = project
    initial = persistent_files(root)
    context = store.context(str(root))
    assert context["revision"] is None and not context["configured"]
    assert context["controls"] == {} and context["document"] is None
    assert persistent_files(root) == initial

    draft = store.define_control(str(root), None, "button", button_definition())
    assert draft["controls"]["button"]["file_status"] == "not_registered"
    before_registration = persistent_files(root)
    write_prefab(root, BUTTON_PATH, BUTTON_GUID)
    with pytest.raises(ProjectError, match="revision 冲突"):
        store.register_prefab(str(root), None, "button", BUTTON_PATH, BUTTON_GUID, {"label": 700})
    assert persistent_files(root) == before_registration
    saved = store.register_prefab(str(root), draft["revision"], "button", BUTTON_PATH, BUTTON_GUID, {"label": 700})
    assert saved["controls"]["button"]["file_status"] == "file_checked"
    assert saved["controls"]["button"]["unity_validation"] == "not_performed"
    assert projects.context(str(root))["revision"] == context["source_revision"]
    document = (root / DOCUMENT).read_bytes()
    assert "主按钮（button）" in document.decode("utf-8")
    assert "(<../../Assets/Button.prefab>)" in document.decode("utf-8")
    assert json.loads((root / REGISTRY).read_bytes())["document_sha256"] == hashlib.sha256(document).hexdigest()

    moved = tmp_path / "moved"
    shutil.move(root, moved)
    cache = moved / ".smartui"
    assert cache.resolve() == tmp_path.resolve() / "moved" / ".smartui"
    shutil.rmtree(cache)
    reloaded = store.context(str(moved))
    assert reloaded["revision"] == saved["revision"]
    assert reloaded["controls"]["button"]["file_status"] == "file_checked"
    assert Path(reloaded["document"]) == moved / DOCUMENT
    assert (moved / DOCUMENT).read_bytes() == document


@pytest.mark.parametrize("has_unity_control", [False, True], ids=["unconfigured", "referenced"])
def test_typography_role_can_only_be_removed_without_a_unity_reference(project, text_node, has_unity_control):
    root, projects, store = project
    font = root / "fixture.ttf"
    shutil.copyfile(text_node["font_path"], font)
    typography = {
        "description": "",
        "design_size": {"width": 320, "height": 200},
        "size_scale": {"regular": 20},
        "styles": {"body": {
            "name": "正文", "usage": "按钮文案", "size_id": "regular", "font_path": str(font),
            "line_height": 24, "letter_spacing": 0, "fill": {"color": "#FFFFFF", "opacity": 1},
            "stroke": None, "shadow": None, "evidence": [],
        }},
    }
    source = projects.update_typography(str(root), projects.context(str(root))["revision"], typography)
    without_role = typography | {"styles": {}}
    unity_revision = None
    if has_unity_control:
        definition = button_definition() | {"text_style_ids": ["body"]}
        control = store.define_control(str(root), None, "button", definition)
        before = persistent_files(root)
        with pytest.raises(ProjectError, match=r"不能移除文字样式 body.*Unity 控件 button 仍在引用"):
            projects.update_typography(str(root), source["revision"], without_role)
        assert persistent_files(root) == before
        assert projects.context(str(root))["revision"] == source["revision"]
        assert store.context(str(root))["revision"] == control["revision"]
        control = store.define_control(str(root), control["revision"], "button", button_definition())
        unity_revision = control["revision"]

    updated = projects.update_typography(str(root), source["revision"], without_role)
    assert updated["data"]["typography"]["styles"] == {}
    assert store.context(str(root))["revision"] == unity_revision


def test_publication_failure_restores_catalog_document_and_handoff(project, registered_button, monkeypatch):
    root, _, store = project
    before = persistent_files(root)
    replace = os.replace

    def fail_registry(source, destination):
        if destination == root / REGISTRY:
            assert (root / "smart-ui/unity/handoffs/menu.json").is_file()
            assert (root / DOCUMENT).read_bytes() != before[Path(DOCUMENT)]
            raise OSError("模拟登记发布中断")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_registry)
        with pytest.raises(ProjectError, match="模拟登记发布中断"):
            store.prepare_handoff(str(root), registered_button["revision"], "menu", handoff_request())
    assert persistent_files(root) == before
    assert store.context(str(root))["revision"] == registered_button["revision"]


@pytest.mark.parametrize("fault, match", [
    ("missing_file", r"Assets/Button\.prefab.*现存文件"),
    ("guid", r"Assets/Button\.prefab\.meta.*GUID"),
    ("slot", r"槽位 'label'.*fileID 999"),
])
def test_prefab_registration_reports_file_guid_and_object_errors(project, fault, match):
    root, _, store = project
    draft = store.define_control(str(root), None, "button", button_definition())
    if fault != "missing_file":
        write_prefab(root, BUTTON_PATH, BUTTON_GUID)
    before = persistent_files(root)
    with pytest.raises(ProjectError, match=match):
        store.register_prefab(str(root), draft["revision"], "button", BUTTON_PATH,
                              WRAPPER_GUID if fault == "guid" else BUTTON_GUID,
                              {"label": 999 if fault == "slot" else 700})
    assert persistent_files(root) == before


def test_shared_nested_prefab_change_updates_dependencies_only(project, registered_button):
    root, _, store = project
    definition = button_definition() | {"name": "按钮组合", "dependencies": ["button"]}
    wrapper = store.define_control(str(root), registered_button["revision"], "wrapper", definition)
    write_prefab(root, WRAPPER_PATH, WRAPPER_GUID, nested_guid=BUTTON_GUID)
    wrapper = store.register_prefab(str(root), wrapper["revision"], "wrapper", WRAPPER_PATH,
                                    WRAPPER_GUID, {"label": 700})
    request = handoff_request("wrapper")
    first = store.prepare_handoff(str(root), wrapper["revision"], "menu", request)
    assert first["page_changes"] == {"home": "new", "shop": "new"}
    assert set(first["handoff"]["controls"]) == {"button", "wrapper"}
    assert first["handoff"]["controls"]["wrapper"]["prefab"]["source_guids"] == [BUTTON_GUID]
    assert first["handoff"]["unity_validation"] == "not_performed"
    before = persistent_files(root)
    same = store.prepare_handoff(str(root), first["revision"], "menu", request)
    assert same["revision"] == first["revision"]
    assert same["page_changes"] == {"home": "unchanged", "shop": "unchanged"}
    assert persistent_files(root) == before

    write_prefab(root, BUTTON_PATH, BUTTON_GUID, name="UpdatedSyntheticButton")
    invalid = store.context(str(root))["controls"]["button"]
    assert invalid["file_status"] == "invalid"
    assert BUTTON_PATH in invalid["issue"]["message"]
    with pytest.raises(ProjectError, match=r"控件 button.*sha256"):
        store.prepare_handoff(str(root), same["revision"], "menu", request)
    assert persistent_files(root) == before
    updated = store.register_prefab(str(root), same["revision"], "button", BUTTON_PATH, BUTTON_GUID, {"label": 700})
    updated = store.prepare_handoff(str(root), updated["revision"], "menu", request)
    assert updated["page_changes"] == {"home": "dependencies_changed", "shop": "dependencies_changed"}
    assert [page["instances"][0]["parameters"]["caption"] for page in updated["handoff"]["pages"]] == ["开始", "购买"]
    for original, changed in zip(first["handoff"]["pages"], updated["handoff"]["pages"], strict=True):
        assert changed["direct_sha256"] == original["direct_sha256"]
        assert changed["dependency_sha256"] != original["dependency_sha256"]
    assert json.loads(Path(updated["handoff_path"]).read_bytes()) == updated["handoff"]


@pytest.mark.parametrize("parameters, match", [
    ({}, r"页面 home.*caption.*缺少必需参数"),
    ({"caption": "开始", "opacity": 1}, r"页面 home.*opacity.*未在控件中公开"),
])
def test_invalid_instance_overrides_leave_persisted_handoff_unchanged(project, registered_button, parameters, match):
    root, _, store = project
    request = handoff_request()
    first = store.prepare_handoff(str(root), registered_button["revision"], "menu", request)
    before = persistent_files(root)
    request["pages"][0]["instances"][0]["parameters"] = parameters
    with pytest.raises(ProjectError, match=match):
        store.prepare_handoff(str(root), first["revision"], "menu", request)
    assert persistent_files(root) == before


@pytest.mark.parametrize("fault, match", [
    ("schema", "schema_version=1"),
    ("document", "受管文档已被修改"),
])
def test_unknown_registry_schema_and_edited_document_are_rejected(project, registered_button, fault, match):
    root, _, store = project
    if fault == "schema":
        path = root / REGISTRY
        payload = json.loads(path.read_bytes())
        payload["schema_version"] = 2
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        (root / DOCUMENT).write_text("手工编辑的文档", encoding="utf-8")
    before = persistent_files(root)
    with pytest.raises(ProjectError, match=match):
        store.context(str(root))
    with pytest.raises(ProjectError, match=match):
        store.define_control(str(root), registered_button["revision"], "button", button_definition())
    assert persistent_files(root) == before


def test_handoff_validates_registered_source_asset_file(project, registered_button, tmp_path):
    root, projects, store = project
    png = tmp_path / "background.png"
    Image.new("RGB", (4, 4), "red").save(png)
    source = projects.context(str(root))
    projects.register_image(str(root), source["revision"], "background", "背景", str(png),
                            {"kind": "imported", "description": "合成图像"})
    current = projects.context(str(root))
    projects.set_public_images(str(root), current["revision"], ["background"])
    candidate = store.search_reusable(str(root), "合成图像")["images"]["background"]
    assert candidate["width"] == 4 and candidate["source_issue"] is None
    assert "button" in store.search_reusable(str(root), "button")["controls"]
    definition = button_definition() | {"asset_ids": ["background"]}
    edited = store.define_control(str(root), registered_button["revision"], "button", definition)
    registered = store.register_prefab(str(root), edited["revision"], "button", BUTTON_PATH,
                                       BUTTON_GUID, {"label": 700})
    request = handoff_request()
    handoff = store.prepare_handoff(str(root), registered["revision"], "menu", request)
    assert handoff["handoff"]["assets"]["background"]["sha256"] == hashlib.sha256(png.read_bytes()).hexdigest()
    path = root / handoff["handoff"]["assets"]["background"]["path"]
    Image.new("RGB", (4, 4), "blue").save(path)
    before = persistent_files(root)
    with pytest.raises(ProjectError, match=r"页面 home.*background.*image\.png.*SHA-256"):
        store.prepare_handoff(str(root), handoff["revision"], "menu", request)
    assert persistent_files(root) == before


def test_handoff_document_order_is_stable_after_reopening_registry(project, registered_button):
    root, _, store = project
    request = handoff_request()
    last = store.prepare_handoff(str(root), registered_button["revision"], "z-menu", request)
    first = store.prepare_handoff(str(root), last["revision"], "a-menu", request)
    document = (root / DOCUMENT).read_bytes()
    assert store.context(str(root))["revision"] == first["revision"]
    repeated = store.prepare_handoff(str(root), first["revision"], "a-menu", request)
    assert repeated["revision"] == first["revision"]
    assert repeated["page_changes"] == {"home": "unchanged", "shop": "unchanged"}
    assert (root / DOCUMENT).read_bytes() == document


@pytest.mark.parametrize("fault, match", [
    ("prefab_file", r"控件 button 预制体文件.*slot_bindings"),
    ("page_signatures", r"接入计划 menu.*page_signatures"),
])
def test_malformed_registry_records_report_configuration_errors(project, registered_button, fault, match):
    root, _, store = project
    store.prepare_handoff(str(root), registered_button["revision"], "menu", handoff_request())
    path = root / REGISTRY
    registry = json.loads(path.read_bytes())
    if fault == "prefab_file":
        registry["controls"]["button"]["prefab"]["file"]["slot_bindings"] = []
    else:
        registry["handoffs"]["menu"]["page_signatures"] = None
    path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ProjectError, match=match):
        store.context(str(root))


def test_variant_inherits_source_slot_identity_and_requires_reregistration_after_it_changes(project, registered_button):
    root, _, store = project
    definition = button_definition() | {"name": "按钮变体", "base_control_id": "button", "slots": {}, "parameters": {}}
    draft = store.define_control(str(root), registered_button["revision"], "variant", definition)
    write_prefab(root, WRAPPER_PATH, WRAPPER_GUID, file_id=None, nested_guid=BUTTON_GUID)
    saved = store.register_prefab(str(root), draft["revision"], "variant", WRAPPER_PATH, WRAPPER_GUID, {})
    persisted = json.loads((root / REGISTRY).read_bytes())["controls"]["variant"]["prefab"]
    assert persisted["file"]["slot_bindings"] == {}
    assert persisted["slot_sources"] == {"label": {"control_id": "button", "file_id": 700}}
    request = handoff_request("variant")
    first = store.prepare_handoff(str(root), saved["revision"], "menu", request)
    assert first["handoff"]["controls"]["variant"]["slot_sources"] == persisted["slot_sources"]

    write_prefab(root, BUTTON_PATH, BUTTON_GUID, file_id=701)
    updated = store.register_prefab(str(root), first["revision"], "button", BUTTON_PATH, BUTTON_GUID, {"label": 701})
    assert updated["controls"]["button"]["file_status"] == "file_checked"
    assert updated["controls"]["variant"]["file_status"] == "invalid"
    assert "继承槽位的对象身份已改变" in updated["controls"]["variant"]["issue"]["message"]
    with pytest.raises(ProjectError, match=r"控件 variant.*继承槽位"):
        store.prepare_handoff(str(root), updated["revision"], "menu", request)
    saved = store.register_prefab(str(root), updated["revision"], "variant", WRAPPER_PATH, WRAPPER_GUID, {})
    final = store.prepare_handoff(str(root), saved["revision"], "menu", request)
    assert final["handoff"]["controls"]["variant"]["slot_sources"] == {"label": {"control_id": "button", "file_id": 701}}


def test_variant_new_slot_requires_a_local_file_id(project, registered_button):
    root, _, store = project
    definition = button_definition() | {
        "name": "按钮变体", "base_control_id": "button", "parameters": {},
        "slots": {"subtitle": {"kind": "text", "component_type": "Label", "required": False, "description": "副标题"}},
    }
    draft = store.define_control(str(root), registered_button["revision"], "variant", definition)
    write_prefab(root, WRAPPER_PATH, WRAPPER_GUID, file_id=702, nested_guid=BUTTON_GUID)
    with pytest.raises(ProjectError, match=r"variant.*缺少本地公开槽位 subtitle"):
        store.register_prefab(str(root), draft["revision"], "variant", WRAPPER_PATH, WRAPPER_GUID, {})
    saved = store.register_prefab(str(root), draft["revision"], "variant", WRAPPER_PATH, WRAPPER_GUID, {"subtitle": 702})
    assert saved["controls"]["variant"]["prefab"]["slot_sources"] == {
        "label": {"control_id": "button", "file_id": 700},
        "subtitle": {"control_id": "variant", "file_id": 702},
    }


def test_variant_chain_must_reregister_in_dependency_order(project, registered_button):
    root, _, store = project
    definition = button_definition() | {"name": "按钮变体", "base_control_id": "button", "slots": {}, "parameters": {}}
    variant = store.define_control(str(root), registered_button["revision"], "variant", definition)
    write_prefab(root, WRAPPER_PATH, WRAPPER_GUID, file_id=None, nested_guid=BUTTON_GUID)
    variant = store.register_prefab(str(root), variant["revision"], "variant", WRAPPER_PATH, WRAPPER_GUID, {})
    grand_path, grand_guid = "Assets/Grand.prefab", "c" * 32
    grand = store.define_control(str(root), variant["revision"], "grand", definition | {"base_control_id": "variant"})
    write_prefab(root, grand_path, grand_guid, file_id=None, nested_guid=WRAPPER_GUID)
    grand = store.register_prefab(str(root), grand["revision"], "grand", grand_path, grand_guid, {})
    assert grand["controls"]["grand"]["prefab"]["slot_sources"] == {"label": {"control_id": "button", "file_id": 700}}

    write_prefab(root, BUTTON_PATH, BUTTON_GUID, file_id=701)
    store.register_prefab(str(root), grand["revision"], "button", BUTTON_PATH, BUTTON_GUID, {"label": 701})
    current = store.context(str(root))
    assert current["controls"]["variant"]["file_status"] == "invalid"
    assert current["controls"]["grand"]["file_status"] == "invalid"
    before = persistent_files(root)
    with pytest.raises(ProjectError, match=r"先处理依赖控件 variant.*继承槽位"):
        store.register_prefab(str(root), current["revision"], "grand", grand_path, grand_guid, {})
    assert persistent_files(root) == before

    variant = store.register_prefab(str(root), current["revision"], "variant", WRAPPER_PATH, WRAPPER_GUID, {})
    grand = store.register_prefab(str(root), variant["revision"], "grand", grand_path, grand_guid, {})
    assert all(control["file_status"] == "file_checked" for control in grand["controls"].values())
    assert grand["controls"]["grand"]["prefab"]["slot_sources"] == {"label": {"control_id": "button", "file_id": 701}}


@pytest.mark.skipif(os.name != "nt", reason="目录联接是 Windows 功能")
def test_catalog_reports_junction_for_registered_prefab_without_blocking_context(project, registered_button):
    root, _, store = project
    directory = root / "Assets"
    target = root / "MovedAssets"
    directory.rename(target)
    try:
        subprocess.run(["cmd", "/d", "/c", "mklink", "/j", str(directory), str(target)],
                       check=True, capture_output=True)
        context = store.context(str(root))
        assert context["revision"] == registered_button["revision"]
        control = context["controls"]["button"]
        assert control["file_status"] == "invalid"
        assert "目录联接" in control["issue"]["message"]
    finally:
        if directory.is_junction():
            assert directory.resolve() == target.resolve()
            directory.rmdir()
        assert not directory.exists()
        target.rename(directory)

    restored = store.register_prefab(str(root), registered_button["revision"], "button",
                                     BUTTON_PATH, BUTTON_GUID, {"label": 700})
    assert restored["controls"]["button"]["file_status"] == "file_checked"


def test_handoff_rejects_image_record_pointing_to_identical_external_file(project, registered_button, tmp_path):
    root, projects, store = project
    png = tmp_path / "background.png"
    Image.new("RGB", (4, 4), "red").save(png)
    projects.register_image(str(root), projects.context(str(root))["revision"], "background", "背景", str(png),
                            {"kind": "imported", "description": "合成图像"})
    definition = button_definition() | {"asset_ids": ["background"]}
    edited = store.define_control(str(root), registered_button["revision"], "button", definition)
    registered = store.register_prefab(str(root), edited["revision"], "button", BUTTON_PATH, BUTTON_GUID, {"label": 700})
    index = json.loads((root / "smart-ui/project.json").read_bytes())
    record_path = root / index["images"]["background"]
    record = json.loads(record_path.read_bytes())
    assert hashlib.sha256(png.read_bytes()).hexdigest() == record["sha256"]
    record["path"] = str(png)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    before = persistent_files(root)
    with pytest.raises(ProjectError, match=r"Unity 资产 background.*越出工程根目录"):
        store.prepare_handoff(str(root), registered["revision"], "menu", handoff_request())
    assert persistent_files(root) == before


@pytest.mark.parametrize("fault, match", [
    ("missing", r"Unity 来源组件 artwork/preview.*现存文件"),
    ("modified", r"SHA-256.*artwork.*preview\.png"),
    ("invalid_plan", r"运行计划或风格快照不是有效 JSON：artwork"),
    ("mismatched_snapshot", r"运行风格快照与记录的风格摘要或组件规格不一致：artwork"),
])
def test_handoff_checks_actual_component_artifacts(project, registered_button, fault, match):
    root, projects, store = project
    source = projects.context(str(root))
    source = projects.save_program(str(root), source["revision"], "drawing", "程序", "", "result = []\n")
    spec = {"program_id": "drawing"}
    source = projects.define_component(str(root), source["revision"], "artwork", "视觉组件", spec)
    directory = root / "smart-ui/sources/artwork"
    directory.mkdir(parents=True)
    run = {}
    for field, filename in (("document", "button.pcraft"), ("preview", "preview.png"),
                            ("plan_path", "plan.json"), ("style_snapshot_path", "inputs.json")):
        path = directory / filename
        path.write_bytes(f"Synthetic artifact: {filename}".encode())
        run[field] = str(path)
    plan = Path(run["plan_path"])
    snapshot = Path(run["style_snapshot_path"])
    style = source["data"]["style"]
    plan.write_text(json.dumps({"nodes": []}), encoding="utf-8")
    snapshot_data = {"style": style, "component_id": "artwork", "spec": spec}
    snapshot.write_text(json.dumps(snapshot_data), encoding="utf-8")
    run |= {
        "run_id": "synthetic", "status": "drawn", "style_sha256": style_sha256(style),
        "spec_sha256": spec_sha256(spec), "program_id": "drawing",
        "program_sha256": source["data"]["programs"]["drawing"]["sha256"],
        "backend": {"name": "photocraft", "version": "synthetic fixture", "sha256": "0" * 64,
                    "cli_path": str(root / "synthetic.exe")},
    }
    source = projects.record_run(str(root), source["revision"], "artwork", run)
    definition = button_definition() | {"source_component_ids": ["artwork"]}
    edited = store.define_control(str(root), registered_button["revision"], "button", definition)
    registered = store.register_prefab(str(root), edited["revision"], "button", BUTTON_PATH, BUTTON_GUID, {"label": 700})
    request = handoff_request()
    handoff = store.prepare_handoff(str(root), registered["revision"], "menu", request)
    assert "artwork" in handoff["handoff"]["component_inputs"]
    preview = Path(run["preview"])
    if fault == "missing":
        preview.unlink()
    elif fault == "modified":
        preview.write_bytes(b"Modified synthetic artifact")
    elif fault == "invalid_plan":
        plan.write_bytes(b"Invalid plan JSON")
        projects.record_run(str(root), source["revision"], "artwork", run)
    else:
        snapshot.write_text(json.dumps(snapshot_data | {"spec": {}}), encoding="utf-8")
        projects.record_run(str(root), source["revision"], "artwork", run)
    before = persistent_files(root)
    with pytest.raises(ProjectError, match=match):
        store.prepare_handoff(str(root), handoff["revision"], "menu", request)
    assert persistent_files(root) == before
