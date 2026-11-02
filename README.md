# SmartUI Krita 本地绘制插件

通过 MCP（模型上下文协议）连接运行中的 Krita，使用原生笔刷批量绘制，管理分组、绘画图层与透明度蒙版，保存可编辑 `.kra`，并在重新打开后修改指定部分。

当前范围是 Krita 本地绘制链路；不包含 Unity 导入、风格识别或自动高质量艺术生成。视觉效果需要查看实际预览并迭代。

## 运行条件

- Windows、Krita 5.3.4.1 及可用的 Python 插件支持。
- Python 3.12 或更高版本，以及 PATH 中可执行的 `uv`。
- 支持本地 stdio MCP 的 Codex 桌面端或 CLI。该插件依赖本机 GUI，不能直接在纯网页运行环境中控制 Krita。

已知本机路径：`C:\Program Files\Krita (x64)\bin\krita.exe`、`C:\Program Files\Python312\python.exe`。配置不固定这些机器专属路径；`uv` 根据项目要求选择 Python。

## 启用 Krita 扩展

在插件根目录执行：

```powershell
uv run --locked --project . python scripts/install_krita.py
```

安装器将 `krita_plugin/smartui_bridge.desktop` 和 `krita_plugin/smartui_bridge/` 安装到 `%APPDATA%/krita/pykrita`，并启用扩展。重启 Krita 后，在“设置 → 配置 Krita → Python 插件管理器”检查 SmartUI Bridge 已启用。

Krita 扩展和 MCP 服务必须使用同一桥接目录，默认为 `%LOCALAPPDATA%/SmartUI/bridge`。如需自定义，在启动二者前设置相同的 `SMARTUI_BRIDGE_DIR`。`SMARTUI_BRIDGE_TIMEOUT` 控制 MCP 等待秒数，默认 120。

## 加载 Codex 插件

根目录 `plugin.json` 和 `mcp.json` 使用 [OpenAI 官方文档的 portable Agent Plugins 格式](https://developers.openai.com/plugins/build/plugins)。`skills/` 由宿主自动发现；`mcp.json` 声明本地 stdio 服务。

项目自带 `.agents/plugins/marketplace.json`，指向根目录内的 `dist/smartui-krita`。先构建精简插件包，避免把虚拟环境和验收产物复制进插件缓存，再注册本地来源并安装：

```powershell
uv run --locked python -X utf8 scripts/build_plugin.py
codex plugin marketplace add D:\Projects\SmartUI
codex plugin add smartui-krita@smartui-local
```

这里使用本机 CLI 的 `plugin add` 命令；没有 `plugin install` 子命令。也可按官方文档重启桌面端后，在插件目录选择“SmartUI 本地插件”来源并安装。不同宿主的本地插件入口可能不同。

修改插件代码后重新构建、重新安装，并在新聊天中验证工具是否出现。修改 Krita 内部扩展还需要在 Krita 关闭时重新执行安装器。构建脚本本身不会修改宿主配置。

## MCP 启动与工具

宿主通过 `mcp.json` 执行：

```text
uv run --locked --no-dev --project ${PLUGIN_ROOT} python -m smartui_mcp
```

`${PLUGIN_ROOT}` 由插件宿主展开。配置同时将工作目录和 `PYTHONPATH` 指向插件根目录。首次启动时 `uv` 根据 `uv.lock` 创建环境并安装运行依赖；`--locked` 检查锁文件一致性，`--no-dev` 排除开发依赖。手工排查可使用 `uv run --locked --project D:\Projects\SmartUI --directory D:\Projects\SmartUI python -m smartui_mcp`；服务通过标准输入输出传输 MCP 消息，不是交互式终端命令。

| 工具 | 用途 |
| --- | --- |
| `krita_status` | 实际请求 Krita，检查状态和可用操作 |
| `krita_list_brushes` | 查询已安装的精确笔刷预设名称 |
| `krita_inspect_document` | 检查文档、图层树、UUID 和像素哈希 |
| `krita_execute_batch` | 顺序执行文档、图层、蒙版和绘制操作 |
| `krita_preview` | 导出 PNG，并向模型返回预览图像 |

操作参数和验收目标见 [Krita 本地接口约定](docs/bridge-contract.md)。先请求状态和笔刷；已有文档若已打开，直接检查图层，否则先打开源文件。重复打开已打开的文档会报错。创建新 `.kra` 或检查已有图层后批量执行，再保存和预览。局部修改继续使用同一个源文件，并比较未修改图层的像素哈希。

批量执行不是事务：出错会停止，先前操作仍然保留，失败项也可能部分生效。请求超时不自动重试。错误中的请求 ID 和结果路径用于定位实际结果；应先检查文档和图层，再决定从哪里继续，避免重复修改。

## 当前绘制接口限制

- `line` 的 `from`、`to` 坐标必须是整数像素；可分别指定起点和终点压力。
- `path` 支持直线、二次与三次贝塞尔曲线和闭合路径。Krita 5.3 的当前 Python 接口没有连续自由手绘所需的 `StrokePoint` API；路径绘制不能等同于带逐点压力、倾角和时间信息的完整手绘笔触。
- 当前支持原生透明度蒙版的创建和更新，不能据此声称支持 Krita 的全部蒙版类型。

## 自动测试和实机验收

以下命令在源码项目根目录运行；精简安装包不包含测试、演示脚本和验收产物。运行自动测试不需要启动 Krita：

```powershell
uv run --locked python -m pytest -q
```

启用扩展并启动 Krita 后，运行实机验收：

```powershell
uv run --locked python -X utf8 scripts/verify_krita.py
```

验收会在新建的 `artifacts/verification-时间戳/` 目录创建 `.kra`，比较两种原生预设、批量执行 100 笔、检查透明度蒙版实际效果、保存并重开文件，再原位修改高光和蒙版并核对其他图层像素未变。还会验证缺失笔刷、锁定图层、批量部分完成错误，以及正式 MCP 入口的握手、文档检查和图片返回。结果和完整证据写入 `report.md` 与 `report.json`；断言失败时脚本以非零状态退出。

默认使用 `b) Basic-5 Size` 和 `h) Chalk Grainy`。如安装的预设不同，先查询精确名称，再用 `--brush-a`、`--brush-b`、`--texture-brush` 指定；缺失时验收直接失败。验收期间不要同时操作验收文档或向 Krita 提交其他绘制请求。

本机已通过的验收见 [2026 年 11 月 2 日实机报告](artifacts/verification-20261102-181548-233791/report.md)，其中单次原生绘制 100 笔耗时 1144.95 ms。该数值来自本次机器和预设，完整调用、文件哈希、节点 UUID 与图层像素哈希见同目录的 `report.json`。

按钮演示可通过 `uv run --locked python -X utf8 scripts/draw_button_demo.py` 复现。它保存 9 个绘画图层、1 个分组和 2 个透明度蒙版，并模拟“将偏黄的强高光改为偏冷的窄高光”的反馈；每次生成独立目录，修改在目录内同一 `.kra` 中完成。

## 项目结构

```text
plugin.json                  插件身份和显示信息
mcp.json                     MCP 启动配置
.agents/plugins/marketplace.json  本地插件来源
skills/krita-drawing/        Krita 绘制工作流程
smartui_mcp/                 独立 Python MCP 服务
krita_plugin/                Krita 内部 Python 扩展
scripts/install_krita.py     Krita 扩展安装器
scripts/build_plugin.py      精简插件包构建器
scripts/verify_krita.py      实机验收与证据报告
scripts/draw_button_demo.py  分层按钮与局部修改演示
docs/bridge-contract.md      文件队列协议与操作参数
```
