# Unity 控件登记与接入清单

SmartUI 可定义基础控件、核对已有 Unity 文本预制体，并根据静态页面实例准备接入清单。清单目前供项目接入准备和依赖检查使用，Unity 导入、运行时适配和业务绑定由后续阶段实现。浏览器布局继续使用 `image`、`text`、`group` 节点，清单中的实例矩形单独提供给 Unity 接入工具。

## 工具与持久化

在同一个业务工程中建立 SmartUI 项目并准备真实 Unity 预制体。先读 `project_context` 确认来源项目、组件、图像和文字角色，再使用以下工具。除协议查询外，`project_root` 均为该工程根目录的绝对路径。下面的调用对象省略这一公共参数。

| 工具 | 输入与用途 |
| --- | --- |
| `unity_protocol_schema` | 无参数。返回协议版本、控件定义、清单请求和清单输出的 JSON Schema |
| `project_unity_context` | 读取目录、继承解析后的控件定义、直接使用位置、文件状态、清单登记和 revision |
| `project_define_unity_control` | 传入 `expected_revision`、`control_id`、完整 `definition`，定义或更新控件 |
| `project_register_unity_prefab` | 传入 `expected_revision`、`control_id`、`path`、`guid`、`slot_bindings`，核对并登记实际文件 |
| `project_prepare_unity_handoff` | 传入 `expected_revision`、`handoff_id`、`request`，检查来源与实例并生成清单 |

首次定义时传 `expected_revision: null`。之后写入用 Unity 工具最近返回的 `revision`，它标识 `smart-ui/unity/registry.json` 的当前记录。返回的 `source_revision` 来自读取时的 SmartUI 制作资料，项目索引仍使用 schema 5。两种资料共用项目写锁。revision 冲突时重新读取对应上下文，核对差异后再提交。

登记文件使用 schema 1。当前清单位于 `smart-ui/unity/handoffs/<handoff_id>.json`，同 ID 重新准备时保存当前结果。工具维护 `smart-ui/docs/unity-controls.md`，其中展示使用约定、预制体及使用位置。修改说明、接口或登记须通过工具；直接修改受管文档或清单会导致摘要校验失败。工程持久化资料随 Git 保存。

## 定义控件

下例以文字按钮说明各字段。来源数组暂为空，实际引用已有制作资料时填写对应 ID。为共用状态指定图像时，例如 `"states": {"pressed": {"background": "button.pressed"}}`，需要先公开 `kind: "image"` 的 `background` 槽位，并登记 `button.pressed` 图像。

```json
{
  "expected_revision": null,
  "control_id": "button.primary",
  "definition": {
    "name": "主按钮",
    "description": "用于页面的主要操作。",
    "control_type": "button",
    "base_control_id": null,
    "dependencies": [],
    "source_component_ids": [],
    "asset_ids": [],
    "text_style_ids": [],
    "preview_asset_id": null,
    "layout": {
      "min_width": 120,
      "min_height": 48,
      "max_width": null,
      "max_height": 72
    },
    "slots": {
      "label": {
        "kind": "text",
        "component_type": "TextMeshProUGUI",
        "required": true,
        "description": "按钮标题"
      },
      "click": {
        "kind": "event",
        "component_type": "Button",
        "required": true,
        "description": "点击操作"
      }
    },
    "parameters": {
      "caption": {
        "slot_id": "label",
        "property": "text",
        "value_type": "string",
        "required": false,
        "description": "实例文案，省略时继承控件文案"
      }
    },
    "states": {}
  }
}
```

- ID 以字母或数字开头，可包含字母、数字、点、下划线和连字符。同一范围内的 ID 不得重复或仅大小写不同。矩形尺寸使用有限数值，最小值可为零，最大值为正数或 `null`，且有最大值时不得小于最小值。
- `source_component_ids` 引用制作组件，`asset_ids`、`preview_asset_id` 和状态图引用已登记图像，`text_style_ids` 引用项目文字角色。控件 ID 和制作组件 ID 各自标识对应资料。
- 删除被 Unity 控件引用的文字角色前，先通过控件工具调整 `text_style_ids`。文字规范工具会拒绝删除仍被引用的角色，并指出对应控件。
- `text` 和 `image` 槽位可声明实例参数；一个槽位的同一属性只能由一个参数控制。参数类型为 `string`、`number`、`boolean` 或 `asset_id`。实例须提供所有必需参数，其余按需传入。状态映射只接受公开图像槽位和已登记的资产。
- `content`、`event` 和 `data` 槽位接收业务绑定键。实例须提供必需业务槽位的绑定。`control_type`、`component_type`、参数属性名和绑定键由项目约定，实际 Unity 组件及业务行为仍须工程验证。
- `base_control_id` 指向已登记的基础控件，`dependencies` 指向直接嵌套的其他控件。继承和嵌套关系不得形成循环，变体不能同时嵌套其祖先。变体继承基础控件的依赖、来源引用、槽位、参数和状态映射，可以扩展接口并覆盖状态图。重新声明已有槽位时须保持类别、组件类型和必需条件，重新声明已有参数时须保持目标槽位、属性、值类型和必需条件。变体单独声明自己的尺寸约定。

