# Unity 控件制作、预制体登记和页面构建

SmartUI 保存公开控件目录、控件制作定义和预制体登记。正式页面可以用 `control` 节点引用控件，浏览器根据制作定义展开图文，Unity Editor 包按页面实例和排列结构生成嵌套预制体。使用已有预制体时，可核对并登记实际文件。接入清单也可单独记录手工指定的静态实例，供项目核对依赖。

## 工具与持久化

在目标工程根目录中建立 SmartUI 项目。先读 `project_context` 确认来源项目、组件、图像和文字角色。生成型控件可以先定义再构建，复用工程内已有控件时需要提供真实预制体。除协议查询外，`project_root` 均为该工程根目录的绝对路径。下面的调用对象省略这一公共参数。

| 工具 | 输入与用途 |
| --- | --- |
| `unity_protocol_schema` | 无参数。返回协议版本、控件定义、清单请求和清单输出的 JSON Schema |
| `project_unity_context` | 读取目录、继承解析后的控件定义、直接使用位置、文件状态、清单登记和 revision |
| `project_define_unity_control` | 传入 `expected_revision`、`control_id`、完整 `definition`，定义或更新控件 |
| `project_register_unity_prefab` | 传入 `expected_revision`、`control_id`、`path`、`guid`、`slot_bindings`，核对并登记实际文件 |
| `project_prepare_unity_handoff` | 传入 `expected_revision`、`handoff_id`、`request`，检查来源与实例并生成清单 |
| `project_unity_build_context` | 读取构建配置、制作定义、已解析的控件和请求状态 |
| `project_configure_unity_build` | 传入 `expected_revision`、`config`，设置输出目录和文字角色映射 |
| `project_define_unity_recipe` | 传入 `expected_revision`、`control_id`、`recipe`；同时修改公开接口或状态时，一并传完整 `definition` 和 `expected_registry_revision`，原子保存两份资料 |
| `project_set_unity_bindings` | 传入 `expected_revision`、`page_id`、`bindings`，保存正式页面实例的业务槽位映射 |
| `project_prepare_unity_build` | 传入 `expected_revision`、`control_ids`、`page_ids` 和 `operation`，从当前资料生成构建请求 |
| `project_execute_unity_build` | 传入 `request_id`，通过目标 Editor 执行该请求并核对结果 |
| `project_reconcile_unity_build` | 传入同一个 `request_id`，只核对已有执行结果和资源 |

公开目录的版本来自 `project_unity_context`，对应 `smart-ui/unity/registry.json`。构建配置、制作定义与业务绑定共用 `project_unity_build_context` 的 `revision`，对应 `smart-ui/unity/construction.json`。两种记录首次写入时分别使用 `expected_revision: null`，之后使用各自的最新版本。构建请求同时固定制作资料的 `source_revision`、目录版本和构建资料版本。发生冲突时重新读取对应资料，核对差异后准备新请求。

已存在制作定义时，如果修改状态图或公开槽位会同时影响目录和制作输入，通过 `project_define_unity_recipe` 一次提交 `recipe` 与完整 `definition`，并分别提供构建资料和公开目录的 revision。工具验证最终组合及已有页面后，一起保存目录、使用文档和制作定义；不能先把其中一份保存成不一致状态。单独修改文案或节点属性时只传 `recipe`。

登记文件使用 schema 1。当前清单位于 `smart-ui/unity/handoffs/<handoff_id>.json`，同 ID 重新准备时保存当前结果。工具维护 `smart-ui/docs/unity-controls.md`，其中展示使用约定、预制体及使用位置。修改说明、接口或登记须通过工具；直接修改受管文档或清单会导致摘要校验失败。工程持久化资料随 Git 保存。

## 在工程中接入 Editor 包

在目标 Unity 6 工程的当前目录与分支接入 `com.smartui.ugui`。源码和插件分发均包含 `unity/com.smartui.ugui`。可将该包作为工程内的嵌入包保存到 `Packages/com.smartui.ugui`，或按项目约定使用指向相同包目录的版本化引用。包使用 Editor 专用程序集，依赖 uGUI 2.0 和 Newtonsoft JSON。工程须启用 Force Text 资源序列化，运行构建时 Editor 须处于空闲的编辑模式。

