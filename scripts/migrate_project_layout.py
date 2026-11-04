"""将 schema 2 UI 数据目录复制到工程布局；默认只预检，--apply 才写入。"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smartui_mcp.migration import migrate_project_layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, help="旧 UI 数据目录的绝对路径")
    parser.add_argument("--project-root", required=True, help="工程根目录的绝对路径")
    parser.add_argument("--apply", action="store_true", help="备份并创建新布局，保留旧数据目录")
    arguments = parser.parse_args()
    report = migrate_project_layout(arguments.source_root, arguments.project_root, apply=arguments.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
