---
name: photocraft-drawing
description: 使用 PhotoCraft 执行项目 Python 程序，制作可编辑 .pcraft 组件，检查保存重开并修改指定节点。用于基础组件制作与源文件编辑；已有资产排版使用 page-composition，独立 ICON 生图使用 icon-assets。
---

# 使用 PhotoCraft 制作与修改组件

先读取项目上下文、参考和现有程序。通过 [reference-style](../reference-style/SKILL.md) 明确证据与适用规则。根据接手的人需要单独修改的内容[选择图层和可编辑属性](../../docs/editable-component-artwork.md#按接手后的修改选择做法)，再按[绘制程序约定](../../docs/drawing-programs.md)编写程序。动态文字保留在页面的文字节点。

新建组件前用 `project_search_reusable` 查同用途控件和图素，已有候选满足需求时直接引用。

1. 用 `photocraft_status` 确认本机 CLI 可用，并查看是否缺少制作所需的接口或命令。依赖缺失时报告具体缺项，不自动下载或替换后端。 [PhotoCraft 制作接口](../../docs/photocraft-contract.md#mcp-工具)
2. 保存实现 `build(parameters, spec, component_id)` 的程序，定义引用 `program_id` 的组件。缺少必需参数明确报错，不能添加制作兜底。
3. 首次编写或需要诊断计划时，用 `project_plan_component` 检查原生形状、分组、像素节点、效果及蒙版。`project_draw_component` 本身会执行程序并严格预检；已明确的参数调整无需为了固定顺序重复单独预检。预检不代表文件已经生成。
4. 用 `project_draw_component` 制作。局部反馈仅选择相关稳定 key；省略 `layers` 更新全部。当前画布尺寸或节点结构变化使用新组件 ID。
5. 查看[发布检查](../../docs/photocraft-contract.md#执行与失败)结果，用实际 PNG 核对目标尺寸和背景下的轮廓、留白、状态及外扩效果边界。需要诊断源文件或定位编辑属性时，可按需查看 `editable` 视图。首次采用一种画法或编辑入口存疑时，用代表样片的副本[试改约定的属性](../../docs/editable-component-artwork.md#保存源文件并验证交接)。

首次制作先校准少量代表组件，再用同一程序与共用参数扩展。图文关系需要验证时，复用组件预览或正式资产组合一个典型区域，不提前制作完整业务页面。已有代表样片能覆盖当前需求且对应当前输入时直接复用。

按[参考匹配与按需修正流程](../../docs/style-reproduction-validation.md)收拢明确偏差后集中修改，不默认反复整组绘制。局部更新仍有整档保存、整图渲染和重开检查，不能视为没有成本；根据实际影响明确选择节点，不能因检查失败自动扩大任务范围。修改后复查目标和受影响状态，无明确偏差不重绘，修正无收益时说明剩余问题。使用当前计划、源码和产物摘要检查冲突，并保留保存重开验证。

基础组件保留 Python 程序及 `.pcraft`。独立图标通过宿主图像工具制作，登记真实来源和当前图像，不强制转换为组件程序。已有组件外观满足任务时，直接在页面中复用，参见 [page-composition](../page-composition/SKILL.md)。

组件会用于页面分组时，可在 `spec.usage` 声明共享内边距、文字范围、点击范围、枢轴及所需状态。更新组件外观后，用 `project_page_dependencies` 查找需要重新导出的资产和受影响页面，再从当前产物导出对应状态。字段要求见[组件使用规格](../../docs/project-tools.md#组件使用规格)。

组件需要随排版伸缩时，在正式导出阶段随 PNG 交付切片边距和伸缩方向，并保留正常显示尺寸的可编辑源文件和预览。用正式资产在目标尺寸检查边角和接缝。 [切片选取与验收](../../docs/asset-geometry.md#为切片伸缩制作-ui-图形)、[正式 PNG 导出参数](../../docs/project-tools.md#正式-png-导出)

展示实际 PNG、可编辑源文件、实际使用的程序和风格依据、技术检查和待修正的视觉偏差。工具成功、模型判断和用户确认分别报告。程序是可信本机代码，不是安全沙箱；失败或超时后先核对 `.smartui/` 中的诊断和当前持久资料，不自动重放。项目参数见[项目工具](../../docs/project-tools.md)，后端边界见[PhotoCraft 接口](../../docs/photocraft-contract.md)。

交接时指出常改项的图层名称和编辑入口。 [PhotoCraft 中的编辑入口](../../docs/editable-component-artwork.md#photocraft-editing)
