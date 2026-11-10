# SmartUI 项目工具与持久化资料

SmartUI PhotoCraft v0.15.0 管理参考、风格规则、文字规范、Python 绘制程序、组件规格、图像资产、正式 PNG 导出及页面布局。PhotoCraft 执行组件制作，宿主图像工具生成或编辑独立图像。项目工具登记实际素材、来源和当前产物。

## 工具参数与调用顺序

`project_root` 是业务工程根目录的绝对路径。参考图、登记图像和字体的输入文件路径也使用绝对路径。项目、参考、程序和组件等 ID 以字母或数字开头，可包含字母、数字、点、下划线和连字符。

| 操作 | 工具 | 使用方式 |
| --- | --- | --- |
| 建立和读取项目 | `project_create`、`project_context` | 建立项目，可选 `data_root`；读取当前状态和 revision |
| 登记并查看参考 | `project_add_reference`、`project_inspect_reference` | 保存原图并按需预览、裁切及取样 |
| 保存风格 | `project_update_style` | 保存说明、规则、项目共用参数和参考引用 |
| 保存文字规范 | `project_update_typography` | 保存基准字号和完整角色样式，返回使用位置及页面构建状态 |
| 保存并读取程序 | `project_save_program`、`project_get_program` | 按程序 ID 保存和读取当前 Python 源码 |
| 定义、预检及绘制组件 | `project_define_component`、`project_plan_component`、`project_draw_component` | 定义完整规格，按需预检，以全量或指定节点范围绘制 |
| 参考对照 | `project_compare_component`、`project_get_comparison` | 对照当前组件预览与参考区域，保存及读取对照 |
| 多产物视觉检查 | `project_create_visual_check`、`project_get_visual_check` | 检查一组当前组件产物的目标尺寸和明暗 |
| 组件偏差反馈 | `project_record_feedback`、`project_update_feedback` | 记录偏差与改法，依据对照更新反馈状态 |
| 登记并查看图像 | `project_register_image`、`project_get_image` | 按资产 ID 保存和查看当前文件及来源 |
| 导出并查看正式 PNG | `project_export_assets`、`project_get_export` | 从组件当前产物裁切 PNG，并核对交付清单 |
| 保存并读取布局 | `project_save_layout`、`project_get_layout` | 按布局 ID 保存和查看当前定义 |
| 构建和查看页面 | `project_build_layout`、`project_get_page_build` | 构建布局当前定义的离线 HTML 与 PNG，按标识读取当前构建 |
| 查询依赖和集中重建 | `project_page_dependencies`、`project_rebuild_pages` | 定位图像、组件和文字样式的使用位置，按指定顺序重建页面 |
| 检查不同显示场景 | `project_preview_layout_variants` | 用指定尺寸、内缩量和文案构建本机预览并汇总检查结果 |
| 制作和查看静态组合 | `project_create_composition`、`project_get_composition` | 制作或查看当前等比图像与单行文字组合 |

新项目先创建，再登记参考、保存风格和程序、定义组件。已有项目先读上下文。写入时传最新的 `expected_revision`，使用返回的 `revision` 继续写入。revision 根据当前结构化项目资料计算，包括分文件保存的记录。冲突时重新读取、核对差异后再决定如何修改。

Unity 控件目录、预制体登记及接入清单使用独立工具和独立的 `revision`。以 `project_unity_context` 返回的 `revision` 写入 Unity 登记，`source_revision` 表示读取的制作资料版本。完整参数和文件校验范围见 [Unity 接入工具](unity-tools.md)。

## 工程目录与持久化路径

同时具有 `Assets/` 和 `ProjectSettings/ProjectVersion.txt` 的目录按 Unity 工程处理。`project_create` 可用工程相对路径指定 `data_root`，例如 `Assets/UI/SmartUI`。目录必须是 `Assets` 的严格子目录，默认 `Assets/SmartUI`。同一个 Unity 工程只保存一份 SmartUI 项目索引，工具可从 `Assets` 下发现唯一的 `smartui.project.json`。打开项目时仍传 Unity 工程的绝对路径作为 `project_root`。`project_create` 和 `project_context` 会返回实际的 `data_root`。Unity 工程根目录若存在旧 `smart-ui/` 资料，工具会报错，须先明确安排迁移。

例如，给 `project_create` 传入以下参数，在 Unity 工程里选择 `Assets/UI/SmartUI` 作为资料目录。

```json
{
  "project_root": "D:/Games/example",
  "project_id": "example",
  "name": "示例工程",
  "data_root": "Assets/UI/SmartUI"
}
```

下文的 `<data_root>` 表示实际资料目录。普通制作项目使用 `smart-ui`，索引保存在 `smart-ui/project.json`。Unity 项目的目录如下。

