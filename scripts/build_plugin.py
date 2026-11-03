"""构建只包含运行代码和操作说明的本地插件，避免复制开发缓存。"""

from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    distribution_root = root / "dist"
    target = distribution_root / "smartui-krita"
    # 删除范围只允许当前项目的这个构建目录，拒绝指向外部的链接。
    if distribution_root.resolve() != root / "dist" or target.resolve() != root / "dist" / "smartui-krita":
        raise RuntimeError("构建目录解析到预期项目之外：" + str(target.resolve()))
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    files = ["plugin.json", "mcp.json", "pyproject.toml", "uv.lock", "README.md",
             "docs/bridge-contract.md", "docs/project-tools.md", "docs/drawing-programs.md",
             "docs/toolchain-roadmap.md", "docs/style-reproduction-validation.md",
             "scripts/install_krita.py", "krita_plugin/smartui_bridge.desktop",
             "examples/drawing-programs/semantic_palette.py"]
    for directory in ("smartui_mcp", "krita_plugin/smartui_bridge"):
        files += [str(p.relative_to(root)) for p in (root / directory).rglob("*.py")]
    files += [str(p.relative_to(root)) for p in (root / "skills").rglob("*.md")]
    for relative in sorted(files):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, destination)
    print("插件构建目录：", target)
    print("文件数：", len(files))


if __name__ == "__main__":
    main()
