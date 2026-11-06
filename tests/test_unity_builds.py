"""验证 Unity 请求调度、签名与结果核对，使用替身和合成预制体文件。"""

import json
import os
from pathlib import Path

from PIL import Image
import pytest

from smartui_mcp.projects import ProjectError, _sha
from smartui_mcp.unity import DOCUMENT, REGISTRY, UnityStore
from smartui_mcp.unity_builds import CONSTRUCTION, UnityBuildStore
from smartui_mcp.unity_cli import UnityCliError
from test_compositions import text_node
from test_control_pages import control
from test_page_layouts import exported_project, image_node, label, project_font, save, typography_project, typography_spec
from test_review_workflow import project
from test_unity import BUTTON_GUID, write_prefab
from test_unity_recipes import definition, slot


def define_image(builder, root, control_id, *, event=False, parameter=False):
    metadata = definition(slots={"background": slot("image")})
    targets = {"background": {"node_id": "bg", "component": "image"}}
    if event:
        metadata["slots"]["click"] = slot("event")
        targets["click"] = {"node_id": "root", "component": "button"}
    if parameter:
        metadata["parameters"]["art"] = {"slot_id": "background", "property": "asset_id",
                                         "value_type": "asset_id", "required": False, "description": ""}
    unity = builder.unity
    unity.define_control(root, unity.context(root)["revision"], control_id, metadata)
    return builder.define_recipe(root, builder.context(root)["revision"], control_id, {
        "kind": "base", "width": 18, "height": 12,
        "nodes": [image_node("bg", x=0, y=0)], "slots": targets,
        "button": {"target_graphic": "bg", "states": {}} if event else None,
    })


def button_state_edit(builder, root):
    define_image(builder, root, "button", event=True)
    context = builder.context(root)
    public = builder.unity.context(root)["controls"]["button"]["definition"]
    public["states"] = {"pressed": {"background": "button"}}
    recipe = context["construction"]["recipes"]["button"]
    recipe["button"]["states"] = {"pressed": "button"}
    return context, public, recipe


class FakeCli:
    def __init__(self):
        self.calls = []
        self.callback = None

    def execute(self, root, request_path):
        self.calls.append(request_path)
        if self.callback is not None:
            self.callback(root, request_path)


@pytest.fixture
def builder_project(exported_project):
    store, context = exported_project
    root = context["project_root"]
    cli = FakeCli()
    builder = UnityBuildStore(UnityStore(store), cli)
    builder.configure(root, None, {"output_root": "Assets/SmartUI", "fonts": {}})
    return root, store, builder, cli


def prepare(builder, root, control_ids, page_ids=(), operation="apply"):
    return builder.prepare(root, builder.context(root)["revision"], list(control_ids), list(page_ids), operation)


def prepared_result(request_path, statuses, *, overall="success", write_files=True):
    request_bytes = request_path.read_bytes()
    request = json.loads(request_bytes)
    root = request_path.parents[4]
    targets = []
    for target, status in zip(request["targets"], statuses, strict=True):
        item = {key: target[key] for key in ("kind", "id", "path")}
        item.update(status=status, instances={})
        if status in ("created", "updated", "verified", "unchanged"):
            output = root / target["path"]
            if write_files:
                output.parent.mkdir(parents=True, exist_ok=True)
                write_prefab(root, target["path"], BUTTON_GUID)
                baseline = root / "smart-ui/unity/import-state" / target["kind"] / (target["id"] + ".json")
                baseline.parent.mkdir(parents=True, exist_ok=True)
                baseline.write_text(json.dumps({
                    "project_id": request["project_id"], "path": target["path"], "guid": BUTTON_GUID,
                    "direct_sha256": target["direct_sha256"],
                }), encoding="utf-8")
            item.update(guid=BUTTON_GUID, sha256=_sha(output.read_bytes()),
                        meta_sha256=_sha(output.with_suffix(".prefab.meta").read_bytes()),
                        slot_bindings={key: 700 for key in target["slots"]}, source_guids=[])
        targets.append(item)
    return {"schema_version": 1, "request_id": request["request_id"], "request_sha256": _sha(request_bytes),
            "status": overall, "targets": targets, "error": "模拟失败" if overall == "failed" else None}


def write_result(request_path, result):
    request_path.with_name("result.json").write_text(json.dumps(result), encoding="utf-8")


