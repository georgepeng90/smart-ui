---
name: unity-integration
description: 查询和定义 Unity 基础控件，核对并登记现有文本预制体，根据静态控件实例准备接入清单。用于控件目录、槽位、变体、预制体登记和交付依赖检查。
---

# 维护 Unity 控件目录和准备接入清单

1. 读取 `unity_protocol_schema`、`project_unity_context` 和 `project_context`，确认实际接口、控件文件状态、来源资产和两种 revision。具体字段和示例见 [Unity 接入工具](../../docs/unity-tools.md)。
2. 按用途、尺寸、槽位和状态选择已登记控件。缺少合适的控件时，用 `project_define_unity_control` 保存明确的来源和公开接口。复合控件声明直接嵌套依赖，共用差异可用变体表达。首次写入使用 `null`，后续使用 Unity 工具最新返回的 `revision`。
3. 登记前从现有工程文件核对路径、GUID 和需要在本地绑定的槽位对象 fileID。先登记基础及直接嵌套控件，随后登记变体或复合控件。变体可沿用基础控件的槽位绑定，核对返回的 `slot_sources`。缺少实际文件或槽位信息时明确指出需要的资料。用工具更新 `smart-ui/docs/unity-controls.md` 所依赖的结构化记录。
4. 为清单提供稳定的页面和实例 ID、页面尺寸、实例矩形、必要的设计参数及业务槽位绑定。只使用真实登记的控件和资源。现有浏览器页面需要调整时使用 [page-composition](../page-composition/SKILL.md)，Unity 清单的实例目前由接入工具单独接收。
5. 用 `project_prepare_unity_handoff` 核对来源并保存清单。出现文件、来源或契约错误时按工具返回的控件或资源定位问题，核对变更后更新相应来源或登记。重做时保留原有 ID，查看直接输入与依赖变化。
6. 汇报实际清单路径、涉及的页面和文件核对范围，并把 Unity 导入、运行检查与美术确认分别说明。清单工具只操作 SmartUI 资料。需要运行 Unity 或修改工程时，以用户任务授权为范围，在当前工程目录和分支操作，Unity 6 及以上优先使用已安装的 Unity CLI。
