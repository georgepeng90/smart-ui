# SmartUI PhotoCraft 本地制作插件

SmartUI PhotoCraft v0.9.0 是通用游戏 UI 制作工具。通过项目 Python 程序制作 PhotoCraft 可编辑组件，管理参考、风格、图像版本、正式 PNG 导出和页面布局。独立 ICON 由宿主图像工具生成或编辑，SmartUI 登记实际文件和真实来源。

## 安装

需要 Windows、Python 3.12 及以上、uv、正常安装的 PhotoCraft v0.20.1（构建 `327c4cc82`，CLI 版本输出为 `photocraft-cli 0.20.1 (327c4cc82, 2026-11-03)`），以及支持本地 stdio MCP 的 Codex。请通过 [PhotoCraft 官方发布页](https://github.com/storytold/photocraft/releases/tag/v0.20.1)安装应用。服务查找正常安装位置或 PATH 中的 CLI；也可设置 `SMARTUI_PHOTOCRAFT_CLI` 为 CLI 的绝对路径。缺失或完整版本输出不符时明确报错，运行时不自动下载应用。

```powershell
uv sync --locked
uv run --locked playwright install chromium
uv run --locked python scripts/build_plugin.py
codex plugin marketplace add .
codex plugin add smartui-photocraft@smartui-local
```

构建输出为 `dist/smartui-photocraft`，本地来源由 `.agents/plugins/marketplace.json` 定义。修改后重新构建和安装，在新聊天中检查工具。构建脚本不修改宿主配置。浏览器页面构建需要已安装的 Chromium，失败不自动更换渲染器。

## 使用

使用 `reference-style` 分析参考；使用 `photocraft-drawing` 制作与修改基础组件；独立图标使用 `icon-assets`；已有资产排版、移动实例和改文案使用 `page-composition`。

先调用 `project_context` 读取已有项目。新项目依次创建、登记并查看参考、保存风格和共用参数、保存 Python 程序、定义组件、预检计划、实际制作并查看预览。所有写入使用最近返回的 `revision`。技术检查、模型视觉判断和用户确认分别表达。

源文件为 `.pcraft`。程序返回 PhotoCraft 原生形状、像素和分组节点的计划；保存重开后检查结果。更新时可用 `layers` 指定稳定节点 key，省略则更新全部。当前画布或节点结构变化需使用新组件 ID。项目程序是可信本机代码，独立进程不等同于安全沙箱。

服务提供 30 个 MCP 工具：3 个 PhotoCraft 工具和 27 个项目工具。`photocraft_status` 查询实际 CLI 和版本；`photocraft_inspect_document` 检查源文件；`photocraft_preview` 返回原尺寸 PNG。项目接口见[项目工具说明](docs/project-tools.md)，制作计划见[绘制程序约定](docs/drawing-programs.md)，后端边界见[PhotoCraft 接口](docs/photocraft-contract.md)。

## 项目资料与交付

业务工程根目录中的 `.smartui/project.json` 是结构化事实来源。默认文档目录 `docs/ui`，参考目录 `docs/ui/references`，程序目录 `art/ui/programs`，源文件目录 `art/ui/sources`；创建时可指定工程内相对目录。受管文档不得手改。工程内文件引用相对工程保存，整个工程移动后可继续读取。

正式导出保留明确组件运行、来源区域、文件摘要及[伸缩几何](docs/asset-geometry.md)。页面布局可复用统一资产版本，支持九宫格、独立文本框和真实字体；构建保留离线 HTML 与同页 PNG。当前没有自动设计布局、交互业务逻辑、Unity 预制体交付或自动重建全部依赖的能力。

工具仓库只保存通用代码、Skill、文档、测试及示例。具体项目的参考、程序、源文件和运行记录留在项目中，`projects/` 保持忽略。

## 验证

```powershell
uv run --locked pytest
uv run --locked python scripts/verify_page_workflow.py --help
uv run --locked python scripts/verify_program_workflow.py --help
```

`pytest` 在源码仓库中运行；两个验证脚本同时随插件分发。自动测试和页面合成验证不代表真实制作或美术验收。原生制作须使用正常安装的 PhotoCraft，检查创建、保存重开、局部修改和实际 PNG；运行脚本记录真实结果，不预先宣称通过。通用配色及状态示例位于 `examples/drawing-programs/semantic_palette.py`。

总体方向及待验证范围见[工具链规划](docs/toolchain-roadmap.md)。