def test_updates_public_button_states_and_recipe_together(builder_project):
    root, _, builder, _ = builder_project
    context, public, recipe = button_state_edit(builder, root)
    with pytest.raises(ProjectError, match="公开状态一致"):
        builder.define_recipe(root, context["revision"], "button", recipe)
    with pytest.raises(ProjectError, match="公开状态一致"):
        builder.unity.define_control(root, context["registry_revision"], "button", public)

    updated = builder.define_recipe(root, context["revision"], "button", recipe,
                                    definition=public, expected_registry_revision=context["registry_revision"])
    current = builder.context(root)
    assert updated["revision"] == current["revision"] != context["revision"]
    assert updated["registry_revision"] == current["registry_revision"] != context["registry_revision"]
    assert current["controls"]["button"]["button"]["states"] == {"pressed": "button"}
    assert builder.unity.context(root)["controls"]["button"]["definition"]["states"] == public["states"]


def test_combined_recipe_revision_conflicts_and_publication_failure_leave_both_records_intact(builder_project, monkeypatch):
    root, _, builder, _ = builder_project
    context, public, recipe = button_state_edit(builder, root)
    paths = [Path(root) / relative for relative in (REGISTRY, CONSTRUCTION, DOCUMENT)]
    original = [path.read_bytes() for path in paths]
    with pytest.raises(ProjectError, match="Unity 登记 revision 冲突"):
        builder.define_recipe(root, context["revision"], "button", recipe, definition=public,
                              expected_registry_revision="stale")
    with pytest.raises(ProjectError, match="Unity 构建资料 revision 冲突"):
        builder.define_recipe(root, "stale", "button", recipe, definition=public,
                              expected_registry_revision=context["registry_revision"])
    with pytest.raises(ProjectError, match="仅同时提供公开控件 definition"):
        builder.define_recipe(root, context["revision"], "button", recipe,
                              expected_registry_revision=context["registry_revision"])

    replace = os.replace

    def fail_registry(source, destination):
        if destination == paths[0]:
            assert paths[1].read_bytes() != original[1]
            raise OSError("模拟联合编辑发布失败")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_registry)
        with pytest.raises(ProjectError, match="模拟联合编辑发布失败"):
            builder.define_recipe(root, context["revision"], "button", recipe, definition=public,
                                  expected_registry_revision=context["registry_revision"])
    assert [path.read_bytes() for path in paths] == original


def test_applies_and_registers_verified_control_once(builder_project):
    root, _, builder, cli = builder_project
    define_image(builder, root, "button")
    prepared = prepare(builder, root, ["button"])
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created"]))

    result = builder.execute(root, prepared["request_id"])
    assert result["status"] == "completed"
    record = builder.unity.context(root)["controls"]["button"]
    assert record["file_status"] == "file_checked"
    assert record["prefab"]["file"]["guid"] == BUTTON_GUID
    assert builder.reconcile(root, prepared["request_id"]) == result
    with pytest.raises(ProjectError, match="不可重复执行"):
        builder.execute(root, prepared["request_id"])
    assert len(cli.calls) == 1
    write_result(Path(prepared["request_path"]), {})
    with pytest.raises(ProjectError, match="摘要已改变"):
        builder.reconcile(root, prepared["request_id"])


def test_registration_publish_failure_can_reconcile_same_result(builder_project, monkeypatch):
    root, _, builder, cli = builder_project
    define_image(builder, root, "button")
    prepared = prepare(builder, root, ["button"])
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created"]))
    replace = os.replace

    def fail_registration(source, destination):
        if destination == Path(root) / REGISTRY:
            raise OSError("模拟登记文件发布失败")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_registration)
        with pytest.raises(ProjectError, match="模拟登记文件发布失败"):
            builder.execute(root, prepared["request_id"])
    operation = json.loads(Path(prepared["request_path"]).with_name("operation.json").read_bytes())
    assert operation["status"] == "reconcile_required"
    assert operation["result_sha256"] is None
    assert builder.unity.context(root)["controls"]["button"]["file_status"] == "not_registered"
    assert builder.reconcile(root, prepared["request_id"])["status"] == "completed"
    assert builder.unity.context(root)["controls"]["button"]["file_status"] == "file_checked"
    assert len(cli.calls) == 1