| 路径 | 内容 |
| --- | --- |
| `<data_root>/smartui.project.json` | schema 5 项目索引、当前风格及项目记录 |
| `<data_root>/typography.json` | 已定义的文字规范、字号档位与角色样式；未定义时项目索引为 `null` |
| `<data_root>/docs/` | 工具生成的项目文档 |
| `<data_root>/references/` | 登记的参考原图 |
| `<data_root>/programs/<program_id>.py` | 当前程序源码；同目录的 `<program_id>.json` 保存当前元数据 |
| `<data_root>/components/<component_id>.json` | 组件规格及当前制作记录 |
| `<data_root>/sources/` | 组件 `<component_id>.pcraft`，以及 `images/<asset_id>.json` 和 `images/<asset_id>/image.<扩展名>` |
| `<data_root>/layouts/<layout_id>.json` | 当前页面布局定义 |
| `<data_root>/exports/` | 正式 PNG 导出资料 |
| `<data_root>/previews/` | 当前组件预览、对照、视觉检查、静态组合及页面构建 |
| `<data_root>/unity/` | Unity 目录、制作定义、清单、构建请求和回执、导入基线 |
| `<data_root>/Controls/`、`<data_root>/Pages/`、`<data_root>/Sprites/` | Unity 构建生成的控件预制体、页面预制体和图素 |
| `.smartui/` | 本机缓存、临时文件、诊断、锁和 schema 升级迁移备份 |

提交业务工程的资料目录及项目实际使用的交付文件。项目内路径以相对业务工程根目录的形式保存，使用 `/` 分隔；调用工具时可以用返回的绝对路径打开文件。项目引用的字体等必要资源也应能从业务工程取得。程序、组件、布局和资产按 ID 保存当前状态，历史变更通过 Git 查阅。受管 Markdown 展示项目摘要，通过工具更新结构化资料。

项目写入时在 `<data_root>/.gitattributes` 确保 `* -text` 生效，避免 Git 转换换行后导致文件摘要不匹配。

创建项目时，工具在业务工程根目录的 `.gitignore` 添加 `/.smartui/`。停止工具后可清理本机数据，清理前请另存仍需保留的迁移备份。读取项目、继续修改组件、导出和构建页面依赖的是提交的资料以及正常安装的运行环境。项目锁只协调同一工程目录，revision 用来拒绝过期写入。各自检出的分支仍通过 Git 协商合并，工具不会自动合并 JSON 冲突或 PhotoCraft 文件。
## 参考区域与规则依据

`region` 和取样 `rect` 都是 `[x, y, width, height]`，相对完整参考图。省略 `coordinate_size` 使用原图坐标；若按 1080 × 1920 整图预览测量，传 `[1080,1920]`，工具映射到原图。

```json
{
  "project_root": "D:/Art/example",
  "reference_id": "main",
  "region": [100, 300, 400, 180],
  "coordinate_size": [1080, 1920],
  "patches": [{"id":"face", "rect":[180,350,20,20]}]
}
```

返回图片和原像素 RGB 中位数、通道范围及透明度，保留色彩元数据；不进行 ICC 转换或背景合成。登记后的原图哈希或尺寸变化会报错。需实际查看图片，避开抗锯齿、文字和遮挡后解释取样。

规则可写为 `{id, description, evidence:[{reference_id, note}]}`，引用的参考必须已登记。把可见观察、制作假设和待测参数分清楚；截图不能唯一证明原始笔刷、真实材质或图层结构。共用 `parameters` 是项目自定义 JSON 对象，通用工具不要求色板、圆角或高光等特定字段。

## 保存程序与定义组件

`project_save_program.source` 是完整 Python 源码。保存不执行源码，也不检查程序是否已定义可用的 build；`project_plan_component` 和 `project_draw_component` 都会执行程序并严格预检计划。需要先查看或诊断计划时使用独立预检入口，已明确的参数调整不要求重复调用两个入口。程序作为可信本机代码运行，独立进程与 60 秒超时不是安全沙箱。

