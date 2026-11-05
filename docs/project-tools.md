# SmartUI 项目工具与持久化资料

SmartUI PhotoCraft v0.10.0 管理参考、风格规则、Python 绘制程序、组件规格、图像资产、正式 PNG 导出及页面布局。PhotoCraft 执行组件制作，宿主图像工具生成或编辑独立图像。项目工具登记实际素材、来源和当前产物。

## 工具参数与调用顺序

`project_root` 是业务工程根目录的绝对路径。参考图、登记图像和字体的输入文件路径也使用绝对路径。项目、参考、程序和组件等 ID 以字母或数字开头，可包含字母、数字、点、下划线和连字符。

| 操作 | 工具 | 使用方式 |
| --- | --- | --- |
| 建立和读取项目 | `project_create`、`project_context` | 建立项目或读取当前状态和 revision |
| 登记并查看参考 | `project_add_reference`、`project_inspect_reference` | 保存原图并按需预览、裁切及取样 |
| 保存风格 | `project_update_style` | 保存说明、规则、项目共用参数和参考引用 |
| 保存并读取程序 | `project_save_program`、`project_get_program` | 按程序 ID 保存和读取当前 Python 源码 |
| 定义、预检及绘制组件 | `project_define_component`、`project_plan_component`、`project_draw_component` | 定义完整规格，按需预检，以全量或指定节点范围绘制 |
| 参考对照 | `project_compare_component`、`project_get_comparison` | 对照当前组件预览与参考区域，保存及读取对照 |
| 多产物视觉检查 | `project_create_visual_check`、`project_get_visual_check` | 检查一组当前组件产物的目标尺寸和明暗 |
| 组件偏差反馈 | `project_record_feedback`、`project_update_feedback` | 记录偏差与改法，依据对照更新反馈状态 |
| 登记并查看图像 | `project_register_image`、`project_get_image` | 按资产 ID 保存和查看当前文件及来源 |
| 导出并查看正式 PNG | `project_export_assets`、`project_get_export` | 从组件当前产物裁切 PNG，并核对交付清单 |
| 保存并读取布局 | `project_save_layout`、`project_get_layout` | 按布局 ID 保存和查看当前定义 |
| 构建和查看页面 | `project_build_layout`、`project_get_page_build` | 构建布局当前定义的离线 HTML 与 PNG，按标识读取当前构建 |
| 制作和查看静态组合 | `project_create_composition`、`project_get_composition` | 制作或查看当前等比图像与单行文字组合 |

新项目先创建，再登记参考、保存风格和程序、定义组件。已有项目先读上下文。写入时传最新的 `expected_revision`，使用返回的 `revision` 继续写入。revision 根据当前结构化项目资料计算，包括分文件保存的记录。冲突时重新读取、核对差异后再决定如何修改。

## 工程目录与持久化路径

| 路径 | 内容 |
| --- | --- |
| `smart-ui/project.json` | schema 4 项目索引、当前风格及项目记录 |
| `smart-ui/docs/` | 工具生成的项目文档 |
| `smart-ui/references/` | 登记的参考原图 |
| `smart-ui/programs/<program_id>.py` | 当前程序源码；同目录的 `<program_id>.json` 保存当前元数据 |
| `smart-ui/components/<component_id>.json` | 组件规格及当前制作记录 |
| `smart-ui/sources/` | 组件 `<component_id>.pcraft`，以及 `images/<asset_id>.json` 和 `images/<asset_id>/image.<扩展名>` |
| `smart-ui/layouts/<layout_id>.json` | 当前页面布局定义 |
| `smart-ui/exports/` | 正式 PNG 导出资料 |
| `smart-ui/previews/` | 当前组件预览、对照、视觉检查、静态组合及页面构建 |
| `.smartui/` | 本机缓存、临时文件、诊断和锁 |

提交业务工程的 `smart-ui/` 及项目实际使用的交付文件。项目内路径以相对业务工程根目录的形式保存，使用 `/` 分隔；调用工具时可以用返回的绝对路径打开文件。项目引用的字体等必要资源也应能从业务工程取得。程序、组件、布局和资产按 ID 保存当前状态，历史变更通过 Git 查阅。受管 Markdown 展示项目摘要，通过工具更新结构化资料。

项目写入时在 `smart-ui/.gitattributes` 确保 `* -text` 生效，避免 Git 转换换行后导致文件摘要不匹配。

创建项目时，工具在业务工程根目录的 `.gitignore` 添加 `/.smartui/`。停止工具后可清空它；读取项目、继续修改组件、导出和构建页面依赖的是提交的资料以及正常安装的运行环境。项目锁只协调同一工程目录，revision 用来拒绝过期写入。各自检出的分支仍通过 Git 协商合并，工具不会自动合并 JSON 冲突或 PhotoCraft 文件。
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

`region` 相对来源预览，`geometry` 相对裁切后的 PNG，约束见[内容区域与伸缩几何](asset-geometry.md)。`output_path` 是 `smart-ui/exports/` 下的工程相对 PNG 路径，`manifests/` 保留给清单。已登记的正式资产需沿用对应输出路径。导出记录包括来源组件、区域、文件摘要、状态、用途和几何。每个组件的当前导出清单在 `smart-ui/exports/manifests/<component_id>.json`。用 `project_get_export` 按导出返回的 ID 查看和校验清单。布局直接引用导出登记的资产 ID。

## 可更新的页面布局

用 `project_save_layout` 保存画布、纯色背景和按叠放顺序排列的节点。用 `project_get_layout` 查看当前定义。同一资产的多个实例使用不同节点 ID，均可引用同一 `asset_id`。节点矩形以画布像素定义。

```json
{"id":"confirm","kind":"image","asset_id":"primary-button","x":32,"y":100,"width":240,"height":48}
```

图像节点在构建时按 `asset_id` 解析当前文件。正式资产遵循其伸缩几何；无几何声明的独立图像按完整原图等比居中。透明边和阴影可能延伸到目标内容框之外，按画布边界裁切。

文字节点使用 `text`、`font_path`、`font_size`、`line_height`、`align`、`wrap`、`overflow`、`color`。字体必须是工程内包含所需字符的持久 TTF/OTF 文件，可直接复用工程现有字体，且不能位于 `.smartui/`。字号和行高为正整数像素，`align` 支持 `left`、`center`、`right`，`wrap` 为布尔值，`overflow` 支持 `error` 和 `clip`。可用 `stroke_width`、`stroke_color` 声明描边。保存时校验字形和字体摘要，构建时检查布局及溢出。更换字体内容后核对文件，再更新布局所用的摘要。

`project_build_layout` 用已安装的 Chromium 构建当前定义，保存离线 HTML、同页 PNG 和渲染所用资源。每个布局保留一份当前构建，`project_get_page_build` 的 `build_id` 与 `layout_id` 相同。布局或引用资产变化后重新构建；浏览器在不同系统上的字体栅格化可能不同，保存的 PNG 记录实际输出。页面观察和复查见[页面对照与反馈](style-reproduction-validation.md#页面对照与反馈)。

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