def test_timeout_blocks_new_request_and_reconciles_original_result(builder_project):
    root, _, builder, cli = builder_project
    define_image(builder, root, "button")
    prepared = prepare(builder, root, ["button"])

    def timeout(_, path):
        raise UnityCliError("模拟等待超时", indeterminate=True)

    cli.callback = timeout
    result = builder.execute(root, prepared["request_id"])
    assert result["status"] == "indeterminate"
    assert builder.reconcile(root, prepared["request_id"])["status"] == "indeterminate"
    with pytest.raises(ProjectError, match="尚未核对"):
        prepare(builder, root, ["button"])
    path = Path(prepared["request_path"])
    write_result(path, prepared_result(path, ["created"]))
    assert builder.reconcile(root, prepared["request_id"])["status"] == "completed"
    assert len(cli.calls) == 1


@pytest.mark.parametrize("change", ["result", "related_source", "unrelated_source"])
def test_reconcile_checks_receipt_and_related_inputs_when_project_changes(builder_project, tmp_path, change):
    root, store, builder, cli = builder_project
    define_image(builder, root, "button")
    if change == "related_source":
        source = tmp_path / "alternate.png"
        provenance = {"kind": "imported", "description": "测试构建期间更新图像"}

        def replace_image(color):
            Image.new("RGB", (18, 12), color).save(source)
            store.register_image(root, store.context(root)["revision"], "alternate", "替换图像", str(source), provenance)

        replace_image("red")
        context = builder.context(root)
        recipe = context["construction"]["recipes"]["button"]
        recipe["nodes"][0]["asset_id"] = "alternate"
        builder.define_recipe(root, context["revision"], "button", recipe)
    prepared = prepare(builder, root, ["button"])

    def apply(_, path):
        result = prepared_result(path, ["created"])
        if change == "result":
            result["request_sha256"] = "0" * 64
        elif change == "related_source":
            replace_image("blue")
        else:
            save(store, store.context(root), [image_node()])
        write_result(path, result)

    cli.callback = apply
    if change == "unrelated_source":
        result = builder.execute(root, prepared["request_id"])
        assert result["status"] == "completed"
        assert builder.unity.context(root)["controls"]["button"]["file_status"] == "file_checked"
        assert builder.reconcile(root, prepared["request_id"]) == result
        assert prepare(builder, root, ["button"])["status"] == "prepared"
        return

    reason = "身份或摘要不符" if change == "result" else "制作输入已改变"
    with pytest.raises(ProjectError, match=reason):
        builder.execute(root, prepared["request_id"])
    assert builder.context(root)["operations"][0]["status"] == "reconcile_required"
    assert builder.unity.context(root)["controls"]["button"]["file_status"] == "not_registered"
    with pytest.raises(ProjectError, match="尚未核对"):
        prepare(builder, root, ["button"])


def test_stale_preparation_is_rejected_before_dispatch(builder_project):
    root, store, builder, cli = builder_project
    define_image(builder, root, "button")
    prepared = prepare(builder, root, ["button"], operation="plan")
    save(store, store.context(root), [image_node()])
    with pytest.raises(ProjectError, match="过期请求"):
        builder.execute(root, prepared["request_id"])
    assert cli.calls == []


