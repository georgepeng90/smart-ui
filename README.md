# SmartUI PhotoCraft 本地制作插件

SmartUI PhotoCraft v0.11.0 是通用游戏 UI 制作工具。通过项目 Python 程序制作 PhotoCraft 可编辑组件，管理参考、风格、文字规范、图像资产、正式 PNG 导出和页面布局。独立图标由宿主图像工具生成或编辑，SmartUI 登记实际文件和真实来源。

## 安装

需要 Windows、Python 3.12 及以上、uv、正常安装的 PhotoCraft v0.20.1（构建 `327c4cc82`，CLI 版本输出为 `photocraft-cli 0.20.1 (327c4cc82, 2026-11-03)`），以及支持本地 stdio MCP 的 Codex。请通过 [PhotoCraft 官方发布页](https://github.com/storytold/photocraft/releases/tag/v0.20.1)安装应用。服务查找正常安装位置或 PATH 中的 CLI；也可设置 `SMARTUI_PHOTOCRAFT_CLI` 为 CLI 的绝对路径。缺失或完整版本输出不符时明确报错，运行时不自动下载应用。

首次安装时，在本仓库根目录运行以下命令：

```powershell
uv sync --locked
uv run --locked playwright install chromium
uv run --locked python scripts/build_plugin.py
codex plugin marketplace add .
codex plugin add smartui-photocraft@smartui-local
```

构建输出为 `dist/smartui-photocraft`，本地插件源由 `.agents/plugins/marketplace.json` 定义。插件源已登记时，更新只需重新运行构建命令和 `codex plugin add smartui-photocraft@smartui-local`。安装或更新后，在新聊天中使用插件。构建脚本只生成分发文件。浏览器页面构建使用已安装的 Chromium。

## 使用

使用 `reference-style` 分析参考；使用 `photocraft-drawing` 制作与修改基础组件；独立图标使用 `icon-assets`；已有资产排版、移动实例和改文案使用 `page-composition`。

先调用 `project_context` 读取已有项目。新项目创建后登记并查看参考，保存风格、共用参数和 Python 程序，定义组件后制作并查看预览。首次编写或需诊断时单独预检计划；绘制入口自身也会严格预检，已明确的参数调整无需重复调用。所有写入使用最近返回的 `revision`。技术检查、模型视觉判断和用户确认分别表达。

先用少量代表组件校准参考匹配度，必要时合成一个典型小区域。发现具体偏差且有可执行的改法时再修改。整页检查在实际页面任务中复用已有构建快照。检查与记录方法见[参考复现与视觉自审](docs/style-reproduction-validation.md)。

源文件为 `.pcraft`。程序返回 PhotoCraft 原生形状、像素和分组节点的计划；保存重开后检查结果。更新时可用 `layers` 指定稳定节点 key，省略则更新全部。当前画布或节点结构变化需使用新组件 ID。项目程序是可信本机代码，独立进程不等同于安全沙箱。

`photocraft_status` 查询实际 CLI 和版本，`photocraft_inspect_document` 检查源文件，`photocraft_preview` 返回原尺寸 PNG。项目接口见[项目工具说明](docs/project-tools.md)，制作计划见[绘制程序约定](docs/drawing-programs.md)，后端边界见[PhotoCraft 接口](docs/photocraft-contract.md)。

## 项目资料与交付

在业务项目的 Git 仓库中提交 `smart-ui/`、根目录的忽略规则和项目实际使用的交付文件。项目索引、参考、程序、组件规格、可编辑源文件、布局、导出和当前预览都在 `smart-ui/`。工具生成受管文档，变更通过项目工具保存。每个标识保存当前状态，历史从 Git 查看。

请一并提交 `smart-ui/.gitattributes`，其中的规则让 Git 保留 SmartUI 校验摘要所需的文件字节。

`.smartui/` 只存本机缓存、临时文件、诊断和锁，整个目录可以忽略，停止工具后可以清空。其他成员取得业务项目提交后，可以用自己机器上的依赖继续制作。项目锁和 revision 检查当前目录的并发操作，跨分支冲突由团队在 Git 中处理。

正式导出记录当前组件产物、来源区域、文件摘要及[伸缩几何](docs/asset-geometry.md)。页面布局按资产 ID 引用当前资产，支持九宫格和独立文本框。文字由项目规范定义固定字号与角色样式，页面引用真实字体、填色、描边和投影，构建保存实际参数、离线 HTML 与同页 PNG。工具可查询样式使用位置并识别需要重建的页面；业务交互和 Unity 预制体交付需要另外处理。接口见[项目文字规范](docs/project-tools.md#项目文字规范)。

工具仓库只保存通用代码、Skill、文档、测试及示例。具体项目的参考、程序、源文件和当前制作资料留在项目中，`projects/` 保持忽略。

## 验证

```powershell
uv run --locked pytest
uv run --locked python scripts/verify_page_workflow.py --help
uv run --locked python scripts/verify_program_workflow.py --help
```

`pytest` 在源码仓库中运行；两个验证脚本同时随插件分发。自动测试和页面合成验证不代表真实制作或美术验收。原生制作须使用正常安装的 PhotoCraft，检查创建、保存重开、局部修改和实际 PNG；运行脚本记录真实结果，不预先宣称通过。通用配色及状态示例位于 `examples/drawing-programs/semantic_palette.py`。

总体方向及待验证范围见[工具链规划](docs/toolchain-roadmap.md)。
