---
name: unity-integration
description: 维护 Unity 控件目录与制作定义，通过 Unity CLI 构建基础预制体、变体和正式页面的嵌套实例，核对结果并登记。也可登记已有预制体和准备静态交付清单。
---

# 制作 Unity 控件并构建嵌套页面

交付前执行[设计稿和 Unity 交付检查](../../docs/ui-delivery-checklists.md)。
人工调整后先用只读请求核对差异。采纳的变化写入对应设计来源，重新构建预览后按清单更新同步基线。

1. 读取 `unity_protocol_schema`、`project_context`、`project_unity_context` 和 `project_unity_build_context`，确认实际工程、资料目录、公开目录、制作定义和各自的 revision。首次创建 Unity 项目时使用用户指定的 `Assets` 子目录作为 `data_root`，默认 `Assets/SmartUI`；`project_root` 始终传工程根目录的绝对路径。公开目录与构建资料独立版本化，首次写入各自使用 `null`。字段及当前限制见 [Unity 接入工具](../../docs/unity-tools.md)。
2. 用 `project_define_unity_control` 维护用途、公开槽位、参数、状态和继承关系，用 `project_define_unity_recipe` 明确内部节点与槽位目标。已有制作定义的状态或接口需要一起修改时，在制作工具中同时传完整 `definition`、`recipe` 及两份资料的 revision，原子保存。基础控件用 `base`；变体用 `variant` 和显式属性差异；引用现有工程资产用 `external` 预览定义。省略实例参数表示继承，显式等值参数仍需保留覆盖意图。不要从 PNG 或说明文字猜测组件层级。
3. 在用户指定的当前工程目录和分支核对 Force Text 资源序列化、Unity CLI 的 `run_script` 能力，以及目标工程已有的 uGUI、TextMeshPro 和 Newtonsoft JSON。通过 `project_configure_unity_build` 映射现有 TMP 字体与材质。插件随附的固定脚本由 `run_script` 在 Editor 内存中编译并执行。尚未选择工程时，可维护通用定义和预览。
4. 用 `project_prepare_unity_build` 准备控件的 `plan` 请求，再通过 `project_execute_unity_build` 执行预检。输入明确且在用户已授权范围内时，继续准备并执行 `apply`，无需额外逐步审批。每批按依赖先构建基础、再构建变体或复合控件。工具核对实际产物后登记和维护使用文档；已有资源通过 `project_register_unity_prefab` 登记，不由生成器接管内部结构。
5. 通过 [page-composition](../page-composition/SKILL.md) 在正式页面中使用 `control` 节点，保留稳定实例 ID、排列和明确参数。实例采用固定设计尺寸。用 `project_set_unity_bindings` 保存公开业务槽位的键；当前不生成业务回调。先完成控件构建登记，再另行按 `page_ids` 准备和执行页面构建，不复制坐标到另一份布局。
6. 共用修改先应用到对应控件，再处理受影响变体和页面。未变化的本地声明只需复验。缺资源、制作输入未应用、对象身份或受管属性冲突时修正明确的问题。已生成预制体的节点、层级、嵌套来源、Button 目标或公开槽位发生变化时，需要单独迁移。
7. CLI 超时、结果缺失或登记失败时保留 `request_id`，用 `project_reconcile_unity_build` 核对同一次执行。请求和结果留在 `<data_root>/unity/operations/`，目标基线及未完成写入记录留在 `<data_root>/unity/import-state/`。始终没有完整回执或仍有 `.pending` 时，人工核对已有文件和中断记录。当前工具会报告并拦截冲突，没有自动解锁或重放。
8. 需要单独交付手工指定的静态实例时，使用 `project_prepare_unity_handoff`；该操作只保存清单。汇报实际请求、产物和核对范围，分别说明工具层检查、真实 Editor 保存重开、场景引用与交互、美术确认。验证工程条件和验收用例见 [预制体复用方案](../../docs/unity-prefab-reuse-plan.md)。

## 锚点、枢轴与代表条目标注

区块根节点按页面适配关系锚定边角或拉伸；块内图标、文字、按钮通常相对所属区块中心锚定，
即 `anchor_min = anchor_max = [0.5, 0.5]`，通过相对位置保持排版。先建立真实父子分组，
不能将所有子元素直接锚定页面边角。只有明确的边缘跟随或拉伸需求才使用例外，并记录理由。
`pivot` 独立于锚点决定缩放与旋转中心，默认 `[0.5, 0.5]`，按钮尤其需要保持中心；
即使按钮贴边锚定，也不能据此把 pivot 改到边角。特殊旋转轴、进度填充等按实际需求单独声明。
修正锚点或 pivot 时保持参考尺寸下的视觉位置，再检查不同尺寸及按钮缩放效果。

重复列表保留各条目的外框，内部元素通常只标注一个完整代表条目；其他状态出现新增元素或不同约束时补充标注。
内部元素密集时可将标记分散到多个条目，但须合起来覆盖完整结构与状态差异。
通过 `unity_adaptation.<节点ID>.annotate=false` 隐藏重复内部节点的标记；默认 true，只影响该节点，
不递归隐藏子节点，不改变布局、Unity 属性或约束缺项检查。不得仅按图像或文字相似度自动去重。

## 必须执行的层级与交付要求

先定义真实语义层级，再排列元素：页面 → 功能区块 → 列表／条目／复合控件 → 内部图文。
无背景、透明的区块也必须有 group／RectTransform 容器。归属同一区块的元素放入其子层级，
禁止把大量内部图标、文字、按钮平铺在页面第一层；不以命名分组或标注框代替真实父子关系。
层级按共同移动、适配、显隐和复用边界划分，不为每个单元素机械加容器。
Unity 制作前核对布局树，交付前读取实际 Prefab 层级，确认父子关系与区块归属；平坦结构不能判为完成。
标注图必须显示不可见区块边界。紫色虚线 Layout Group 表示真实布局分组，即使关闭该组其他标记也保留边界。
设备示意中的浅色 Design Area 边框表示设计区，绿色 Safe Area 内缩边框表示实际声明的安全区，不能互相替代。
没有分组数据时先修正来源层级，不能在截图上虚构分区。

正式交付必须在回复中同时展示同次构建的干净稿、标注图，并链接 inputs.json；只有工具返回或磁盘存在不算展示。
发送回复前逐项核对两张图均已嵌入、标注含区块划分及刘海和手势区示意。缺图或旧快照缺设备区域时先重建。
未指定设备时工具按设计宽度的 6%／4% 扩展顶部／底部，并标明 Illustrative device；这是示意尺寸，不是真实机型验收。
已知设备参数时显式传入 annotation_device。仅用户明确要求无设备示意时将 top、bottom 均设为 0。
内部快速预览可关闭标注，但不得将这种预览直接作为正式交付；用户明确只要干净稿时除外。
