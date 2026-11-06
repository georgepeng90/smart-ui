"""检查本地插件分发包含可复制到 Unity 工程的 Editor 包。"""

from pathlib import Path
import shutil
import subprocess
import sys


def test_distribution_includes_unity_editor_package(tmp_path):
    source = Path(__file__).resolve().parents[1]
    for filename in ("plugin.json", "mcp.json", "pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(source / filename, tmp_path)
    for name in ("docs", "examples", "scripts", "skills", "smartui_mcp", "unity"):
        shutil.copytree(source / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    package = Path("unity/com.smartui.ugui")
    local_cache = tmp_path / package / "Editor" / "local.log"
    local_cache.write_text("local", encoding="utf-8")

    subprocess.run([sys.executable, str(tmp_path / "scripts" / "build_plugin.py")], check=True)
    packaged = tmp_path / "dist" / "smartui-photocraft" / package
    assert (packaged / "package.json").read_bytes() == (source / package / "package.json").read_bytes()
    assert (packaged / "Editor" / "SmartUI.UGUI.Editor.asmdef").is_file()
    assert (packaged / "Editor" / "Pipeline" / "SmartUI.UGUI.Pipeline.Editor.asmdef").is_file()
    assert (packaged / "Tests" / "Editor" / "SmartUI.UGUI.Editor.Tests.asmdef").is_file()
    assert not (packaged / "Editor" / local_cache.name).exists()
