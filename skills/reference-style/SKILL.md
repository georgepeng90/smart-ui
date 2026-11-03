---
name: reference-style
description: 查看项目视觉参考，区分可见特征与制作假设，归纳有依据的风格规则，并通过 SmartUI 项目 MCP 工具维护风格与组件文档。用于参考分析、风格提取及复现偏差校准。
---

# 从参考中提取可执行的项目规则

用 SmartUI 项目 MCP 工具保存参考依据与风格，而不是只在聊天中留下结论。调用参数见[项目工具说明](../../docs/project-tools.md)。编写项目绘制程序时读[程序约定](../../docs/drawing-programs.md)；实际 Krita 制作与修改使用 [krita-drawing Skill](../krita-drawing/SKILL.md)。

## 看图与记录依据

已有项目先 `project_context`，查看已有规则、组件规格和预览，避免重复建立矛盾规范。新项目先 `project_create`。用 `project_add_reference` 登记只读原图，用 `project_inspect_reference` 查看整图、关键区域和需要的色块。坐标相对完整参考，按预览测量时传 coordinate_size。

必须实际查看返回图片后归纳。按参考中适用的维度分析用色与对比、轮廓比例、描边、明暗和高光阴影、材质纹理、内容关系及尺寸伸缩规则；无需给所有参考套相同栏目。图层数量、材质和程序结构服务于当前需求，不固定为按钮或七层模板。

规则引用 reference_id 与具体区域或特征，区分可见观察、制作假设和待测参数。截图不能唯一反推原始笔刷、图层顺序、真实材质和光源数量。没有证据时不补造纹理、磨损或光照理论。

取色返回原始通道统计，不做 ICC 转换或背景合成。避开文字、遮挡与抗锯齿混色，截图表观色不直接等于原始素材色。必要时用多个清楚色块比较，说明依据及不确定性。

## 维护项目文档与校准

用 `project_update_style` 保存 description、rules、parameters 和 reference_ids，证据可用 `{reference_id, note}`。参数按项目程序需要组织，不强加固定色板、圆角或高光字段。用 `project_define_component` 保存完整组件规格，其中 program_id 引用已保存的程序；定义规格不表示已经生成资产。

每次写入用最新 revision，冲突时重新读取核对。style.md 和 components.md 由 MCP 根据 project.json 生成，不直接手改受管 Markdown 或摘要。

制作后用 `project_compare_component` 对照指定运行的实际预览与参考区域。选可比区域，需要调整展示尺度时显式传入等比倍率。查看返回拼图后再用 `project_record_feedback` 记录具体偏差。分别描述可见现象和建议改动，按依据关联计划节点 key 或风格规则 ID。查看已有对照及反馈用 `project_get_comparison`。

根据偏差范围修改组件规格、项目程序或共用规则。规则与参数更新会提升风格版本，程序更新有独立版本，旧产物保留实际执行记录并需要重新检查。修改后以同一参考区域和新运行重新对照，查看拼图后用 `project_update_feedback` 逐项记录复查结果。当前仍可见的偏差继续保持开放。

交付规则依据、对照及待验证项。用 `applies_to_current_run` 确认历史反馈结论是否适用于当前运行。用户风格确认需要用户明确给出。