@pytest.mark.parametrize("tamper_middle", [False, True])
def test_reconcile_checks_unselected_dependency_after_selected_ancestor_is_written(builder_project, tamper_middle):
    root, store, builder, cli = builder_project
    define_image(builder, root, "button")
    variants = {"middle": ("b" * 32, BUTTON_GUID), "outer": ("c" * 32, "b" * 32)}
    for control_id, base_id in (("middle", "button"), ("outer", "middle")):
        context = builder.context(root)
        builder.define_recipe(root, context["revision"], control_id, {"kind": "variant", "overrides": []},
                              definition=definition(base_control_id=base_id),
                              expected_registry_revision=context["registry_revision"])

    def write_targets(path, statuses):
        result = prepared_result(path, statuses)
        for item in result["targets"]:
            guid, base_guid = variants.get(item["id"], (BUTTON_GUID, None))
            write_prefab(Path(root), item["path"], guid, name=item["status"], nested_guid=base_guid)
            output = Path(root) / item["path"]
            item.update(guid=guid, sha256=_sha(output.read_bytes()),
                        meta_sha256=_sha(output.with_suffix(".prefab.meta").read_bytes()))
            if base_guid is not None:
                item.update(slot_bindings={}, source_guids=[base_guid])
                baseline = Path(root) / "smart-ui/unity/import-state/variant" / (item["id"] + ".json")
                baseline.write_text(json.dumps(json.loads(baseline.read_bytes()) | {"guid": guid}), encoding="utf-8")
        write_result(path, result)

    middle = prepare(builder, root, ["button", "middle"])
    cli.callback = lambda _, path: write_targets(path, ["created", "created"])
    assert builder.execute(root, middle["request_id"])["status"] == "completed"
    repeat = prepare(builder, root, ["button", "middle"], operation="plan")
    for first, second in zip(middle["request"]["targets"], repeat["request"]["targets"], strict=True):
        assert first["direct_sha256"] == second["direct_sha256"]
        assert first["dependency_sha256"] == second["dependency_sha256"]

    context = builder.context(root)
    recipe = context["construction"]["recipes"]["button"]
    recipe["nodes"][0].update(x=1, width=17)
    builder.define_recipe(root, context["revision"], "button", recipe)

    def apply(_, path):
        write_targets(path, ["updated", "created"])
        if tamper_middle:
            middle_path = middle["request"]["targets"][1]["path"]
            write_prefab(Path(root), middle_path, variants["middle"][0], name="Unrelated edit", nested_guid=BUTTON_GUID)
        save(store, store.context(root), [image_node()])

    cli.callback = apply
    batch = prepare(builder, root, ["button", "outer"])
    if tamper_middle:
        with pytest.raises(ProjectError, match="依赖控件 middle.*sha256"):
            builder.execute(root, batch["request_id"])
        return

    assert builder.execute(root, batch["request_id"])["status"] == "completed"
    assert all(item["file_status"] == "file_checked" for item in builder.unity.context(root)["controls"].values())


def test_partial_result_registers_only_successful_targets_and_is_idempotent(builder_project):
    root, _, builder, cli = builder_project
    define_image(builder, root, "button")
    define_image(builder, root, "other")
    prepared = prepare(builder, root, ["button", "other"])
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created", "failed"], overall="failed"))
    result = builder.execute(root, prepared["request_id"])
    assert result["status"] == "failed"
    controls = builder.unity.context(root)["controls"]
    assert controls["button"]["file_status"] == "file_checked"
    assert controls["other"]["file_status"] == "not_registered"
    assert builder.reconcile(root, prepared["request_id"]) == result


def test_duplicate_guid_in_build_results_prevents_registration(builder_project):
    root, _, builder, cli = builder_project
    define_image(builder, root, "button")
    define_image(builder, root, "other")
    prepared = prepare(builder, root, ["button", "other"])
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created", "created"]))
    with pytest.raises(ProjectError, match="路径或 GUID 已由控件 button 登记"):
        builder.execute(root, prepared["request_id"])
    assert builder.context(root)["operations"][0]["status"] == "reconcile_required"
    assert all(item["file_status"] == "not_registered" for item in builder.unity.context(root)["controls"].values())


def test_control_and_page_requests_must_be_prepared_separately(builder_project):
    root, _, builder, cli = builder_project
    with pytest.raises(ProjectError, match="分开准备控件和页面"):
        prepare(builder, root, ["button"], ["screen"])
    assert cli.calls == []
    assert builder.context(root)["operations"] == []


@pytest.mark.parametrize("identifiers, reason", [
    (["Root"], "保留 ID root"),
    (["Layer", "layer"], "layer 重复"),
])
def test_prepare_rejects_node_ids_reserved_or_duplicated_in_unity(builder_project, identifiers, reason):
    root, store, builder, cli = builder_project
    save(store, store.context(root), [image_node(identifier, x=2 + index * 18, y=2)
                                      for index, identifier in enumerate(identifiers)])

    with pytest.raises(ProjectError, match=reason):
        prepare(builder, root, [], ["screen"], "plan")

    assert cli.calls == []
    assert builder.context(root)["operations"] == []


def test_required_business_binding_checked_before_page_request(builder_project):
    root, store, builder, cli = builder_project
    define_image(builder, root, "button", event=True)
    save(store, store.context(root), [control("start", "button", x=2, y=2, width=18, height=12)])
    with pytest.raises(ProjectError, match="screen/start/click 缺少必需业务绑定"):
        prepare(builder, root, [], ["screen"], "plan")
    bindings = builder.set_bindings(root, builder.context(root)["revision"], "screen", {"start": {"click": "menu.start"}})
    assert bindings["construction"]["bindings"]["screen"]["start"]["click"] == "menu.start"
    assert cli.calls == []


