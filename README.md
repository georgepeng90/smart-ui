# SmartUI Krita 本地绘制插件

SmartUI Krita v0.3.0 通过 MCP（模型上下文协议）连接运行中的 Krita，登记参考图，保存项目风格、Python 绘制程序和组件规格，生成可编辑 `.kra`，并在同一源文件中更新选定图层。

绘制程序属于项目，由模型根据参考与当前需求编写。通用工具只执行和验证绘制计划，图层数量、名称、几何、材质和绘制步骤由项目程序决定。参考工具提供裁切和取色，模型负责查看图片、归纳依据和校准参数；技术检查通过后仍需查看预览并验收视觉效果。

项目以 `project.json` 为唯一结构化数据源，工具自动维护 `style.md` 和 `components.md`，不会静默覆盖手改文档。工具参数见[项目工具说明](docs/project-tools.md)，程序接口见[绘制程序约定](docs/drawing-programs.md)。参考分析使用 `reference-style` Skill，实际制作与原文件修改使用 `krita-drawing` Skill。

## 运行条件

- Windows、Krita 5.3.4.1 及可用的 Python 插件支持。
- Python 3.12 或更高版本，以及 PATH 中可执行的 `uv`。
- MCP 服务运行依赖包含 Pillow，用于读取参考图、生成预览和取色；由 `uv.lock` 安装。
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

工具共 15 个：五个底层 Krita 工具和十个项目工具。

| 工具 | 用途 |
| --- | --- |
| `krita_status` | 请求 Krita，检查状态和可用操作 |
| `krita_list_brushes` | 查询精确笔刷预设名称 |
| `krita_inspect_document` | 检查文档、图层树、UUID 和像素哈希 |
| `krita_execute_batch` | 顺序执行文档、图层、蒙版和绘制操作 |
| `krita_preview` | 导出 PNG 并返回预览图像 |
| `project_create` | 创建项目及受管文档 |
| `project_context` | 读取项目摘要和最新 revision |
| `project_add_reference` | 登记只读参考并生成预览 |
| `project_inspect_reference` | 裁切区域、取样原图色值并返回图片 |
| `project_update_style` | 保存规则、共用参数及参考依据 |
| `project_save_program` | 语法检查并保存项目 Python 程序版本 |
| `project_get_program` | 读取程序元数据与源码 |
| `project_define_component` | 保存含 program_id 的组件规格 |
| `project_plan_component` | 执行程序并预检计划，不调用 Krita |
| `project_draw_component` | 执行计划、保存、重开检查并记录结果；可指定更新节点 |

### 在新聊天中制作组件

已有项目先读取 `project_context`。新项目先创建并登记参考，查看图片后保存有依据的风格规则及共用参数，再保存实现 `build(parameters, spec, component_id)` 的项目程序。定义组件时使用 `spec.program_id` 引用它，其余字段由程序定义。先用 `project_plan_component` 检查计划，再确认 Krita 连接及精确笔刷，调用 `project_draw_component`，查看实际预览。

每次项目写入使用最近返回的 `revision`；冲突时重新读取并核对差异。程序保存只检查语法；计划与绘制会在独立 Python 进程中执行源码，超时为 60 秒。程序是可信本机代码，可以访问文件和网络；独立进程不是安全沙箱。

继续项目时可这样交代任务：

```text
使用 SmartUI Krita 插件，读取指定项目的 project_context，并查看参考和当前预览。
按我的反馈检查项目绘制程序与组件规格，先生成计划，再更新目标节点。
展示实际 PNG 和同一 .kra 文件，说明技术检查结果和仍需视觉确认的内容。
```

源文件位于 `assets/<component_id>.kra`。每次运行的程序快照、计划、风格快照、操作和检查结果位于 `.smartui/runs/<run_id>/`。源码版本更新后，依赖组件需要重新检查；历史记录保留实际使用的程序与风格版本。

## 当前绘制接口边界