组件规格最少包含已保存的程序标识，其余字段由程序解释。例如采用[最小矩形程序](drawing-programs.md#最小程序示例)：

```json
{
  "project_root":"D:/Art/example",
  "expected_revision":"替换为最新revision",
  "component_id":"panel.primary",
  "name":"主面板",
  "spec":{"program_id":"flat-panel", "width":320, "height":120, "color":"#6AAD3D"}
}
```

执行时 `style.parameters` 作为 `parameters`，完整组件规格作为 `spec`，组件 ID 作为 `component_id`。程序负责其业务参数的必要字段、几何约束及错误说明。通用工具只校验程序返回的计划是否可执行；组件的具体画法由项目程序定义。

## 原文件更新

`layers=["surface"]` 中的值是计划节点 key，不是显示名称或固定角色。可选择形状、像素图层或分组；选择分组只更新该分组自身属性，不隐式包含子树；需要修改子节点时，逐一列出它们的 key。工具比较新计划与上次实际计划，拒绝非目标节点及绘制内容的变化，因此任何参数都可能用于局部调整，前提是它只影响选中范围。

已有源文件通过记录的原生整数 ID 定位。无论完整重画还是局部更新，当前画布和图层结构都必须匹配既有记录；改变尺寸或结构时暂用新组件 ID 创建变体。源文件被外部改动、源文件节点身份不符时直接报错。完整重画不能用来绕过结构检查。局部更新必须有已登记的 `plan.json` 及其摘要，供工具校验和比较。

风格摘要包含规则、共用参数和参考引用。上述内容变化后，组件需要重新检查。只更新风格说明或用户确认时，已有产物仍匹配原有绘制输入。能否只更新部分节点取决于新旧计划的实际差异。

## 当前组件产物与诊断

成功制作保存当前组件的可编辑 `.pcraft`、实际 PNG、当前绘制计划、输入和产物摘要，以及保存重开检查结果。根据记录识别后端构建、源码、规格和风格变化。规格、共用参数或程序变动后，检查受影响组件并按需重新绘制。候选文件和原生请求响应在本机 `.smartui/temp/` 生成，成功后清理。预检错误直接返回。候选制作或发布失败时，工具将诊断留在 `.smartui/diagnostics/<component_id>/`。发布中断后恢复已替换的文件。恢复若失败，错误会列出受影响路径，处理前请核对实际产物和项目状态。

组件技术检查比较保存重开前后的原生节点、属性和像素。局部修改还检查非目标内容。用目标尺寸的实际 PNG 判断参考匹配程度，并单独记录用户的美术确认。

## 对照与偏差反馈

`project_compare_component` 使用组件的当前成功预览和已登记参考。参考区域和产物区域均为 `[x, y, width, height]`，省略时取整图。参考区域可按完整缩略图测量，传入 `reference_coordinate_size` 后映射到原图坐标。产物区域以预览像素为坐标。双方分别裁切，再按显式倍率等比缩放，默认均为 1。

对照按组件和参考的组合保留当前记录，包含参考和产物裁切及棋盘背景拼图。每份裁切快照和拼图每边最多 8192 像素、每张最多 3200 万像素。工具校验来源和保存文件的摘要。用 `project_get_comparison` 按返回的 `comparison_id` 查看已有对照。对同一组件和参考再次创建对照会更新该记录。

`project_record_feedback` 分别记录可见现象 `observation` 和建议改动 `suggested_change`。有依据时用 `target_nodes` 关联计划节点 key，用 `rule_ids` 关联风格规则。用 `project_update_feedback` 更新当前状态和复查说明。`open` 表示继续跟进，`resolved` 表示偏差已修正，`dismissed` 表示结束跟进。记录和复查反馈前，先确认对照对应当前产物。复查沿用同一组件、同一参考原图和同一原图区域，标记 `resolved` 还要求产物预览摘要发生变化。复查状态只保留当前结论。用户风格确认由用户明确给出。

## 多产物视觉检查

用 `project_create_visual_check` 并排查看多个当前组件产物。传入稳定的 `check_id`，按显示顺序给出 1 至 16 个检查项。每项使用 `component_id`、`label` 和可选的 `region`、`scale`，倍率默认 1。用 `background` 指定 `#RRGGBB` 纯色背景，`columns` 为 1 至 16 的整数，默认 3。灰度总览默认开启，只看彩色时显式传 `grayscale=false`。

检查保留原尺寸裁切、显示尺寸、背景和来源摘要。已有同条件检查可通过 `project_get_visual_check` 读取；更新检查时复用原 ID。工具不执行 PhotoCraft 绘制，图像处理不做 ICC 转换，视觉判断由查看者完成。

## 正式 PNG 导出

`project_export_assets` 接收组件 ID 和导出项，使用组件当前成功产物的预览按声明区域裁切。整图导出也要显式声明区域。需要无字的背景时，源程序应提供无示例文案的导出区域。工具处理 PNG，不修改 Unity `.meta` 或导入设置。

每项导出必须完整声明以下字段；`exports` 包含 1 至 64 项，资产 ID 和输出路径在批次内唯一：

```json
{
  "asset_id": "primary-button",
  "name": "主按钮背景",
  "output_path": "smart-ui/exports/primary-button.png",
  "region": {"x": 0, "y": 0, "width": 120, "height": 60},
  "state": "normal",
  "purpose": "主操作按钮的背景，不含文字",
  "geometry": {
    "content_rect": {"x": 8, "y": 8, "width": 104, "height": 44},
    "mode": "nine_slice",
    "borders": {"left": 20, "top": 20, "right": 20, "bottom": 20}
  }
}
```

`region` 相对来源预览，`geometry` 相对裁切后的 PNG，约束见[内容区域与伸缩几何](asset-geometry.md)。`output_path` 是 `<data_root>/exports/` 下的工程相对 PNG 路径。示例用于普通制作项目，Unity 项目改用实际的 `data_root`。`manifests/` 保留给清单。已登记的正式资产需沿用对应输出路径。导出记录包括来源组件、区域、文件摘要、状态、用途和几何。每个组件的当前导出清单在 `<data_root>/exports/manifests/<component_id>.json`。用 `project_get_export` 按导出返回的 ID 查看和校验清单。布局直接引用导出登记的资产 ID。

## 页面制作的项目约定

视觉规则在项目风格中维护，字体和字号复用文字角色，控件尺寸和使用条件复用组件使用约定。
图集、本地化和资源加载路径沿用工程约定。布局对齐、边距及适配检查见页面布局和构建说明。

`project_update_ui_settings(project_root, expected_revision, settings)` 保存完整的项目 UI 设置。
两个字段均按任务需要填写，`project_context` 返回已保存的 `ui_settings`。

- `reference_size` 包含正整数 `width` 与 `height`，作为正式页面的项目参考尺寸。
- `scroll` 包含 `movement`（`Elastic` 或 `Clamped`）和布尔值 `drag_short_content`。
  普通列表继承此约定，特殊列表在交付记录中声明覆盖值及原因。

静态页面无需滚动设置。浏览器的不同尺寸预览继续使用场景尺寸。滚动行为在 Unity 验收。

## 查询可复用的控件与图素

`project_set_public_images(project_root, expected_revision, asset_ids)` 设置公开图素 ID 集合。
图像文件、来源和伸缩几何沿用现有资产登记及导出清单。
`project_search_reusable(project_root, query)` 按 ID、名称和用途检索公共图素与控件目录，
空查询返回全部候选。结果包含预览路径、资产尺寸和几何、页面使用位置、图素来源过期原因及控件登记状态。
选定候选后，页面按 `asset_id` 或 `control_id` 引用。

页面设计稿和整页预览默认交付干净稿、适配标注图和同版 `inputs.json`。
`project_build_layout` 默认保存 `files.annotated` 并返回两张图，内部快速预览可传 `annotate=false`。
标注图以同次 Chromium 渲染的干净稿为底图。灰虚线表示元素范围，金点表示贴边锚定，
两轴均贴边时，金点位于对应角。绿十字表示枢轴，蓝色双向箭头表示拉伸轴。
箭头优先放在避开可见文字的外缘，再尝试内缘。无合适位置的箭头列在 `files.annotated.omitted_stretch`。
英文图例使用实际标记。精确值保留在 `inputs.json`，缺项列在 `files.annotated.missing`。
传入 `annotation_device` 可在完整设计区上下扩展设备绘制区域。用 `top`、`bottom` 指定像素，
并用 `label` 说明设备或标明示意用途。显式设置 `extend_background=true` 可延伸页面底色，
或用 `background_node` 指定铺满全屏的图像节点。设备扩展仅影响标注图，输入快照记录这些参数。
保存布局时可用 `unity_adaptation` 按节点 ID 声明接入约束。
`anchor_min` 和 `anchor_max` 成对提供，`pivot` 可单独提供，坐标为 Unity 约定的两个 0 到 1 的值。
`viewport_insets` 和 `content_insets` 使用布局像素，分别表示遮罩视口和滚动内容的四边内缩。
这些字段随构建快照交付。Unity 构建将锚点和枢轴映射为受管 RectTransform 属性，并保持参考尺寸下的布局。
滚动内缩供工程配置 ScrollRect。浏览器仍按现有布局规则渲染。

## 项目文字规范

文字规范独立于组件绘制参数。`project_update_typography(project_root, expected_revision, typography)` 保存完整当前规范，并生成 `<data_root>/docs/typography.md`。`project_context` 返回规范、`typography_usage` 和 `page_status`。新项目可以暂不定义文字规范，但保存含文字的正式页面前必须定义所引用的角色样式。参考分析和样片校准方法见[文字设计规范](typography-plan.md)。

项目索引使用 `typography_sha256` 校验独立规范内容，未定义规范时为 `null`。规范、文档与索引通过同一次发布更新；文件内容与登记摘要不符时明确失败。

以下为字段示例，字号和颜色不构成通用默认值；字体路径须替换为工程内实际存在的绝对路径。

```json
{
  "description": "竖屏页面的文字规范；以项目参考和目标尺寸样片校准",
  "design_size": {"width": 720, "height": 1280},
  "size_scale": {"body": 24, "title": 36},
  "styles": {
    "button-primary": {
      "name": "主要按钮文字",
      "usage": "主要操作的短文案；使用深色按钮背景",
      "size_id": "body",
      "font_path": "D:/Art/example/Assets/Fonts/UI-Bold.ttf",
      "line_height": 32,
      "letter_spacing": 0,
      "fill": {"color": "#FFFFFF", "opacity": 1},
      "stroke": {"width": 2, "color": "#25344A", "opacity": 1},
      "shadow": {"offset_x": 0, "offset_y": 2, "blur": 1, "color": "#101824", "opacity": 0.6},
      "evidence": []
    }
  }
}
```

- `description` 说明适用语言、设备、背景和设计条件，允许空字符串。设计基准宽高、字号和行高为正整数像素；基准尺寸记录规范的使用条件，不自动缩放页面或文字。
- `size_scale` 和 `styles` 使用稳定 ID。角色的 `name`、`usage` 为非空文字；`size_id` 必须引用已定义档位。同一档位可以被多个角色使用。当前不支持继承或节点外观覆盖。
- `letter_spacing` 为有限的有符号像素值。填色、描边、投影各自具有独立 `color` 和 `opacity`，分别为 `#RRGGBB` 与 0 至 1。描边宽度和投影模糊值是非负有限数；投影偏移允许负数。`stroke` 或 `shadow` 为 `null` 表示不启用，透明度为 0 的效果不占用效果预留空间。
- 字体使用工程内持久的 TTF/OTF，可复用现有字体目录，不能位于 `.smartui/`。保存时登记 `font_sha256`；传入该字段时要求文件与摘要一致。明确接受新字体内容时先核对文件，再省略对应角色的旧摘要并保存。未知字段、非法字体、缺字和摘要冲突明确报错，不替换字体。
- `evidence` 可为空，补充设计写入用途和说明；有依据时每项必须包含 `reference_id`、`region:[x,y,width,height]`、`coordinate_size:[width,height]`、非空 `observation` 和 `decision`，分开记录观察及采用的设计决定。引用必须已登记，区域不得超出指定坐标尺寸。

删除仍被角色引用的档位或被页面、Unity 控件使用的角色会报错，并给出相关位置。旧构建快照和旧接入清单不阻止删除已无当前引用的角色。`typography_usage` 按样式 ID 列出当前布局（包括分组内节点）的 `{layout_id,node_id}`；Unity 控件的角色引用通过 `project_unity_context` 查询，并通过控件定义工具调整。字体内容摘要和效果的实际值参与样式摘要，说明、参考记录、档位名称及未引用角色的变化不影响其他页面。文字变化不改变基础组件的风格摘要。构建状态与更新流程见[页面依赖与集中重建](#页面依赖与集中重建)。

### 已有工程采用文字规范

当前项目接口只接受 schema 5。需要采用规范的 schema 4 工程使用独立转换脚本，先审查页面内容并准备完整文字规范及节点角色映射，不自动推断历史字号的用途。映射格式为 `{"home":{"title":"page-title","confirm-label":"button-primary"}}`，必须覆盖所有含文字的页面及其中每个文字节点，不包含多余页面或节点。

```powershell
uv run --locked python scripts/adopt_typography.py --project-root D:/Art/example --typography D:/Art/typography.json --layout-styles D:/Art/layout-styles.json
uv run --locked python scripts/adopt_typography.py --project-root D:/Art/example --typography D:/Art/typography.json --layout-styles D:/Art/layout-styles.json --apply --expected-revision <预检返回的revision>
```

schema 升级的本机备份按 `.smartui/migrations/<源版本>-to-<目标版本>/<revision>/` 组织。本次 4 → 5 升级引入文字角色规范，预检和应用结果的 `backup_directory` 均为相对业务工程根目录的路径 `.smartui/migrations/4-to-5/<revision>`。预检只返回结果，不创建备份目录或写入文件。应用前校验 revision、原受管文档、布局、原字体摘要、新规范与全部映射。应用一次发布新规范及引用，归档原索引、受影响布局、旧构建记录和含文字页面的独立 HTML/PNG。含文字的旧构建从当前索引移出，页面需重建。纯图像构建保留。目标目录已有同 revision 的备份或项目已有文字规范文件时，转换会拒绝覆盖。历史备份留在原位置，由项目维护者按需移动。

## 可更新的页面布局

用 `project_save_layout` 保存正整数画布宽高、`#RRGGBB` 背景和节点树。用 `project_get_layout` 查看当前定义。节点顺序同时决定绘制叠放次序，排在后面的节点画在上层。节点 ID 在同一布局中唯一，最多 512 个节点（含分组），最多 32 层。长度以布局像素表示。

```json
{"id":"confirm","kind":"image","asset_id":"primary-button","x":32,"y":100,"width":240,"height":48}
```

图像节点在构建时按 `asset_id` 解析当前文件。正式资产遵循其伸缩几何；无几何声明的独立图像按完整原图等比居中。透明边和阴影可能延伸到目标内容框之外，按画布边界裁切。

### 控件实例

`control` 节点引用有预览定义的 Unity 控件或变体。每个实例保存独立的 `id`、`control_id`、布局字段和显式 `parameters`。按控件声明填写公开参数，沿用当前默认值时省略对应参数键，无覆盖时传空对象。业务绑定通过 Unity 接入资料单独维护。实例的实际布局宽高须等于控件的固定设计尺寸。

```json
{"id":"confirm","kind":"control","control_id":"button.primary","x":32,"y":100,"width":240,"height":48,
 "parameters":{"caption":"确认"}}
```

浏览器从控件定义解析内部图文，按实例 ID 生成 `confirm/label` 等预览节点。正式布局只保存控件引用及其参数，显示场景中的 `texts`、`images` 和 `text_styles` 只按正式布局中的图文节点 ID 修改。预览展开后同样限制为 512 个节点、32 层。Unity 从同一正式布局取得分组、图文和控件实例的静态矩形。控件制作和接入见 [Unity 接入工具](unity-tools.md)。

### 分组与尺寸

分组节点包含 `id`、`kind: "group"`、`layout` 和非空 `children`，可嵌套。根节点使用非负 `x`、`y` 和正整数 `width`、`height`。根分组也可用 `region: "canvas"` 或 `region: "safe"` 替代这四个字段，分别占满画布或安全区。使用安全区时，`project_save_layout` 的 `safe_area` 参数必须同时提供 `top`、`right`、`bottom`、`left` 四边的非负整数内缩量，并为画布留下正宽高。

```json
{
  "id": "action-row",
  "kind": "group",
  "region": "canvas",
  "layout": {"direction": "row", "padding": 16, "gap": 8, "align": "center"},
  "children": [
    {"id": "mark", "kind": "image", "asset_id": "confirm-icon", "width": 32, "height": 32},
    {"id": "label", "kind": "text", "text_style_id": "button-primary", "text": "确认",
     "width": "fill", "height": 40, "align": "left", "wrap": false, "overflow": "error"}
  ]
}
```

| `layout.direction` | 子节点定位与尺寸 | 支持的排列参数 |
| --- | --- | --- |
| `absolute` | 必须提供相对于分组内边距起点的 `x`、`y`，宽高为正整数 | `padding` |
| `row` / `column` | 沿水平或垂直主轴排列，省略 `x`、`y`，宽高可用整数或 `fill` | `padding`、`gap`、主轴 `justify`、交叉轴 `align` |
| `overlay` | 在同一内容区域内叠放，省略 `x`、`y`，宽高可用整数或 `fill` | `padding`、水平 `justify`、垂直 `align` |

`padding` 可用一个非负整数表示四边，也可用四边对象。未绑定组件时默认 0，`gap` 默认 0。对齐与分布默认 `start`，也支持 `center` 和 `end`。行、列的 `justify` 另支持 `space-between`，行的 `align` 另支持 `baseline`。主轴上的多个 `fill` 均分扣除固定尺寸及间距后的空间，余数依次分给靠前节点。交叉轴和叠放中的 `fill` 使用该轴全部可用空间。尺寸放不进分组时明确报错。

`visual_offset` 必须同时提供 `x`、`y` 整数校正量，用于可见中心偏离几何中心的资产。仅在居中对齐的轴上填写非零值，正值将节点向左或向上移。行、列的排列轴不支持校正。基线行的直接子节点必须是单行、不换行的文字。Chromium 测量字体实际基线并对齐，若对齐后节点超出分组内容区则报错。

### 组件使用规格

页面分组可用 `component_id` 引用已声明 `spec.usage` 的组件。用 `project_define_component` 随组件规格保存 `usage`，其五个字段如下。

| 字段 | 含义与要求 |
| --- | --- |
| `padding` | 四边非负整数内缩量，作为绑定分组的共享内边距。分组省略 `layout.padding` 时采用此值，显式填写时须与共享值一致 |
| `text_insets` | 四边非负整数内缩量，从分组外框计算文字安全范围 |
| `hit_insets` | 四边整数内缩量，负值扩大该侧的点击范围 |
| `pivot` | `{x,y}`，两个坐标各为 0 到 1 的数值，按分组外框换算枢轴位置 |
| `required_states` | 非空列表，列出不重复的状态名称，与正式导出的 `state` 比对 |

三个四边对象都须完整提供 `top`、`right`、`bottom`、`left`。边距须为实例留下正宽高。构建记录每个绑定分组的实际文字范围、点击范围、枢轴、规格摘要及导出状态。状态报告的 `available` 只包括来源仍有效的导出，`missing` 表示尚未登记的必需状态，`stale` 表示已有导出但全部来源过期的必需状态。点击范围和枢轴供静态检查和后续交付使用。

### 文字节点与溢出策略

文字节点使用 `text_style_id` 引用文字规范中的完整角色，另存文案、文本框和排版策略。字体、字号、颜色和效果由角色样式提供。

```json
{"id":"confirm-label","kind":"text","text_style_id":"button-primary","text":"确认","x":40,"y":104,"width":224,"height":40,"align":"center","wrap":false,"overflow":"error"}
```

`align` 支持 `left`、`center`、`right`，`wrap` 为布尔值。可排列分组内的不换行单行文字可用 `width: "content"`，由 Chromium 按实际字体、字距、字形和效果测量宽度，再安排相邻节点。该宽度策略不能与 `shrink` 同用。

| `overflow` | 超出文本框或可选正整数 `max_lines` 时 |
| --- | --- |
| `error` | 报告节点及实际测量范围，构建失败 |
| `clip` | 按节点框及行数限制裁切，记录实际溢出 |
| `shrink` | 从角色字号逐个整数像素减小，行高按比例调整。必须显式提供正整数 `min_font_size`，不得大于角色字号。减至下限仍放不下时构建失败 |

保存和构建均解析当前样式并校验字体摘要及文案字形。布局保存样式引用，构建记录实际角色 ID、`text_style_sha256`、解析值、字体快照和渲染边界。渲染报告另记录实测行数、实际字号、基线、溢出和效果范围。

`effect_insets` 记录四边效果预留：可见描边占总宽度的一半；投影从字形产生，模糊值大于 0 时每侧预算为 `ceil(2 × blur) + 1` 像素，无模糊时为 0，再分别加减水平和垂直偏移。每侧取描边、投影和 0 的最大值。`effects_bounds` 联合实际断行宽度、CSS 行高及 Canvas 字形外伸，再扩展效果预算；负字距及字体负边距也参与检查。它是布局校验范围，精确像素以 PNG 为准，不能直接作为 Unity 的描边或投影参数。

`project_build_layout` 用已安装的 Chromium 构建当前定义，保存离线 HTML、同页 PNG 和渲染所用资源。每个布局保留一份当前构建，`project_get_page_build` 的 `build_id` 与 `layout_id` 相同。布局、图像、实际使用的文字样式或控件定义变化后显式重建。含控件的构建以 `control_dependencies` 记录实际使用的控件、基础控件和嵌套控件的定义摘要。读取快照不依赖源字体和图像仍然存在；一致性标记比较当前登记资料，重新保存或构建时才校验实际来源文件。浏览器在不同系统上的字体栅格化可能不同，保存的 PNG 记录实际输出。页面观察和复查见[页面对照与反馈](style-reproduction-validation.md#页面对照与反馈)。

构建中的图像快照按资产 ID 保存在页面目录，字体快照键由工程相对字体路径确定。完整字体按内容 SHA-256 存入项目的 `<data_root>/previews/fonts/`，页面间复用同一份字节。`inputs.json` 记录相对于页面目录的资源路径，快照清单、节点引用和文件摘要须一致。共享字体随预览目录一起保留，供构建记录独立校验。

离线 HTML 按页面实际文案内嵌字体子集，按内容摘要内嵌每张图片一次。可直接双击 HTML 打开，图像可见边界继续通过 Canvas 像素计算。

### 浏览器检查与显示场景

HTML 可缩放，并可显示安全区、布局框、内容框、可见范围和文字基线。分组绑定组件后也可检查文字范围、点击范围和枢轴。PNG 保存关闭辅助线时的同一页面。图像的 `visible_box_source` 为 `canvas_alpha`，边界来自 Canvas 的非透明像素；文字为 `estimated_text_ink`，边界依据实际显示行的字形与效果估计，裁切隐藏的行不计入。文字完整的 `effects_bounds` 仍用于诊断，不代表裁切后可见范围。无可见内容时 `visible_box` 为 `null`。

渲染结果的 `warnings` 报告内容框重叠、组件文字效果越过文字范围、必需状态缺失或来源过期，以及低于配置阈值的实际字号。浏览器中可展开布局提示查看涉及的节点和原因。

`project_save_layout` 可传入 `checks`。其中 `min_font_size` 为可选正整数，小于它的实际字号会产生警告。此阈值独立于单个节点用于缩字的同名参数。`allow_overlap` 为可选的节点 ID 对列表，允许为顶层同级节点或 `absolute` 分组的同级节点明确放行重叠。每对须引用两个不同的现存 ID，顺序无关且不可重复。`overlay` 本身允许其直接子节点叠放。自动警告帮助定位问题，视觉层级、文字可读性和目标状态仍须查看实际页面。

`checks.visual_alignment_tolerance` 为可选非负整数像素。声明后，工具检查居中轴上各直接子节点的可见中心与分组内容中心的偏移：行分组检查纵向，列分组检查横向，叠放分组按 `align` 与 `justify` 各自启用。子分组采用其内容框中心，完全透明的图像不参与比较。超出容差时记录 `visual_alignment_offset` 提示，不自动移动节点。像素边界和文字估计都只辅助视觉复查；需要校正时可明确设置 `visual_offset`。

`project_preview_layout_variants(project_root, expected_revision, layout_id, variants)` 用同一正式布局对比画布尺寸、安全区、译文、字体角色和图像状态。`variants` 为非空列表，场景 ID 不区分大小写且须唯一。每个场景提供 `id`、`width`、`height`、`texts`。`texts` 按现有文字节点 ID 替换非空文案，可传空对象。

可选的 `safe_area` 覆盖内缩量。`text_styles` 将现有文字节点 ID 映射到已登记的文字角色 ID，可与 `texts` 一起检查译文和字体。`images` 将现有图像节点 ID 映射到已登记的资产 ID。对比组件状态时，可将场景命名为 `normal`、`pressed` 等，并给同一节点选用对应的资产。每个场景按实际引用校验资产来源、样式和字体，再按原布局约束排版。

成功时在 `.smartui/previews/variants/<layout_id>/` 发布本机批次索引、报告及各场景的 HTML、PNG、检查报告。返回索引和报告路径、场景结果及所用项目 revision 和布局摘要。场景预览独立于正式布局、正式构建及项目版本记录。批次预检或渲染失败时保留上次成功结果，并报告失败阶段、场景及本机诊断目录。目录保存该布局最近一次成功批次，修改前后的正式产物可用 Git 对应版本复查。

### 页面依赖与集中重建

`project_page_dependencies(project_root)` 返回当前 `revision`，并按 ID 列出 `components`、`assets`、`text_styles`、`unity_controls` 和 `pages`。组件列出正式导出的 `asset_ids`、直接引用组件的分组位置、当前运行及复查状态。`unity_controls` 列出正式页面直接引用各控件的位置。资产和文字样式包含控件内部的实际预览引用位置。未构建页面也纳入查询。每个页面给出 `not_built`、`stale` 或 `current` 状态及对应原因。过期的正式导出会报告来源组件、导出所用的运行、当前运行及需要执行的动作。

`page_status` 和读取快照的结果分别给出 `matches_layout`、`matches_images`、`matches_typography`、`matches_sources`、`matches_component_usage` 和 `matches_unity_controls`。`matches_bindings` 表示图像与文字样式均匹配，`is_current` 要求各项均匹配。图像来源过期时，仍能读取此前的快照。重建引用这类资产的页面前，须按当前组件输入重画并重新导出，或在已有当前绘制结果时直接重新导出。只绑定 `spec.usage` 的分组可直接应用当前规格。

`project_rebuild_pages(project_root, expected_revision, layout_ids)` 接收非空且无重复的布局 ID 列表。工具先预检所有目标及其构建输入，再按指定顺序逐页发布。成功返回 `completed_layout_ids` 和当前 `revision`。预检失败时不会发布页面。渲染或发布中途失败时停止，错误报告给出失败页面、已完成页面、当前 revision 及原因，便于修正后从对应页面继续。

## 独立图像资产与静态组合预览

用宿主图像工具制作或编辑独立图像，先看实际结果，再将需要使用的 PNG、JPEG 或 WebP 文件交给 `project_register_image`。传入项目最新 revision、`asset_id`、`name`、实际绝对路径 `source_path` 和 `provenance`。登记按原字节保存图像及尺寸、模式、透明度和 SHA-256。同一资产 ID 对应一份当前图像；`project_get_image` 按 ID 查看当前文件及来源。

`provenance.kind` 为 `generated` 或 `edited` 时填写非空 `tool` 和 `prompt`，为 `imported` 时填写非空 `description`。如实记录已取得的模型、种子或参考等信息。正式组件导出由导出工具登记来源。

静态组合提供固定坐标的简单 PNG 检查。用 `project_create_composition` 指定稳定的 `composition_id`、正整数画布尺寸、`#RRGGBB` 背景和按叠放顺序排列的节点。每项使用唯一 ID 和画布像素位置；支持当前组件预览、当前独立图像和单行文字。文字也使用工程内的持久 TTF/OTF 文件。

| `kind` | 除 `id`、`x`、`y` 外的必填字段 | 可选字段 |
| --- | --- | --- |
| `component` | `component_id`、`width`、`height` | `region` |
| `image` | `asset_id`、`width`、`height` | `region` |
| `text` | `text`、`font_path`、`font_size`、`color` | `stroke_width`、`stroke_color` |

需要文本框、换行、明确伸缩几何或 HTML 时使用页面布局。用 `project_get_composition` 按 ID 查看当前组合；引用来源更新后显式重建受影响的组合。