def test_target_dependency_signature_does_not_include_an_unrelated_target(builder_project):
    root, _, builder, _ = builder_project
    define_image(builder, root, "button")
    define_image(builder, root, "other")
    alone = prepare(builder, root, ["button"], operation="plan")["request"]["targets"][0]
    combined = prepare(builder, root, ["button", "other"], operation="plan")["request"]["targets"][0]
    assert combined == alone


def test_explicit_asset_parameter_is_collected_and_pixel_change_only_affects_dependency(builder_project, tmp_path):
    root, store, builder, cli = builder_project
    define_image(builder, root, "button", parameter=True)
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created"]))
    prepared = prepare(builder, root, ["button"])
    assert builder.execute(root, prepared["request_id"])["status"] == "completed"

    source = tmp_path / "alternate.png"
    provenance = {"kind": "imported", "description": "测试替换图像"}

    def replace_image(color):
        Image.new("RGB", (18, 12), color).save(source)
        store.register_image(root, store.context(root)["revision"], "alternate", "替换图像", str(source), provenance)

    replace_image("red")
    save(store, store.context(root), [
        control("start", "button", x=2, y=2, width=18, height=12, parameters={"art": "alternate"}),
    ])
    first = prepare(builder, root, [], ["screen"], "plan")["request"]
    assert "alternate" in first["assets"]
    assert first["controls"]["button"]["sha256"]
    assert first["controls"]["button"]["meta_sha256"]
    replace_image("blue")
    second = prepare(builder, root, [], ["screen"], "plan")["request"]
    assert first["assets"]["alternate"]["sha256"] != second["assets"]["alternate"]["sha256"]
    assert first["targets"][0]["direct_sha256"] == second["targets"][0]["direct_sha256"]
    assert first["targets"][0]["dependency_sha256"] != second["targets"][0]["dependency_sha256"]


def test_generated_dependency_must_apply_current_recipe_before_page_prepare(builder_project):
    root, store, builder, cli = builder_project
    define_image(builder, root, "button")
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created"]))
    control_build = prepare(builder, root, ["button"])
    assert builder.execute(root, control_build["request_id"])["status"] == "completed"
    save(store, store.context(root), [control("start", "button", x=2, y=2, width=18, height=12)])
    page_before = prepare(builder, root, [], ["screen"], "plan")["request"]["targets"][0]
    assert page_before["background"] == store.context(root)["data"]["layouts"]["screen"]["definition"]["background"]

    construction = builder.context(root)
    recipe = construction["construction"]["recipes"]["button"]
    recipe["nodes"][0].update(x=1, width=17)
    builder.define_recipe(root, construction["revision"], "button", recipe)
    with pytest.raises(ProjectError, match="请先构建依赖控件 button.*构建基线不一致"):
        prepare(builder, root, [], ["screen"], "plan")

    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["updated"]))
    control_update = prepare(builder, root, ["button"])
    assert builder.execute(root, control_update["request_id"])["status"] == "completed"
    page_after = prepare(builder, root, [], ["screen"], "plan")["request"]["targets"][0]
    assert page_before["direct_sha256"] == page_after["direct_sha256"]
    assert page_before["dependency_sha256"] != page_after["dependency_sha256"]