- `layers` 是计划中的节点 key 列表，可指定绘画图层、分组或透明度蒙版。工具比较上次计划，拒绝目标之外的变化。完整重画也要求当前画布及节点结构仍匹配；新尺寸或新结构暂用新的组件 ID。
- 计划支持 `paintlayer`、`grouplayer`、`transparencymask`，不代表支持 Krita 的全部节点类型。计划与属性细节见[绘制程序约定](docs/drawing-programs.md)。
- `line` 的起止坐标是整数像素，可分别指定起终点压力。`path` 支持直线、二次与三次贝塞尔曲线及闭合路径。路径绘制不保留自由手绘的逐点压力、倾角和时间信息。
- 项目工具不自动识别艺术风格，不自动确认视觉质量，也不包含 Unity 导入。

已有文档若已打开，直接检查；否则先打开源文件。批量执行不是事务：失败会停止，已执行操作和失败项的部分效果可能保留。超时后先检查请求结果与 Krita 现场，不自动重发修改请求。

## 自动测试与实机验证

在源码根目录运行自动测试，不需要启动 Krita：

```powershell
uv run --locked python -m pytest -q
```

当前自动测试为 245 项、10 个子测试，全部通过。v0.3 的[真实 MCP → Krita 验证报告](projects/slimeblock/.smartui/integration/project-program-report.json)已通过：自定义圆形徽章程序生成 3 个绘画图层和 1 个透明度蒙版，循环计算 16 条笔触；程序升级只标记依赖组件，原文件中的条纹与蒙版分别局部修改，其他节点及按钮资产保持不变。四个既有按钮也已通过项目程序重新绘制并登记实际源码版本。

复现通用程序验收使用已迁移的 SlimeBlock 项目，脚本会实际更新验证用徽章：

```powershell
uv run --locked python -X utf8 scripts/verify_program_workflow.py --project-root D:\Projects\SmartUI\projects\slimeblock
```

精简安装包不包含测试、项目实例、绘制示例或验收产物。SlimeBlock 的七层亮面按钮仅作为[项目 Python 示例](examples/drawing-programs/slimeblock_buttons.py)保留在源码中；需要该画法时，将适用的源码通过 `project_save_program` 保存到对应项目，再提供该程序要求的参数。

此前 v0.2 的[项目工具报告](projects/slimeblock/.smartui/integration/report.json)、[风格升级报告](projects/slimeblock/.smartui/integration/style-update-report.json)及[2026 年 11 月 2 日底层实机报告](artifacts/verification-20261102-181548-233791/report.md)保留为历史证据。参考分析与复现验收方法见[验证流程](docs/style-reproduction-validation.md)。

项目结构 v2 不隐式读取旧结构。SlimeBlock 已使用 `scripts/migrate_project_programs.py` 显式迁移：旧文档与项目 JSON 备份在 `.smartui/migrations/`，旧执行记录保持原样，未重绘前的资产不会被登记为新程序版本。该脚本只处理 v0.2 的亮面按钮示例，不猜测其他项目的程序内容。

底层实机验收脚本 `scripts/verify_krita.py` 会创建独立验收文件并实际操作 Krita。运行前启动扩展，查询本机精确笔刷名称；验收期间避免同时操作验收文档。脚本中的默认笔刷是测试输入，不是所有项目的制作要求。

## 项目结构

```text
plugin.json                    插件身份和显示信息
mcp.json                       MCP 启动配置
skills/krita-drawing/           Krita 制作、检查与修改工作流
skills/reference-style/         参考分析与风格文档维护
smartui_mcp/                    MCP 服务、项目存储与程序执行
krita_plugin/                  Krita 内部扩展
examples/drawing-programs/      源码项目示例，不进入安装包
scripts/build_plugin.py        精简插件包构建器
docs/bridge-contract.md        底层操作约定
docs/project-tools.md          项目工具与状态约定
docs/drawing-programs.md       项目 Python 程序与计划接口
```
