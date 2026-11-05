---
name: photocraft-drawing
description: 使用 PhotoCraft 执行项目 Python 程序，制作可编辑 .pcraft 组件，检查保存重开并修改指定节点。用于基础组件制作与源文件编辑；已有资产排版使用 page-composition，独立 ICON 生图使用 icon-assets。
---

# 使用 PhotoCraft 制作与修改组件

先读取项目上下文、参考和现有程序。通过 [reference-style](../reference-style/SKILL.md) 明确证据与适用规则，再按[绘制程序约定](../../docs/drawing-programs.md)编写项目画法。通用工具不强制某种按钮结构、材质或高光。

1. 查询 `photocraft_status`，核对实际安装路径与版本。缺失依赖明确报告，不自动下载或替换后端。
2. 保存实现 `build(parameters, spec, component_id)` 的程序，定义引用 `program_id` 的组件。缺少必需参数明确报错，不能添加制作兜底。
3. 用 `project_plan_component` 检查原生形状、分组、像素节点、效果及蒙版计划。预检不代表文件已经生成。
4. 用 `project_draw_component` 制作。局部反馈仅选择相关稳定 key；省略 `layers` 更新全部。当前画布尺寸或节点结构变化使用新组件 ID。
5. 查看真实预览及保存重开检查。用对照和视觉检查在目标尺寸与背景下核对轮廓、留白、配色和状态。未选内容变化应当失败，不能以重新生成掩盖。

基础组件保留 Python 程序及 `.pcraft`。独立图标通过宿主图像工具制作，登记真实来源和版本，不强制转换为组件程序。已有组件外观满足任务时，直接在页面中复用，参见 [page-composition](../page-composition/SKILL.md)。

展示实际 PNG、可编辑源文件、程序及风格版本、技术检查和待修正的视觉偏差。工具成功、模型判断和用户确认分别报告。程序是可信本机代码，不是安全沙箱；失败或超时后先核对运行证据，不自动重放。项目参数见[项目工具](../../docs/project-tools.md)，后端边界见[PhotoCraft 接口](../../docs/photocraft-contract.md)。
