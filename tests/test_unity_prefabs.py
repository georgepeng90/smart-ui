from hashlib import sha256
import os
import subprocess

import pytest

from smartui_mcp.projects import ProjectError
from smartui_mcp.unity_prefabs import inspect_prefab, verify_prefab


PATH = "Assets/UI/Button.prefab"
GUID = "1234567890abcdef1234567890abcdef"
SOURCE_GUID = "234567890abcdef1234567890abcdef1"
SLOT_FILE_ID = -(2**63)
HEADER = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"

# 合成文本只用于测试登记检查，未经过 Unity 导入。
PREFAB = HEADER + f"""--- !u!1 &12
GameObject:
  m_Name: Button
--- !u!224 &{SLOT_FILE_ID} stripped
RectTransform:
  m_CorrespondingSourceObject: {{fileID: 31, guid: {SOURCE_GUID}, type: 3}}
--- !u!1001 &31
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {SOURCE_GUID}, type: 3}}
"""
META = f"fileFormatVersion: 2\nguid: {GUID}\nPrefabImporter:\n  userData:\n"


@pytest.fixture
def prefab_project(tmp_path):
    root = tmp_path / "Project"
    prefab = root / PATH
    prefab.parent.mkdir(parents=True)
    prefab.write_bytes(PREFAB.encode("utf-8"))
    (root / (PATH + ".meta")).write_bytes(META.encode("utf-8"))
    return root


def test_prefab_record_survives_project_move(prefab_project):
    record = inspect_prefab(prefab_project, PATH, GUID.upper(), {"label": SLOT_FILE_ID}, [SOURCE_GUID.upper()])
    assert record == {
        "path": PATH,
        "guid": GUID,
        "sha256": sha256(PREFAB.encode("utf-8")).hexdigest(),
        "meta_sha256": sha256(META.encode("utf-8")).hexdigest(),
        "slot_bindings": {"label": SLOT_FILE_ID},
        "source_guids": [SOURCE_GUID],
    }
    relocated = prefab_project.with_name("Moved")
    prefab_project.rename(relocated)
    verify_prefab(relocated, record)
    assert (relocated / PATH).read_bytes() == PREFAB.encode("utf-8")
    assert (relocated / (PATH + ".meta")).read_bytes() == META.encode("utf-8")


def test_standalone_prefab_has_no_source_prefabs(prefab_project):
    prefab = HEADER + "--- !u!114 &12\nMonoBehaviour:\n  按钮标题: 开始\n"
    (prefab_project / PATH).write_text(prefab, encoding="utf-8")
    record = inspect_prefab(prefab_project, PATH, GUID, {"root": 12}, [])
    assert record["source_guids"] == []
    verify_prefab(prefab_project, record)


@pytest.mark.parametrize("file_id", [False, float("nan"), 12.0, "12", 2**63, -(2**63) - 1])
def test_prefab_rejects_invalid_slot_file_id(prefab_project, file_id):
    with pytest.raises(ProjectError, match=r"Button\.prefab.*label.*64 位整数"):
        inspect_prefab(prefab_project, PATH, GUID, {"label": file_id}, [SOURCE_GUID])


def test_prefab_rejects_absent_slot_file_id(prefab_project):
    with pytest.raises(ProjectError, match=r"Button\.prefab.*label.*fileID 99"):
        inspect_prefab(prefab_project, PATH, GUID, {"label": 99}, [SOURCE_GUID])


@pytest.mark.parametrize("guid", [SOURCE_GUID, "123"])
def test_prefab_rejects_guid_conflict(prefab_project, guid):
    with pytest.raises(ProjectError, match=r"Button\.prefab.*GUID"):
        inspect_prefab(prefab_project, PATH, guid, {}, [SOURCE_GUID])


def test_prefab_rejects_duplicate_meta_guid(prefab_project):
    (prefab_project / (PATH + ".meta")).write_text(META + f"guid: {GUID}\n", encoding="utf-8")
    with pytest.raises(ProjectError, match=r"Button\.prefab\.meta.*仅有一个"):
        inspect_prefab(prefab_project, PATH, GUID, {}, [SOURCE_GUID])


@pytest.mark.parametrize("relative", [PATH, PATH + ".meta"])
def test_prefab_requires_both_files(prefab_project, relative):
    (prefab_project / relative).unlink()
    with pytest.raises(ProjectError, match=rf"{relative}.*现存文件"):
        inspect_prefab(prefab_project, PATH, GUID, {}, [SOURCE_GUID])