CLI 接入需要 `com.unity.pipeline` 及支持分组自定义命令的 Unity CLI。SmartUI 的命令程序集以 Pipeline `0.10.0-exp.1` 为起点。按项目的包依赖流程引入 Pipeline，等待当前 Editor 完成脚本编译并运行 Pipeline 服务。用以下命令核对所选工程的命令目录。

```powershell
unity --format json --no-banner --non-interactive command --project-path "<Unity 工程绝对路径>" --detail full
```

目标 Editor 应公开 `smartui execute`，其中 `request_path` 是必填字符串参数。SmartUI 通过同一个绝对工程路径发现并调用该命令。使用 SmartUI 工具准备和执行构建，工具会保存本次请求、校验目标身份和结果文件，并处理控件登记。

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

## 控件制作定义与页面引用

公开目录声明控件的用途、尺寸约定、槽位、参数及依赖。制作定义补充浏览器或 Editor 使用的节点树、默认值和槽位目标。基础控件和已有控件的制作定义使用以下字段。

| 字段 | 含义 |
| --- | --- |
| `kind` | `base` 生成普通 Prefab，`external` 为已有 Prefab 提供预览定义 |
| `width`、`height` | 正整数设计尺寸，须符合公开目录的尺寸范围 |
| `nodes` | 与页面节点格式相同的内部节点。图像和文字样式须已登记。隐含控件根使用保留 ID `root` |
| `slots` | 每个公开槽位的 `node_id` 和 `component`。文字与图像分别指向对应节点，内容和数据槽位可指向根或分组的 `rect`，事件槽位可指向根的 `button` |
| `button` | 无 Button 时为 `null`。否则填 `target_graphic` 图像节点 ID 和 `states` 状态图映射。目标图像对应一个公开槽位，映射与目录中的状态一致。可使用 `normal`、`highlighted`、`pressed`、`selected`、`disabled`；显式声明的 `normal` 须与目标图像节点的资产相同 |

变体制作定义使用 `kind: "variant"` 和 `overrides`。每个覆盖指定 `node_id`、`property` 和 `value`，支持文字节点的 `text`、`text_style_id` 以及图像节点的 `asset_id`。生成型变体从目录的 `base_control_id` 继承尺寸、节点、槽位、参数及 Button 定义。其公开目录中的 `dependencies` 填空数组，嵌套依赖由基础控件继承。省略的覆盖继续继承，显式指定当前基础值仍保留固定该值的制作意图。制作定义的实例参数支持文字槽位的 `text` 和图像槽位的 `asset_id`。

当前 `external` 预览要求目录的 `base_control_id` 为 `null`；它的预览节点不授权修改实际内部对象，因此不能作为生成型变体的基础控件。现有 Prefab Variant 可通过目录工具登记并用于静态交付清单；将它纳入正式页面的只读预览尚需扩展。Unity 导出时，节点 ID 不得使用任意大小写的 `root`，同一预制体内也不得有仅大小写不同的节点 ID。

在页面节点中保存控件 ID、布局字段和显式参数。例如，以下页面实例引用设计尺寸为 320×64 的主按钮。

```json
{
  "id": "start",
  "kind": "control",
  "control_id": "button.primary",
  "x": 160,
  "y": 640,
  "width": 320,
  "height": 64,
  "parameters": {"caption": "开始"}
}
```

实例尺寸采用控件的设计尺寸，普通页面节点的布局字段同样适用。浏览器预览验证必需参数、图像和文字样式，内部节点以实例 ID 区分。业务绑定按页面、实例与槽位单独保存，页面预览只依赖控件制作和显示数据。

## 准备、执行与核对构建

先通过 `project_configure_unity_build` 保存配置。以下示例中的 GUID 和路径应替换为工程中现有的 TextMeshPro 字体资源、材质和各自的实际 GUID。每个用于构建的文字角色都需要映射。没有文字输入时可使用空的 `fonts` 对象。

