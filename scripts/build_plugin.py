"""构建只包含运行代码和操作说明的本地插件，避免复制开发缓存。"""

from pathlib import Path
import shutil
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from smartui_mcp.unity_script import SCRIPT_DIRECTORY, SCRIPT_NAME, write_script

    distribution_root = root / "dist"
    target = distribution_root / "smartui-photocraft"
    # 删除范围只允许当前项目的这个构建目录，拒绝指向外部的链接。
    if distribution_root.resolve() != root / "dist" or target.resolve() != root / "dist" / "smartui-photocraft":
        raise RuntimeError("构建目录解析到预期项目之外：" + str(target.resolve()))
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    files = ["plugin.json", "mcp.json", "pyproject.toml", "uv.lock", "README.md",
             "docs/photocraft-contract.md", "docs/project-tools.md", "docs/drawing-programs.md",
             "docs/toolchain-roadmap.md", "docs/typography-plan.md", "docs/style-reproduction-validation.md", "docs/asset-geometry.md",
             "docs/unity-integration-plan.md", "docs/unity-tools.md", "docs/unity-prefab-reuse-plan.md",
             "docs/ui-delivery-plan.md", "docs/ui-delivery-checklists.md",
             "examples/drawing-programs/semantic_palette.py",
             "scripts/verify_program_workflow.py", "scripts/verify_page_workflow.py", "scripts/adopt_typography.py"]
    files += [str(p.relative_to(root)) for p in (root / "smartui_mcp").rglob("*.py")]
    files += [str(p.relative_to(root)) for p in (root / "skills").rglob("*.md")]
    for relative in sorted(files):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, destination)
    unity_script = target / SCRIPT_DIRECTORY.relative_to(root) / SCRIPT_NAME
    unity_script.parent.mkdir(parents=True)
    write_script(unity_script)
    print("插件构建目录：", target)
    print("文件数：", len(files) + 1)


if __name__ == "__main__":
    main()