@pytest.mark.parametrize("relative", [
    "/Assets/UI/Button.prefab", "C:/Assets/UI/Button.prefab", "Assets/../Button.prefab",
    "Assets/./UI/Button.prefab", "Assets//UI/Button.prefab", "Assets/.smartui/Button.prefab",
    r"Assets\UI\Button.prefab", "Assets/UI/Button.prefab:alternate",
])
def test_prefab_requires_canonical_asset_path(prefab_project, relative):
    with pytest.raises(ProjectError, match="规范"):
        inspect_prefab(prefab_project, relative, GUID, {}, [SOURCE_GUID])


@pytest.mark.parametrize("relative", [PATH, PATH + ".meta", "Assets/UI"])
def test_prefab_rejects_symbolic_links(prefab_project, relative):
    path = prefab_project / relative
    target = prefab_project / "Assets" / "Target"
    path.rename(target)
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("当前环境无法创建符号链接")
    with pytest.raises(ProjectError, match="符号链接"):
        inspect_prefab(prefab_project, PATH, GUID, {}, [SOURCE_GUID])


@pytest.mark.skipif(os.name != "nt", reason="目录联接是 Windows 功能")
def test_prefab_rejects_directory_junction(prefab_project):
    directory = prefab_project / "Assets/UI"
    target = directory.with_name("Target")
    directory.rename(target)
    subprocess.run(["cmd", "/d", "/c", "mklink", "/j", str(directory), str(target)],
                   check=True, capture_output=True)
    try:
        with pytest.raises(ProjectError, match="目录联接"):
            inspect_prefab(prefab_project, PATH, GUID, {}, [SOURCE_GUID])
    finally:
        directory.rmdir()


@pytest.mark.parametrize("sources", [[], [GUID], [SOURCE_GUID, GUID]])
def test_prefab_requires_exact_source_guids(prefab_project, sources):
    with pytest.raises(ProjectError, match=r"Button\.prefab.*m_SourcePrefab"):
        inspect_prefab(prefab_project, PATH, GUID, {}, sources)


def test_prefab_only_uses_source_prefab_for_dependency_check(prefab_project):
    prefab = PREFAB.replace(f"guid: {SOURCE_GUID}, type: 3}}\n---", f"guid: {GUID}, type: 3}}\n---")
    (prefab_project / PATH).write_text(prefab, encoding="utf-8")
    inspect_prefab(prefab_project, PATH, GUID, {}, [SOURCE_GUID])


@pytest.mark.parametrize("relative, field", [(PATH, "sha256"), (PATH + ".meta", "meta_sha256")])
def test_prefab_verification_detects_byte_changes(prefab_project, relative, field):
    record = inspect_prefab(prefab_project, PATH, GUID, {"label": SLOT_FILE_ID}, [SOURCE_GUID])
    file = prefab_project / relative
    file.write_bytes(file.read_bytes() + b"\n")
    with pytest.raises(ProjectError, match=rf"Button\.prefab.*{field}"):
        verify_prefab(prefab_project, record)


@pytest.mark.parametrize("data, error", [
    (b"\xff\x00", "无法读取"),
    (b"ordinary text", "文件头"),
    (HEADER.encode("utf-8"), "缺少 Unity 对象声明"),
    ((HEADER + "--- !u!1 &1\nGameObject:\n").encode("utf-8"), "缺少有效的对象正文"),
    (PREFAB.replace("  m_Name: Button", "  [unclosed").encode("utf-8"), "fileID 12.*缺少有效的对象正文"),
    (PREFAB.replace(f"&{SLOT_FILE_ID}", "&12").encode("utf-8"), "fileID 12 重复"),
    (PREFAB.replace("PrefabInstance:", "GameObject:").encode("utf-8"), "fileID 31.*PrefabInstance"),
    (PREFAB.replace("m_SourcePrefab", "m_Other").encode("utf-8"), "m_SourcePrefab"),
])
def test_prefab_rejects_malformed_text(prefab_project, data, error):
    (prefab_project / PATH).write_bytes(data)
    with pytest.raises(ProjectError, match=error):
        inspect_prefab(prefab_project, PATH, GUID, {}, [SOURCE_GUID])
