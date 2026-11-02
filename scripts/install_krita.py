"""在 Krita 关闭时安装本地扩展，保留原配置的备份。"""

import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime


def main():
    processes = subprocess.run(["tasklist", "/FI", "IMAGENAME eq krita.exe", "/FO", "CSV", "/NH"], capture_output=True, check=True)
    if b'"krita.exe"' in processes.stdout.lower():
        raise SystemExit("请先保存并关闭 Krita，再安装扩展；不会自动结束正在使用的进程。")
    root = Path(__file__).resolve().parents[1]
    target = Path(os.environ["APPDATA"]) / "krita" / "pykrita"
    target.mkdir(parents=True, exist_ok=True)
    source = root / "krita_plugin"
    shutil.copytree(source / "smartui_bridge", target / "smartui_bridge", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(source / "smartui_bridge.desktop", target / "smartui_bridge.desktop")
    config = Path(os.environ["LOCALAPPDATA"]) / "kritarc"
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    if config.exists():
        backup = config.with_name("kritarc.smartui-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(config, backup)
        print("配置备份：", backup)
    section = re.search(r"(?m)^\[python\]\s*\n(?P<body>(?:(?!^\[).*(?:\n|$))*)", original)
    entry = "enable_smartui_bridge=true\n"
    if section:
        body = section.group("body")
        if re.search(r"(?m)^enable_smartui_bridge=.*$", body):
            body = re.sub(r"(?m)^enable_smartui_bridge=.*$", entry.rstrip(), body)
        else:
            body += entry
        updated = original[:section.start("body")] + body + original[section.end("body"):]
    else:
        updated = original.rstrip() + "\n\n[python]\n" + entry
    config.write_text(updated, encoding="utf-8")
    print("扩展已安装：", target / "smartui_bridge")
    print("已启用：", config)
    print("现在启动 Krita，然后调用 krita_status 验证。")


if __name__ == "__main__":
    main()