```json
{
  "output_root": "Assets/SmartUI",
  "fonts": {
    "button-title": {
      "font_asset_path": "Assets/UI/Fonts/Button SDF.asset",
      "font_asset_guid": "e411e53ae7f541659054d09af4469881",
      "material_path": "Assets/UI/Fonts/Button.mat",
      "material_guid": "727569f7ca2546dcbcf12016f1ec5e67"
    }
  }
}
```

生成的控件、页面和 Sprite 分别位于输出目录下的 `Controls/`、`Pages/` 和 `Sprites/`。待生成的控件先保存公开目录和制作定义，再准备控件构建。引用已有 Prefab 时，先保存 `external` 预览定义并登记真实资源。页面依赖的生成型控件须已登记且与当前制作输入一致。修改控件后，先应用控件构建，再准备依赖它的页面。

在 `project_set_unity_bindings` 中按实例 ID 和公开槽位 ID 提供业务键。例如，为 `start` 实例的 `click` 槽位保存 `{"start":{"click":"menu.start"}}`。准备页面构建时核对必需绑定。业务键用于项目接入记录，事件处理和运行时绑定由项目代码实现。

用 `project_prepare_unity_build` 选择构建对象。每次请求填写 `control_ids` 或 `page_ids`，另一项传空列表。根据目的选择操作，再将返回的 `request_id` 交给 `project_execute_unity_build`。

| `operation` | Editor 执行内容 |
| --- | --- |
| `plan` | 预检并报告新建、更新、复验、保持或冲突 |
| `apply` | 构建和保存需要写入的对象，重新加载并核对真实资源 |
| `verify` | 复验当前资源和引用关系 |

请求、状态与结果保存在 `smart-ui/unity/operations/<request_id>/` 的 `request.json`、`operation.json` 和 `result.json`。Editor 为各目标保存成功基线及未完成写入记录，位置为 `smart-ui/unity/import-state/`。页面的直接摘要覆盖背景颜色、本页排列、显式参数和直接资源属性；继承的控件声明及资源内容变化进入依赖摘要，可触发复验。业务绑定保存在项目资料中。

结果按实例 ID 和槽位 ID 记录嵌套槽位。每条记录包含当前预制体中实例排列容器的 `scope_file_id`，以及槽位来源预制体的 `control_id`、`guid` 和 `file_id`。两个同源实例可共享来源身份，各自在当前预制体中使用不同的实例容器。Editor 保存重开后核对各实例范围内的目标对象，SmartUI 再核对本地容器和已登记的来源。

构建请求只能执行一次。执行前，项目资料、控件目录或构建资料的 revision 变化都需要重新准备。执行后核对结果时，普通项目资料变化会触发目标路径及直接、依赖摘要的复查，相关输入一致才可继续登记。控件目录和构建资料仍须与请求的 revision 一致。部分执行结果会区分成功与失败目标，工具只登记已经核对的控件产物。

遇到超时、结果缺失或结果核对失败时，保留当前请求 ID，使用 `project_reconcile_unity_build` 检查同一次执行留下的文件。未完成核对的请求会阻止新构建。若 Editor 中断后始终没有完整结果，或资源留有 `.pending`，需要人工核对请求、目标文件及其 `.meta`、成功基线和中断记录。当前工具只报告状态和冲突，尚无自动补回执、回滚或解锁操作。

当前构建适配固定尺寸的 uGUI 预制体、Sprite、TextMeshPro 文字和标准 Button。文字支持固定字号、填色、行高、字距、水平对齐和换行，溢出策略须为 `error`。缩字、裁切、最大行数、基线排列、描边和投影待后续适配。图像实例参数和变体换图要求源图与替换图的几何和尺寸一致。已生成预制体支持调整受管属性、页面排列和显式参数。增删节点、改变父级、更换嵌套来源、修改 Button 目标或公开槽位时，需要单独迁移。

浏览器预览、合成文件测试和真实 Editor 保存重开分别验证。真实工程还需核对重复嵌套实例、持久对象引用、覆盖与恢复继承，以及实际显示和点击。

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
