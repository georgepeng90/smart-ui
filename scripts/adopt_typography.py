"""预检或显式转换 SmartUI 格式 4 项目的文字规范与页面。"""

import argparse
import json
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from smartui_mcp.projects import ProjectError
from smartui_mcp.typography_adoption import adopt_typography, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="业务工程根目录")
    parser.add_argument("--typography", required=True, type=Path, help="新文字规范的 JSON 文件")
    parser.add_argument("--layout-styles", required=True, type=Path, help="页面文字节点与角色样式 ID 映射的 JSON 文件")
    parser.add_argument("--apply", action="store_true", help="应用转换，默认只预检")
    parser.add_argument("--expected-revision", help="应用转换时传入预检返回的 revision")
    args = parser.parse_args()
    try:
        result = adopt_typography(
            str(args.project_root.resolve()), read_json(args.typography), read_json(args.layout_styles),
            apply=args.apply, expected_revision=args.expected_revision,
        )
    except ProjectError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
