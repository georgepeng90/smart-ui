"""检查本地插件的分发资源和 Unity CLI 构建脚本。"""

from pathlib import Path
import json
import shutil
import subprocess
import sys


def test_distribution_includes_plugin_assets_and_unity_build_script(tmp_path):
    source = Path(__file__).resolve().parents[1]
    for filename in ("plugin.json", "mcp.json", "pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(source / filename, tmp_path)
    for name in ("assets", "docs", "examples", "scripts", "skills", "smartui_mcp"):
        shutil.copytree(source / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    script_directory = Path("smartui_mcp/unity_scripts")
    (tmp_path / script_directory / "src" / "Local.cs").write_text("local", encoding="utf-8")

    subprocess.run([sys.executable, str(tmp_path / "scripts" / "build_plugin.py")], check=True)
    distribution = tmp_path / "dist" / "smartui-photocraft"
    manifest = json.loads((distribution / "plugin.json").read_text(encoding="utf-8"))
    interface = manifest["extensions"]["com.openai"]["interface"]
    for key in ("logo", "composerIcon"):
        icon = Path(interface[key])
        assert (distribution / icon).read_bytes() == (source / icon).read_bytes()
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
