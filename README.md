# SmartUI Krita 本地绘制插件

完整目标、阶段安排和验收标准见[通用游戏 UI 制作工具链总体规划](docs/toolchain-roadmap.md)。本页说明当前已实现能力和使用方法；总体规划中的后续能力不代表已经实现。

SmartUI Krita v0.7.0 通过 MCP（模型上下文协议）连接运行中的 Krita，登记参考图，保存项目风格、Python 绘制程序和组件规格，生成可编辑 `.kra`，并在同一源文件中更新选定图层。项目可保存参考与实际绘制结果的对照，记录和复查偏差，也可检查多份实际产物，并登记独立图像版本、明确选用版本及创建静态 UI 组合预览。

绘制程序属于项目，由模型根据参考与当前需求编写。通用工具只执行和验证绘制计划，图层数量、名称、几何、材质和绘制步骤由项目程序决定。参考工具提供裁切和取色，模型负责查看图片、归纳依据和校准参数；技术检查通过后仍需查看预览并验收视觉效果。

基础 UI 与 ICON（图标）图像采用独立制作流程。基础 UI 通过 Krita 制作；独立图像由宿主可用的生图或图像编辑工具生成，再复制到项目并登记真实来源信息。SmartUI 不内置生图后端，但已支持独立图像版本登记、选用、查看及基于固定坐标的静态 UI 组合预览。工作流见[ICON 资产 Skill](skills/icon-assets/SKILL.md)，参数与边界见[项目工具说明](docs/project-tools.md#独立图像资产与静态组合预览)。

用已有组件安排页面、移动实例或修改文案时，使用[页面组合 Skill](skills/page-composition/SKILL.md)。先查询并引用实际资产，组件外观变化才进入 Krita 制作；布局变化只创建新的组合。当前提供静态 PNG 快照，浏览器预览和可更新布局仍属于规划能力。

项目以业务工程根目录中的 `.smartui/project.json` 为唯一结构化数据源，工具默认自动维护 `docs/ui/style.md` 和 `docs/ui/components.md`，不会静默覆盖手改文档。工具参数见[项目工具说明](docs/project-tools.md)，程序接口见[绘制程序约定](docs/drawing-programs.md)。参考分析使用 `reference-style` Skill，实际制作与原文件修改使用 `krita-drawing` Skill。

## 运行条件

- Windows、Krita 5.3.4.1 及可用的 Python 插件支持。
- Python 3.12 或更高版本，以及 PATH 中可执行的 `uv`。
- MCP 服务运行依赖包含 Pillow，用于读取参考图、生成预览和取色；fontTools 用于检查组合文字所需的字体字符；均由 `uv.lock` 安装。
- 支持本地 stdio MCP 的 Codex 桌面端或 CLI。该插件依赖本机 GUI，不能直接在纯网页运行环境中控制 Krita。

Krita 和 Python 的安装路径由本机环境决定，`uv` 根据项目要求选择 Python。

## 启用 Krita 扩展

在插件根目录执行：

```powershell
uv run --locked --project . python scripts/install_krita.py
```

安装器将 `krita_plugin/smartui_bridge.desktop` 和 `krita_plugin/smartui_bridge/` 安装到 `%APPDATA%/krita/pykrita`，并启用扩展。重启 Krita 后，在“设置 → 配置 Krita → Python 插件管理器”检查 SmartUI Bridge 已启用。

Krita 扩展和 MCP 服务必须使用同一桥接目录，默认为 `%LOCALAPPDATA%/SmartUI/bridge`。如需自定义，在启动二者前设置相同的 `SMARTUI_BRIDGE_DIR`。`SMARTUI_BRIDGE_TIMEOUT` 控制 MCP 等待秒数，默认 120。

## 加载 Codex 插件

根目录 `plugin.json` 和 `mcp.json` 使用 [OpenAI 官方文档的 portable Agent Plugins 格式](https://developers.openai.com/plugins/build/plugins)。`skills/` 由宿主自动发现；`mcp.json` 声明本地 stdio 服务。

项目自带 `.agents/plugins/marketplace.json`，指向根目录内的 `dist/smartui-krita`。在源码仓库根目录构建精简插件包，再注册本地来源并安装：

```powershell
uv run --locked python -X utf8 scripts/build_plugin.py
codex plugin marketplace add (Get-Location).Path
codex plugin add smartui-krita@smartui-local
```

也可按官方文档重启桌面端后，在插件目录选择“SmartUI 本地插件”来源并安装。不同宿主的本地插件入口可能不同。

修改插件代码后重新构建、重新安装，并在新聊天中验证工具是否出现。修改 Krita 内部扩展还需要在 Krita 关闭时重新执行安装器。构建脚本本身不会修改宿主配置。

## MCP 启动与工具

宿主通过 `mcp.json` 执行：

```text
uv run --locked --no-dev --project ${PLUGIN_ROOT} python -m smartui_mcp
```

`${PLUGIN_ROOT}` 由插件宿主展开。配置同时将工作目录和 `PYTHONPATH` 指向插件根目录。首次启动时 `uv` 根据 `uv.lock` 创建环境并安装运行依赖；`--locked` 检查锁文件一致性，`--no-dev` 排除开发依赖。手工排查时，在插件根目录执行 `uv run --locked --no-dev --project . python -m smartui_mcp`；服务通过标准输入输出传输 MCP 消息，不是交互式终端命令。

工具共 28 个，包括五个底层 Krita 工具和二十三个项目工具。

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
| `project_compare_component` | 将已保存的绘制预览和参考区域生成可追溯的对照 |
| `project_get_comparison` | 读取对照拼图、证据和相关反馈 |
| `project_create_visual_check` | 按指定裁切、等比倍率和纯色背景并排展示多个成功运行的产物，可同时生成灰度图 |
| `project_get_visual_check` | 校验并读取已保存的视觉检查图与对应运行记录 |
| `project_register_image` | 按原字节登记 PNG/JPEG/WebP 独立图像版本及真实来源，不自动选用 |
| `project_select_image` | 明确选用已登记的图像版本，不表示用户美术确认 |
| `project_get_image` | 校验并查看指定图像版本、来源及当前选用版本 |
| `project_export_assets` | 从明确组件运行的区域导出正式 PNG、伸缩元数据及清单，不改变选用状态 |
| `project_get_export` | 校验并读取历史导出清单及归档资产 |
| `project_create_composition` | 按明确坐标组合成功组件运行、图像版本和单行文字，保存静态快照 |
| `project_get_composition` | 校验并读取已保存的静态组合及来源快照 |
| `project_record_feedback` | 根据对照记录观察到的偏差及修改建议 |
| `project_update_feedback` | 根据对照追加复查记录，更新反馈状态 |

### 业务工程中的项目目录

`project_root` 始终是业务工程根目录的绝对路径。SmartUI 状态保存在 `.smartui/project.json`；参考、文档、程序和源文件与工程一起维护，不放在工具安装目录中。

| 用途 | 默认目录 | `paths` 配置名 |
| --- | --- | --- |
| 风格与组件文档 | `docs/ui` | `documents` |
| 参考原图 | `docs/ui/references` | `references` |
| 绘制程序版本 | `art/ui/programs` | `programs` |
| Krita 源文件与独立图像 | `art/ui/sources` | `sources` |

创建时可通过 `paths` 局部覆盖这些目录，值必须是工程内的相对路径；完整映射写入 schema 3 项目状态。工程外的参考输入会复制到参考目录的 `<reference_id>/<原文件名>`。项目内文件引用以工程相对 POSIX 路径保存，工具返回用于读取和执行的绝对路径；程序元数据的 `source_path` 保持工程相对路径。

创建项目时，工具在 `.smartui/.gitignore` 中按子项忽略 `cache/` 和临时锁 `project.lock`。项目状态及 `.smartui/runs/`、`.smartui/comparisons/`、`.smartui/visual-checks/` 是需要保留的制作记录，不应整体忽略 `.smartui/`。受管文档的链接相对于文档目录生成。

需要导入已有数据目录时，使用[数据导入与目录迁移](docs/project-tools.md#数据导入与目录迁移)中的脚本，先预检，再显式执行。迁移的目标是业务工程根目录。

### 在新聊天中制作组件

已有项目先读取 `project_context`。新项目先创建并登记参考，查看图片后保存有依据的风格规则及共用参数，再保存实现 `build(parameters, spec, component_id)` 的项目程序。定义组件时使用 `spec.program_id` 引用它，其余字段由程序定义。先用 `project_plan_component` 检查计划，再确认 Krita 连接及精确笔刷，调用 `project_draw_component`，查看实际预览。

每次项目写入使用最近返回的 `revision`；冲突时重新读取并核对差异。程序保存只检查语法；计划与绘制会在独立 Python 进程中执行源码，超时为 60 秒。程序是可信本机代码，可以访问文件和网络；独立进程不是安全沙箱。

继续项目时可这样交代任务：

```text
使用 SmartUI Krita 插件读取项目上下文，查看已有对照和反馈。
按反馈检查项目绘制程序与组件规格，先生成计划，再更新目标节点。
用新运行对照原参考区域，逐条复查反馈，展示对照拼图、实际 PNG 和可编辑的 .kra 文件。
```

源文件默认位于 `art/ui/sources/<component_id>.kra`。每次运行的程序快照、计划、风格快照、操作和检查结果位于 `.smartui/runs/<run_id>/`。源码版本更新后，依赖组件需要重新检查；历史记录保留实际使用的程序与风格版本。

需要检查复现偏差时，用 `project_compare_component` 对照参考和指定运行的实际预览，查看拼图后再记录偏差。修改并绘制后，用新运行与原参考区域重新对照，逐条复查反馈。对照的裁切快照和拼图保存在 `.smartui/comparisons/<comparison_id>/`。操作细节见[对照与偏差反馈](docs/project-tools.md#对照与偏差反馈)。

需要比较配色、状态、尺寸或修改前后的效果时，用 `project_create_visual_check` 指定各个产物的成功运行 ID、标签、显示倍率和共同的纯色背景。用彩色总览和可选灰度图检查应共用的轮廓、留白以及各状态的辨识度。操作细节见[多产物视觉检查](docs/project-tools.md#多产物视觉检查)，共用参数的写法见[配色与状态变体示例](docs/drawing-programs.md#配色与状态变体示例)。

制作 ICON 时使用宿主提供的图像生成或编辑能力，查看实际输出后将真实产物复制到业务工程，再通过 `project_register_image` 登记。生成和编辑来源必须如实提供实际工具名及提示词；已有图像可按 `imported` 登记描述，不补造提示词。登记候选不会自动选用，组合可以引用任意明确的登记版本；需要改变项目选用版本时，单独调用 `project_select_image`。`project_create_composition` 只做固定坐标、纯色背景上的静态预览：图像在目标矩形内等比缩放并居中，组件与图像可以按原像素区域裁切，文字是使用真实 TTF/OTF 字体的单行文本。它不提供自动布局、九宫格、运行时交互或 Krita 位图图层导入。完整工作流见[ICON 资产 Skill](skills/icon-assets/SKILL.md)和[页面组合 Skill](skills/page-composition/SKILL.md)。

## 当前绘制接口边界

交付组件 PNG 时，使用 `project_export_assets` 明确指定来源运行、导出区域、状态、用途、生产路径和几何声明。导出沿用图像资产身份和版本，重复相同输入不重写生产文件；源文件或已受管输出被外部修改时明确报错。区域导出不能自动去除已烘焙的文字，不处理 Unity 导入设置。参数见[正式 PNG 导出](docs/project-tools.md#正式-png-导出)。

- `layers` 是计划中的节点 key 列表，可指定绘画图层、分组或透明度蒙版。工具比较上次计划，拒绝目标之外的变化。完整重画也要求当前画布及节点结构仍匹配；新尺寸或新结构暂用新的组件 ID。
- 计划支持 `paintlayer`、`grouplayer`、`transparencymask`，不代表支持 Krita 的全部节点类型。计划与属性细节见[绘制程序约定](docs/drawing-programs.md)。
- `line` 的起止坐标是整数像素，可分别指定起终点压力。`path` 支持直线、二次与三次贝塞尔曲线及闭合路径。路径绘制不保留自由手绘的逐点压力、倾角和时间信息。
- 项目工具不自动识别艺术风格，不自动确认视觉质量，也不包含 Unity 导入。
- SmartUI 不提供模型生图后端；图像生成和编辑由宿主工具完成。独立图像资产登记与静态坐标组合可用，但不会自动布局或导入为 Krita 位图图层。

已有文档若已打开，直接检查；否则先打开源文件。批量执行不是事务：失败会停止，已执行操作和失败项的部分效果可能保留。超时后先检查请求结果与 Krita 现场，不自动重发修改请求。

## 开发验证

在源码根目录运行自动测试，不需要启动 Krita：

```powershell
uv run --locked python -m pytest -q
```

验证原生笔刷、图层、蒙版和源文件保存重开时，先启用扩展并启动 Krita，再在源码根目录运行：

```powershell
uv run --locked python -X utf8 scripts/verify_krita.py
```

脚本在 `artifacts/` 下创建独立验收文件和报告，不依赖已有业务项目。运行前核对本机笔刷预设；验收期间避免同时操作验收文档。脚本默认笔刷是测试输入，项目制作可以使用其他适用预设。

参考分析与复现质量的验证方法见[验证流程](docs/style-reproduction-validation.md)。项目程序的最小可执行示例见[绘制程序约定](docs/drawing-programs.md#最小程序示例)。

精简安装包不包含测试和验收脚本，上述命令在源码仓库中使用。具体项目数据与每次运行产物保留在本地，不随工具分发。

## 项目结构

```text
plugin.json                    插件身份和显示信息
mcp.json                       MCP 启动配置
skills/krita-drawing/           Krita 制作、检查与修改工作流
skills/reference-style/         参考分析与风格文档维护
skills/icon-assets/             独立图像资产登记、版本选用与 UI 静态组合
skills/page-composition/        已有资产的页面排版、修改路由与静态组合
smartui_mcp/                    MCP 服务、项目存储与程序执行
krita_plugin/                  Krita 内部扩展
examples/drawing-programs/      绘制程序示例，安装包包含通用配色与状态示例
scripts/build_plugin.py        精简插件包构建器
docs/bridge-contract.md        底层操作约定
docs/project-tools.md          项目工具与状态约定
docs/drawing-programs.md       项目 Python 程序与计划接口
```