@pytest.mark.parametrize(("problem", "reason"), [
    ("missing_slot", "实例 confirm 槽位与请求不一致"),
    ("shared_scope", "与另一实例 confirm 共用容器 fileID"),
    ("missing_scope", "缺失的对象 fileID 999"),
    ("zero_scope", "非零有符号 64 位整数"),
    ("wrong_source", "来源 GUID、fileID 或控件 ID 与登记不一致"),
    (None, None),
])
def test_page_result_validates_each_nested_instance_scope(builder_project, problem, reason):
    root, store, builder, cli = builder_project
    define_image(builder, root, "button")
    built = prepare(builder, root, ["button"])
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created"]))
    assert builder.execute(root, built["request_id"])["status"] == "completed"
    save(store, store.context(root), [
        control("confirm", "button", x=2, y=2, width=18, height=12),
        control("cancel", "button", x=24, y=2, width=18, height=12),
    ])
    prepared = prepare(builder, root, [], ["screen"])

    def create_page(_, request_path):
        request_bytes = request_path.read_bytes()
        request = json.loads(request_bytes)
        target = request["targets"][0]
        output = Path(root) / target["path"]
        output.parent.mkdir(parents=True)
        page_guid = "b" * 32
        write_prefab(Path(root), target["path"], page_guid, nested_guid=BUTTON_GUID)
        with output.open("a", encoding="utf-8") as stream:
            for file_id in (701, 702):
                stream.write(f"--- !u!224 &{file_id}\nRectTransform:\n  m_GameObject: {{fileID: 0}}\n")
        source = {"control_id": "button", "guid": BUTTON_GUID, "file_id": 700}
        instances = {
            "confirm": {"background": {**source, "scope_file_id": 701}},
            "cancel": {"background": {**source, "scope_file_id": 702}},
        }
        if problem == "missing_slot":
            instances["confirm"].clear()
        elif problem in ("shared_scope", "missing_scope", "zero_scope"):
            instances["cancel"]["background"]["scope_file_id"] = {"shared_scope": 701, "missing_scope": 999, "zero_scope": 0}[problem]
        elif problem == "wrong_source":
            instances["cancel"]["background"]["file_id"] = 702
        write_result(request_path, {
            "schema_version": 1, "request_id": request["request_id"], "request_sha256": _sha(request_bytes),
            "status": "success", "targets": [{
                "kind": "page", "id": target["id"], "path": target["path"], "status": "created",
                "guid": page_guid, "sha256": _sha(output.read_bytes()),
                "meta_sha256": _sha(output.with_suffix(".prefab.meta").read_bytes()),
                "slot_bindings": {}, "source_guids": [BUTTON_GUID], "instances": instances,
            }],
        })

    cli.callback = create_page
    if reason is None:
        result = builder.execute(root, prepared["request_id"])
        assert result["status"] == "completed"
        assert result["result"]["targets"][0]["slot_bindings"] == {}
    else:
        with pytest.raises(ProjectError, match=reason):
            builder.execute(root, prepared["request_id"])
        statuses = {item["request_id"]: item["status"] for item in builder.context(root)["operations"]}
        assert statuses[prepared["request_id"]] == "reconcile_required"


def test_font_asset_bytes_affect_control_direct_signature_without_rewriting_page(typography_project, label):
    store, context = typography_project
    root = context["project_root"]
    cli = FakeCli()
    builder = UnityBuildStore(UnityStore(store), cli)
    font_path, material_path = "Assets/Fonts/TMP.asset", "Assets/Fonts/TMP.mat"
    for path, guid in ((font_path, "f" * 32), (material_path, "a" * 32)):
        (Path(root) / path).write_bytes(b"synthetic")
        (Path(root) / (path + ".meta")).write_text(f"guid: {guid}\n", encoding="utf-8")
    builder.configure(root, None, {"output_root": "Assets/SmartUI", "fonts": {"button-label": {
        "font_asset_path": font_path, "font_asset_guid": "f" * 32,
        "material_path": material_path, "material_guid": "a" * 32,
    }}})
    builder.unity.define_control(root, None, "button", definition(slots={"label": slot("text")}))
    builder.define_recipe(root, builder.context(root)["revision"], "button", {
        "kind": "base", "width": 44, "height": 34, "button": None,
        "nodes": [{**label, "x": 0, "width": 44, "wrap": False, "text": "A"}],
        "slots": {"label": {"node_id": "label", "component": "text"}},
    })
    first = prepare(builder, root, ["button"])
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["created"]))
    assert builder.execute(root, first["request_id"])["status"] == "completed"
    save(store, store.context(root), [control("start", "button", x=2, y=2)])
    page_before = prepare(builder, root, [], ["screen"], "plan")["request"]["targets"][0]

    (Path(root) / font_path).write_bytes(b"new synthetic metrics")
    second = prepare(builder, root, ["button"])
    assert first["request"]["targets"][0]["direct_sha256"] != second["request"]["targets"][0]["direct_sha256"]
    with pytest.raises(ProjectError, match="请先构建依赖控件 button"):
        prepare(builder, root, [], ["screen"], "plan")
    cli.callback = lambda _, path: write_result(path, prepared_result(path, ["updated"]))
    assert builder.execute(root, second["request_id"])["status"] == "completed"
    page_after = prepare(builder, root, [], ["screen"], "plan")["request"]["targets"][0]
    assert page_before["direct_sha256"] == page_after["direct_sha256"]
    assert page_before["dependency_sha256"] != page_after["dependency_sha256"]
