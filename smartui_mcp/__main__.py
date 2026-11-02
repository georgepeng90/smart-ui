"""使用 python -m smartui_mcp 启动 stdio 服务。"""

from .server import create_server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
