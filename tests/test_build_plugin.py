"""检查本地插件分发只包含 Unity CLI 所需的构建脚本。"""

from pathlib import Path
import shutil
import subprocess
import sys


def test_distribution_includes_fixed_unity_build_script(tmp_path):
    source = Path(__file__).resolve().parents[1]
    for filename in ("plugin.json", "mcp.json", "pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(source / filename, tmp_path)
    for name in ("docs", "examples", "scripts", "skills", "smartui_mcp"):
        shutil.copytree(source / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    script_directory = Path("smartui_mcp/unity_scripts")
    (tmp_path / script_directory / "src" / "Local.cs").write_text("local", encoding="utf-8")

    subprocess.run([sys.executable, str(tmp_path / "scripts" / "build_plugin.py")], check=True)
    distribution = tmp_path / "dist" / "smartui-photocraft"
    for filename in ("ui-delivery-plan.md", "ui-delivery-checklists.md"):
        assert (distribution / "docs" / filename).is_file()
    packaged = distribution / script_directory / "SmartUIBuild.cs"
    assert list(distribution.rglob("*.cs")) == [packaged]
    script = packaged.read_text(encoding="utf-8")
    assert "public static object Execute(string requestPath)" in script
    assert "class BuildExecution" in script
    assert "class PrefabGraph" in script
    assert "class PrefabReuseTests" not in script
    assert "CliCommand" not in script
    assert not list(distribution.rglob("*.asmdef"))