## 登记实际预制体

```json
{
  "expected_revision": "<最新 Unity revision>",
  "control_id": "button.primary",
  "path": "Assets/UI/Prefabs/PrimaryButton.prefab",
  "guid": "8fa741009b9f4836a0cc64ff230cabee",
  "slot_bindings": {
    "label": 11400001,
    "click": 11400002
  }
}
```

将示例中的路径、GUID 和对象标识替换为现有文件中的实际值。`path` 使用 `Assets/` 下正斜杠分隔的工程相对路径。工具读取 UTF-8 Unity 文本序列化的 `.prefab` 及相邻的 `.prefab.meta`，核对文件头、对象声明和正文、`.meta` 中的 GUID、提供的槽位对象 fileID，以及文件摘要。GUID 使用 32 位十六进制字符串，fileID 使用有符号 64 位整数。同一预制体路径或 GUID 只能登记给一个控件。

没有基础控件时，`slot_bindings` 必须覆盖全部公开槽位，包括实例可选的槽位。变体须绑定自己新增的槽位，继承的槽位可以省略。显式绑定继承槽位时，须使用该变体文件中实际存在的对象 fileID。登记和交付清单中的 `slot_sources` 记录每个槽位所属的控件 ID 和对象 fileID。登记中的 `file.slot_bindings` 和清单中的 `prefab.slot_bindings` 只记录当前文件的绑定。基础控件的槽位对象映射变更后，按继承顺序重新核对并登记受影响的变体。

变体和复合控件先登记其基础及直接嵌套控件的预制体。工具用这些预制体的 GUID 核对当前文本中 `PrefabInstance` 对象的 `m_SourcePrefab` 引用集合，并在登记中保存 `source_guids`。该字段由工具推导，无需传参。不支持旧式 `Prefab` 对象记录。核对范围限于对象记录，不验证完整 YAML 语法；实际 Unity 导入、对象的组件类型、嵌套和继承效果、业务行为及视觉结果需要单独验证。

上下文用 `file_status` 区分 `not_registered`、`invalid` 和 `file_checked`，并在 `issue` 中说明问题。文件字节或控件契约变化后，先核对变更，再使用当前 GUID 和槽位对象重新登记。使用位置来自登记中的直接引用和清单请求。

## 准备页面接入清单

```json
{
  "expected_revision": "<最新 Unity revision>",
  "handoff_id": "main-menu",
  "request": {
    "schema_version": 1,
    "adapter_id": "project-ugui",
    "adapter_version": "1.0.0",
    "pages": [
      {
        "id": "main-menu",
        "name": "主菜单",
        "width": 640,
        "height": 960,
        "instances": [
          {
            "id": "start",
            "control_id": "button.primary",
            "x": 160,
            "y": 640,
            "width": 320,
            "height": 64,
            "parameters": {"caption": "开始"},
            "bindings": {"click": "menu.start"}
          }
        ]
      }
    ]
  }
}
```

矩形在页面左上坐标系内，位置非负，宽高为正，边缘不得超出页面，实例尺寸须满足控件约定。页面 ID 在请求内唯一，实例 ID 在本页内唯一。请求中的适配器标识和版本用于标注接入目标及计算签名，工具此时不执行适配器。清单保留参数和绑定的显式值，省略可选参数表示按控件继承约定处理。

准备时检查实例引用的控件、基础和嵌套依赖均已通过当前文件核对。工具检查相关图像的真实文件及来源是否过期、文字角色对应的字体文件与摘要、来源组件是否按当前输入制作。来源组件必须有成功产物，其计划和风格快照还须与登记的组件、规格及风格摘要一致。出现错误时按具体标识处理：重新制作过期组件并更新正式导出，修复或重新登记失配的图像、字体或预制体，再重读上下文并准备清单。受管文档或清单若有手工改动，先核对并从一致的工程版本恢复，再通过工具修改。

清单记录控件和预制体身份、工程相对资源路径、来源摘要、实例和每页签名。`direct_sha256` 对应页面输入、适配器标识与版本、实例直接选择的预制体 GUID。`dependency_sha256` 覆盖当前页引用的控件契约、预制体记录和槽位来源，包含继承和嵌套依赖，以及相关制作资源。用同一 ID 重做清单时，返回的 `page_changes` 标记新增（`new`）、直接输入变化（`inputs_changed`）、仅依赖签名变化（`dependencies_changed`）或无变化（`unchanged`）。清单的 `unity_validation` 为 `not_performed`。
