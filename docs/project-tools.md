# SmartUI 项目工具与持久化资料

SmartUI PhotoCraft v0.11.0 管理参考、风格规则、文字规范、Python 绘制程序、组件规格、图像资产、正式 PNG 导出及页面布局。PhotoCraft 执行组件制作，宿主图像工具生成或编辑独立图像。项目工具登记实际素材、来源和当前产物。

## 工具参数与调用顺序

`project_root` 是业务工程根目录的绝对路径。参考图、登记图像和字体的输入文件路径也使用绝对路径。项目、参考、程序和组件等 ID 以字母或数字开头，可包含字母、数字、点、下划线和连字符。

| 操作 | 工具 | 使用方式 |
| --- | --- | --- |
| 建立和读取项目 | `project_create`、`project_context` | 建立项目或读取当前状态和 revision |
| 登记并查看参考 | `project_add_reference`、`project_inspect_reference` | 保存原图并按需预览、裁切及取样 |
| 保存风格 | `project_update_style` | 保存说明、规则、项目共用参数和参考引用 |
| 保存文字规范 | `project_update_typography` | 保存固定字号和完整角色样式，返回使用位置及页面构建状态 |
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
| `smart-ui/project.json` | schema 5 项目索引、当前风格及项目记录 |
| `smart-ui/typography.json` | 已定义的文字规范、字号档位与角色样式；未定义时项目索引为 `null` |
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

## 项目文字规范

文字规范独立于组件绘制参数。`project_update_typography(project_root, expected_revision, typography)` 保存完整当前规范，并生成 `smart-ui/docs/typography.md`。`project_context` 返回规范、`typography_usage` 和 `page_status`。新项目可以暂不定义文字规范，但保存含文字的正式页面前必须定义所引用的角色样式。参考分析和样片校准方法见[文字设计规范](typography-plan.md)。

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

删除仍被角色引用的档位或被页面使用的角色会报错，并给出相关位置。旧构建快照不阻止删除已无当前页面引用的角色。`typography_usage` 按样式 ID 列出 `{layout_id,node_id}`；`page_status` 按已构建页面列出 `matches_layout`、`matches_images`、`matches_typography` 和 `matches_bindings`，最后一项为图像与文字样式均匹配。未构建页面仍可通过使用位置查询。字体和效果的实际值参与样式摘要，说明、参考记录、档位名称及未引用角色的变化不会使无关页面过期。文字变化不改变基础组件的风格摘要。

### 已有工程采用文字规范

当前项目接口只接受 schema 5。需要采用规范的 schema 4 工程使用独立转换脚本，先审查页面内容并准备完整文字规范及节点角色映射，不自动推断历史字号的用途。映射格式为 `{"home":{"title":"page-title","confirm-label":"button-primary"}}`，必须覆盖所有含文字的页面及其中每个文字节点，不包含多余页面或节点。

```powershell
uv run --locked python scripts/adopt_typography.py --project-root D:/Art/example --typography D:/Art/typography.json --layout-styles D:/Art/layout-styles.json
uv run --locked python scripts/adopt_typography.py --project-root D:/Art/example --typography D:/Art/typography.json --layout-styles D:/Art/layout-styles.json --apply --expected-revision <预检返回的revision>
```

第一条命令只预检，返回 revision、转换页面、需重建页面和备份目录。应用前校验原受管文档、布局、原字体摘要、新规范与全部映射。应用一次发布新规范及引用，归档原索引、受影响布局、旧构建记录和含文字页面的独立 HTML/PNG，目录为 `smart-ui/adoptions/typography/<revision>/`。含文字的旧构建从当前索引移出，页面需重建；纯图像构建保留。已有备份或文字规范文件不会被覆盖。脚本不支持其他格式，也不为正常接口保留旧文字字段。

## 可更新的页面布局

用 `project_save_layout` 保存画布、纯色背景和按叠放顺序排列的节点。用 `project_get_layout` 查看当前定义。同一资产的多个实例使用不同节点 ID，均可引用同一 `asset_id`。节点矩形以画布像素定义。

```json
{"id":"confirm","kind":"image","asset_id":"primary-button","x":32,"y":100,"width":240,"height":48}
```

图像节点在构建时按 `asset_id` 解析当前文件。正式资产遵循其伸缩几何；无几何声明的独立图像按完整原图等比居中。透明边和阴影可能延伸到目标内容框之外，按画布边界裁切。

文字节点使用 `text_style_id` 引用文字规范中的完整角色，另存文案、文本框和排版策略，不接受逐节点字体、字号、颜色或效果字段。

```json
{"id":"confirm-label","kind":"text","text_style_id":"button-primary","text":"确认","x":40,"y":104,"width":224,"height":40,"align":"center","wrap":false,"overflow":"error"}
```

`align` 支持 `left`、`center`、`right`，`wrap` 为布尔值，`overflow` 必须为 `error` 或 `clip`。保存和构建均解析当前样式，校验字体摘要及文案字形；布局本身保存引用，不复制外观参数。浏览器将描边和投影的预留空间纳入换行与边界检查，超出时按策略报错或裁切，不自动缩字。构建记录实际角色 ID、`text_style_sha256`、解析值、字体快照和渲染边界。描边总宽度沿字形内外展开，投影使用自己的颜色和透明度；效果预留范围是明确的版式约束，不是跨渲染器通用的像素测量值。

`effect_insets` 记录四边效果预留：可见描边占总宽度的一半；投影从字形产生，模糊值大于 0 时每侧预算为 `ceil(2 × blur) + 1` 像素，无模糊时为 0，再分别加减水平和垂直偏移。每侧取描边、投影和 0 的最大值。`effects_bounds` 联合实际断行宽度、CSS 行高及 Canvas 字形外伸，再扩展效果预算；负字距及字体负边距也参与检查。它是布局校验范围，精确像素以 PNG 为准，不能直接作为 Unity 的描边或投影参数。

`project_build_layout` 用已安装的 Chromium 构建当前定义，保存离线 HTML、同页 PNG 和渲染所用资源。每个布局保留一份当前构建，`project_get_page_build` 的 `build_id` 与 `layout_id` 相同。布局、图像或实际使用的文字样式变化后显式重建。读取快照不依赖源字体和图像仍然存在；一致性标记比较当前登记资料，重新保存或构建时才校验实际来源文件。浏览器在不同系统上的字体栅格化可能不同，保存的 PNG 记录实际输出。页面观察和复查见[页面对照与反馈](style-reproduction-validation.md#页面对照与反馈)。

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
