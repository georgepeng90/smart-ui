# 项目工具与执行记录

SmartUI PhotoCraft v0.9.0 提供二十七个项目工具，与三个底层 `photocraft_*` 工具共存。项目管理参考、风格规则、Python 绘制程序、组件规格、运行记录、参考对照、偏差反馈、多产物视觉检查、独立图像资产、正式 PNG 导出、可更新页面布局、浏览器构建和静态组合预览。具体画法属于项目程序。

当前项目组件指关联绘制程序的 PhotoCraft 制作对象。ICON（图标）图像采用独立资产流程：生成和编辑由宿主图像工具完成，SmartUI 登记真实产物及来源，再以静态组合预览检查。SmartUI 不内置生图后端；不要将图像资产伪装成组件成功运行。`project_add_reference` 登记的是只读参考，不是生产资产导入。

## 工具参数与调用顺序

`project_root` 使用业务工程根目录的绝对路径，参考输入的 `source_path` 也使用绝对路径。项目、参考、程序和组件 ID 以字母或数字开头，可包含字母、数字、点、下划线和连字符。

| 工具 | 参数 | 作用 |
| --- | --- | --- |
| `project_create` | `project_root: str, project_id: str, name: str, paths: dict \| null = null` | 创建项目及受管文档，拒绝覆盖 |
| `project_context` | `project_root: str` | 读取摘要和最新 revision |
| `project_add_reference` | `project_root: str, expected_revision: str, reference_id: str, source_path: str` | 登记只读 PNG/JPEG/WebP 参考，生成预览 |
| `project_inspect_reference` | `project_root: str, reference_id: str, region: list[int] \| null = null, patches: list[dict] \| null = null, coordinate_size: list[int] \| null = null` | 返回参考区域图片及原像素取样，不写项目 |
| `project_update_style` | `project_root: str, expected_revision: str, description: str, rules: list[dict], parameters: dict, reference_ids: list[str]` | 完整替换风格说明、规则、共用参数及引用 |
| `project_save_program` | `project_root: str, expected_revision: str, program_id: str, name: str, description: str, source: str` | 只检查语法并保存程序源码与版本 |
| `project_get_program` | `project_root: str, program_id: str` | 读取程序元数据和源码，不执行 |
| `project_define_component` | `project_root: str, expected_revision: str, component_id: str, name: str, spec: dict` | 创建或替换规格，保留执行历史；spec 必须引用已保存的 program_id |
| `project_plan_component` | `project_root: str, component_id: str` | 执行程序并预检计划，不调用 PhotoCraft、不登记绘制成功 |
| `project_draw_component` | `project_root: str, expected_revision: str, component_id: str, layers: list[str] \| null = null` | 执行、保存、重开检查并记录；省略 layers 完整绘制 |
| `project_compare_component` | `project_root: str, expected_revision: str, component_id: str, run_id: str, reference_id: str, reference_region: list[int] \| null = null, reference_coordinate_size: list[int] \| null = null, artifact_region: list[int] \| null = null, reference_scale: float = 1.0, artifact_scale: float = 1.0` | 保存参考与运行预览的对照，返回 JSON 和拼图 |
| `project_get_comparison` | `project_root: str, comparison_id: str` | 校验并读取对照快照、拼图与反馈详情 |
| `project_create_visual_check` | `project_root: str, expected_revision: str, items: list[dict], background: str, columns: int = 3, grayscale: bool = true` | 保存多份运行预览的彩色总览与可选灰度图 |
| `project_get_visual_check` | `project_root: str, check_id: str` | 校验并读取已保存的视觉检查图与来源记录 |
| `project_register_image` | `project_root: str, expected_revision: str, asset_id: str, name: str, source_path: str, provenance: dict` | 按原字节登记 PNG/JPEG/WebP 图像的新版本，不自动选用 |
| `project_select_image` | `project_root: str, expected_revision: str, asset_id: str, version: int` | 显式选用已登记版本，不重建组合或表示用户确认 |
| `project_get_image` | `project_root: str, asset_id: str, version: int` | 校验并查看指定版本及真实来源 |
| `project_export_assets` | `project_root: str, expected_revision: str, component_id: str, run_id: str, exports: list[dict]` | 从明确成功运行的区域批量导出正式 PNG，登记统一资产版本及清单 |
| `project_get_export` | `project_root: str, export_id: str` | 校验历史清单与归档资产，不依赖生产路径仍为旧版本 |
| `project_save_layout` | `project_root: str, expected_revision: str, layout_id: str, name: str, width: int, height: int, background: str, nodes: list[dict]` | 保存完整页面定义及修订，校验真实资产与字体，不生成图像 |
| `project_get_layout` | `project_root: str, layout_id: str, version: int \| null = null` | 读取当前布局或明确的历史修订 |
| `project_build_layout` | `project_root: str, expected_revision: str, layout_id: str, version: int \| null = null` | 构建不可变浏览器快照，返回离线 HTML、同页 PNG 和资源证据 |
| `project_get_page_build` | `project_root: str, build_id: str` | 校验历史构建，返回 JSON、PNG 与两种匹配状态 |
| `project_create_composition` | `project_root: str, expected_revision: str, width: int, height: int, background: str, nodes: list[dict]` | 创建固定坐标的静态组合预览并保存源裁切、字体及 PNG 快照 |
| `project_get_composition` | `project_root: str, composition_id: str` | 校验并读取已保存的组合及来源快照 |
| `project_record_feedback` | `project_root: str, expected_revision: str, comparison_id: str, observation: str, suggested_change: str, target_nodes: list[str] \| null = null, rule_ids: list[str] \| null = null, source: str = "model"` | 记录带稳定 ID 的偏差反馈 |
| `project_update_feedback` | `project_root: str, expected_revision: str, feedback_id: str, status: str, comment: str, comparison_id: str, source: str = "model"` | 更新反馈状态并追加复查历史 |

新项目通常依次执行：创建 → 登记并查看参考 → 保存规则和参数 → 保存程序 → 定义组件 → 生成计划 → 绘制 → 查看预览。已有项目先读上下文和已有程序，避免覆盖项目已有制作逻辑。程序接口、可执行最小示例及计划校验规则见[绘制程序约定](drawing-programs.md)。

每次状态写入返回新 `revision`；后续写入必须使用它。revision 是 `.smartui/project.json` 原始字节的 SHA-256，与风格版本和程序版本无关。冲突后重新读取并核对差异，不自动覆盖。

## 工程目录与持久化路径

状态文件固定为 `<project_root>/.smartui/project.json`。`project_create.paths` 可局部覆盖下表，省略项使用默认值；状态中保存完整映射。配置只接受工程内相对目录，绝对路径、越出工程的路径和非法配置会直接报错。

| 配置名 | 默认值 | 内容 |
| --- | --- | --- |
| `documents` | `docs/ui` | 自动生成的 `style.md`、`components.md` |
| `references` | `docs/ui/references` | 登记并保存的参考原图 |
| `programs` | `art/ui/programs` | `<program_id>/<version>.py` |
| `sources` | `art/ui/sources` | `<component_id>.pcraft` 及 `images/<asset_id>/<version>/image.<png|jpg|webp>` |

目录映射不改变 `project_root` 的含义。参考输入在工程外时，登记操作将其复制到 `<references>/<reference_id>/<原文件名>`，后续读取和摘要检查针对该副本。工程内输入可保留其原有位置。

项目状态 JSON 中的工程内文件引用保存为工程相对 POSIX 路径，即用 `/` 分隔；明确的工程外路径可以使用绝对路径。运行时 API 返回可直接读取的绝对路径，程序元数据的 `source_path` 除外，它保持工程相对路径。生成文档的资源链接相对于 `documents` 目录，移动整个工程后仍可使用。历史 `operations.json`、`result.json` 等原始执行证据保留当时调用的路径和字节，继续制作使用项目状态中解析后的文件引用。

创建项目时，工具在 `.smartui/.gitignore` 中写入缓存、锁、临时文件 的忽略规则，并保留已有规则。状态、运行、对照及视觉检查记录应保留，不要整体忽略 `.smartui/`。

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

`project_save_program.source` 是完整 Python 源码。保存不执行源码，也不检查程序是否已定义可用的 build；用 `project_plan_component` 验证运行行为。程序作为可信本机代码运行，独立进程与 60 秒超时不是安全沙箱。

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

共享规则或参数变化后，组件需要重新检查；能否只更新部分节点取决于计划对比，不根据特定参数名或高光角色判断。只更新目标节点不能把未实际执行的变化宣称为完成。

## 状态、版本与证据

`.smartui/project.json` 使用 schema 3，主要字段如下：

| 字段 | 含义 |
| --- | --- |
| `schema_version/project_id/name` | 结构版本、项目标识和名称 |
| `paths` | 文档、参考、程序与源文件的完整工程内相对目录映射 |
| `references` | 只读参考路径、摘要、尺寸、色彩元数据和预览 |
| `style` | version、description、rules、parameters、reference_ids、user_confirmation |
| `programs` | 按 ID 保存程序元数据：id、name、description、version、sha256、source_path、history |
| `components` | 名称、spec、目标风格版本、最近成功记录和全部尝试 |
| `comparisons` | 可选，记录对照的参考、运行、裁切与显示参数及快照摘要 |
| `visual_checks` | 可选，记录检查图使用的成功运行、裁切与显示参数，以及保存图片的摘要 |
| `images` | 可选，记录独立图像资产及不可变连续版本；缺省表示尚无登记图像 |
| `compositions` | 可选，记录静态组合、源节点及图像/字体快照；缺省表示尚无组合 |
| `feedback` | 可选，记录对照反馈、当前状态与复查历史 |
| `document_hashes` | 自动生成 style.md 和 components.md 的摘要 |

程序源码默认路径为工程相对路径 `art/ui/programs/<id>/<version>.py`。读取时核对实际源码与记录摘要；源码变化通过工具保存为版本，不手改文件绕过记录。程序更新使依赖组件的 `needs_review` 为 true；历史 run 仍记实际程序版本与源码哈希，不把旧产物改成新版本。

规则或共用参数变化提升风格版本；只改风格说明或引用清单不升级。`needs_review` 是派生状态：缺少成功产物、当前规格/风格/程序与实际记录不同，或最近尝试失败，都需要重新检查。

源文件默认位于 `art/ui/sources/<component_id>.pcraft`。每次运行在 `.smartui/runs/<run_id>/` 保存 `program.py`、`plan.json`、`style-snapshot.json`、`operations.json` 和 `result.json`。成功时另有 `before-reopen.png`、`preview.png`、`output/<component_id>.pcraft` 与 `reopened/<component_id>.pcraft`；更新已有文件时保留 `input.pcraft`。运行记录的 `backend` 包含实际 `name=photocraft`、`version`、CLI `sha256` 和 `cli_path`；`source_snapshot_path` 指向 `output/` 中的归档源文件，其摘要包含在 `artifact_hashes` 中。读取实际产物时使用记录的路径。运行记录还包含实际风格版本、规格摘要、程序版本、源码哈希、原生节点 ID、文件摘要及检查数据。

`drawn` 表示已绘制，`verified` 表示保存重开后的技术检查通过，`failed` 表示尝试失败。技术检查不证明视觉接近参考，也不代表用户确认；当前工具不代填 `user_confirmation`。

预检错误直接报告。候选制作失败时保留现场并尝试登记 `failed`，不替换最近成功记录；登记仍受 `expected_revision` 约束，发生冲突时仅保留运行目录中的证据。发布前发现项目版本或原源文件摘要变化时，拒绝发布并保留候选产物，原源文件不被覆盖。发布后若项目持久化失败，错误会明确说明源文件已发布并给出结果路径，不自动回滚或重试。

`.smartui/project.json` 是唯一结构化数据源，受管 Markdown 只展示摘要。手改、缺失或多文件写入中断造成摘要不一致时明确报错。状态写入使用项目排他锁、同目录临时文件和原子替换，不静默修复或吞掉错误。

## 对照与偏差反馈

`project_compare_component` 使用指定成功运行保留的实际 PNG 预览和已登记参考。两个区域均为 `[x, y, width, height]`，缺省时取整图。参考区域可按完整缩略图测量，传入该缩略图的 `reference_coordinate_size` 后映射到原图坐标。产物区域直接使用运行预览的像素坐标。双方分别裁切，再按各自的显式倍率等比缩放，默认均为 1。

项目在 `.smartui/comparisons/<comparison_id>/` 保存两份原始像素尺寸的 RGBA 裁切快照和 RGB 棋盘背景拼图。显示尺寸四舍五入到整数像素并记入对照。每份裁切快照和拼图均限制为每边最多 8192 像素、每张最多 3200 万像素。超限时选择更小区域，若仅拼图超限，也可降低显示倍率。

对照记录用 SHA-256 绑定参考、运行输入和保存证据，读取时校验快照。创建和读取都通过 MCP 返回拼图，不调用 PhotoCraft 或改写输入。处理过程不做 ICC 颜色转换，不输出自动质量分。

查看拼图后，用 `project_record_feedback` 分别记录可见现象 `observation` 和建议改动 `suggested_change`。可用 `target_nodes` 关联运行所用绘制计划的节点 key，用 `rule_ids` 关联运行所用风格快照的规则。初始状态为 `open`。

用 `project_update_feedback` 追加复查理由。`open` 表示继续跟进，`resolved` 表示偏差已修正，`dismissed` 表示决定结束跟进。复查对照必须与原对照使用同一组件、同一参考 ID、同一参考哈希及同一原图区域。标记 `resolved` 时，复查对照的成功运行序号必须晚于原对照的运行序号。

原始观察与建议保留，后续更新追加历史。新绘制可能使既有复查不再适用于当前运行，反馈状态和历史仍然保留。`source` 可声明 `model` 或 `user`，工具不验证身份，也不据此写入用户风格确认。

`project_context` 返回对照与反馈摘要，`components.md` 展示对应组件的对照和反馈。schema 3 的可选字段缺失时表示尚无记录。用以下字段区分历史结论与当前绘制结果。

- 对照的 `is_current` 表示对照是否对应当前项目版本与最新运行。它根据项目记录计算，磁盘文件校验在相关操作中执行。
- 反馈的 `reviewed_comparison_id` 标识当前反馈状态所依据的对照，`applies_to_current_run` 表示它是否适用于当前运行。
- 组件的 `open_feedback_count` 统计仍为 `open` 的反馈。

## 多产物视觉检查

用 `project_create_visual_check` 并排查看已实际绘制的样片。适用场景包括检查同一组件的配色和状态、多个尺寸的变体，以及两次绘制之间的差异。工具读取指定运行保存的 PNG，不连接 PhotoCraft。

| 参数 | 约束 |
| --- | --- |
| `items` | 按检查顺序提供 1 至 16 项。每项都要有 `component_id`、对应的成功 `run_id` 和非空 `label`。可以指定同一组件的不同运行，也可以指定不同组件。 |
| `items[].region` | 可选的整数数组 `[x, y, width, height]`，以对应运行的原始预览像素为坐标，省略时使用整张预览。 |
| `items[].scale` | 可选的有限正数，默认 1，最大 8192。按目标显示尺寸指定等比倍率，输出尺寸四舍五入到整数像素。 |
| `background` | 必填，使用 `#RRGGBB` 指定所有样片共用的纯色背景。 |
| `columns` | 列数，取 1 至 16，默认 3，实际列数最多等于样片数。 |
| `grayscale` | 是否另存灰度图，默认开启。 |

创建和读取通过 MCP 返回 JSON 元数据、彩色总览和已请求的灰度图。图上序号对应元数据中的样片顺序和标签，标签可用中文。每项记录包含运行 ID、实际风格和程序版本、组件规格、规格和源码摘要、原图坐标、缩放倍率、显示尺寸及图片摘要。源图、单项裁切、缩放图和总览均限制为每边最多 8192 像素、每张最多 3200 万像素。

工具用 SHA-256 校验来源预览并保存原像素尺寸的 RGBA 裁切快照，采用 Lanczos 等比缩放后合成到纯色背景。灰度图由合成后的彩色总览通过 Pillow 转换为 L 模式，使用 RGB 加权亮度。处理过程不做 ICC 转换。灰度可帮助判断明暗层次，视觉检查由人或模型完成，工具不计算审美分或认证对比度。

快照与检查图保存在 `.smartui/visual-checks/<check_id>/`。用 `project_context` 查看摘要，用 `project_get_visual_check` 按 ID 校验并恢复历史检查。历史图像和来源保持固定。每项的 `is_current` 表示该运行仍是组件当前成功运行且无需复查，整组在所有项目都满足时才为 true。检查图没有页面布局或任意背景图输入。需要记录偏差时，按对应组件和运行建立参考对照，再用现有反馈工具关联节点或规则。

## 正式 PNG 导出

`project_export_assets` 读取明确的组件成功 `run_id`，校验计划、风格快照和预览摘要，再按声明区域生成生产 PNG。当前支持整图或矩形区域导出；不能从已合成的预览中自动去除文字，源程序应提供无示例文案的导出区域。工具不执行 PhotoCraft 绘制、不按图层组重新投影，也不修改 Unity `.meta` 或导入设置。

每项导出必须完整声明以下字段；`exports` 包含 1 至 64 项，资产 ID 和输出路径在批次内唯一：

```json
{
  "asset_id": "primary-button",
  "name": "主按钮背景",
  "output_path": "Assets/UI/primary-button.png",
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

`region` 相对来源预览，整图导出也显式提供；`geometry` 相对裁切后的 PNG，约束见[内容区域与伸缩几何](asset-geometry.md)。`output_path` 是工程内 `.png` 相对路径，不能占用 `.smartui`、项目受管文档、参考、程序或源文件目录。同一正式资产保持生产路径；已有未受管目标、不同资产占用的路径、受管输出缺失或摘要不符均报错，不覆盖修复。

正式导出使用已有 `images` 资产身份和不可变版本，来源类型 `component_export` 由导出工具生成，不能通过普通图像登记伪造。版本记录包含组件与运行、来源区域及摘要、状态、用途、几何和实际 PNG 编码设置。导出不改变 `selected_version`；组合可直接引用返回版本，需要改变项目选用版本时再调用 `project_select_image`。

重复导出相同来源和声明会复用最新资产版本及原文件字节；来源、几何或声明改变会保留新版本，即使像素相同。实际 PNG 字节相同时不重写生产文件。整批输入先完成校验再发布，磁盘发布失败会报告错误及恢复结果；项目记录写入失败时保留已发布文件和清单并明确指出，不盲目重试。

每次导出返回 `export.id`、项目 `revision`、每项版本与复用情况，以及 `.smartui/exports/<id>/manifest.json` 的路径和摘要。清单中的文件路径相对工程，`project_get_export` 校验清单和归档资产后读取历史结果；生产路径后来更新不改变旧清单。保留 `.smartui/exports/`，它不是可丢弃缓存。项目上下文包含导出摘要，可从运行找到正式资产，也可从资产记录追溯来源。

## 可更新的页面布局

`project_save_layout` 使用稳定 `layout_id` 保存完整定义，变化时产生连续修订；相同名称和定义重复保存不增加修订。`project_get_layout` 返回 `layout.definition`、`layout.version`、摘要和当前修订号，指定 `version` 可读取历史定义。位置、文案变化只产生布局修订，不生成新图像版本，也不改变选用状态。保存校验定义和输入；浏览器排版及物理溢出在构建时检查。

画布宽高为正整数，背景必填 `#RRGGBB`，节点为按叠放顺序排列的 1 至 512 项。节点 ID 在页面内唯一；坐标单位为参考画布像素，左上为原点。所有节点必填 `id`、`kind`、`x`、`y`、`width`、`height`，位置非负整数、尺寸正整数，布局主体必须位于画布内。图像主体之外的光晕由几何决定，页面渲染时按画布裁切。

图像节点必填 `asset_id` 和 `binding`，例如：

```json
{"id":"confirm","kind":"image","asset_id":"primary-button","binding":{"kind":"fixed","version":1},"x":32,"y":100,"width":240,"height":48}
```

`binding` 必须为 `{"kind":"fixed","version":正整数}` 或 `{"kind":"selected"}`。固定绑定始终解析指定版本；选用绑定解析项目当前 `selected_version`，没有选用版时报错。保存时校验引用及目标伸缩尺寸，但不把选用绑定改成固定绑定。正式导出使用该版本的 `geometry`；没有几何声明的独立导入或生成图像明确按完整原图等比居中，不推断九宫格。

文字节点额外必填 `text`、`font_path`、`font_size`、`line_height`、`align`、`wrap`、`overflow`、`color`。`font_path` 是真实 TTF/OTF 文件绝对路径；字号和行高为正整数像素，`align` 为 `left/center/right`，`wrap` 为布尔值，`overflow` 为显式的 `error/clip`。文字允许换行，不允许其他控制字符。`stroke_width` 可省略，表示无描边；`stroke_color` 可省略，表示黑色。文本框的物理溢出将在浏览器构建时校验。

保存会校验字体和所需字符，并写入 `font_sha256`。读取定义、改位置后可保留该字段再次保存；字体内容改变时旧摘要会阻止误用，核对新字体后移除旧摘要再保存以绑定新内容。字体缺失、缺字、非法参数或来源摘要不符直接报错。工程内字体路径相对工程持久化，返回时恢复为绝对路径；工程外字体仍需在本机存在。

`layouts` 是 schema 3 的可选状态字段，缺省表示尚无布局；历史修订保留定义和字体依据。布局尚不包含父子分组、自动布局、业务交互或 Unity 对象绑定。

### 浏览器构建与历史快照

`project_build_layout(project_root, expected_revision, layout_id, version=None)` 构建指定修订；省略 `version` 使用当前修订。每次调用创建新构建，不覆盖旧结果。返回 JSON 和实际 PNG，JSON 包含 `page_build.id`、布局修订及摘要、解析后的图像版本和几何、`files`、`renderer` 与新 `revision`。

`files.html.path` 是可离线打开的 `preview.html`，支持查看和缩放；`files.overview.path` 是同一 HTML 在 Chromium 中的 PNG 截图，采用参考画布尺寸及设备像素比 1。图像和字体内嵌在 HTML 中，无网络请求；图片切片由共用几何生成，分别裁片后缩放，防止相邻切片串色。文字通过浏览器独立排版，等待字体与图片加载完成后截图，不另用 Pillow 排字。`overflow: error` 在超出文本框时中止构建；`clip` 明确裁切并在测量证据中标记溢出，不自动缩小字号。浏览器版本、用户代理、实际像素比、字体摘要和节点测量保存在 `renderer`。

构建保存在 `.smartui/page-builds/<build_id>/`：HTML、PNG、解析后的 `inputs.json` 及图像/字体文件都有 SHA-256。相同资源在文件快照中复用。`inputs.json` 仅使用同目录资源名，项目状态使用工程相对路径，项目整体移动后可以恢复读取。独立 HTML 可以单独复制查看；不同系统或浏览器的重绘可能存在字体栅格化差异，已保存 PNG 才是该次 Chromium 的实际输出。

`project_get_page_build(project_root, build_id)` 校验全部快照并返回 JSON 和 PNG；原图和原字体被删除不影响历史读取。资产或字体在构建前缺失、改变或无法加载会明确失败，失败不会保存成功构建。首次使用需按 README 安装 Chromium。快照是制作证据，不能当成临时缓存删除。

读取结果把两种状态分别报告，均不代表视觉或用户确认：

- `matches_layout`：构建使用的布局修订仍是当前修订。布局位置、文字或名称更新后为 false。
- `matches_bindings`：此构建的所有选用绑定仍对应当前 `selected_version`；固定绑定不受选用变化影响。修改选用不会改写快照，需显式重建。

`page_builds` 是 schema 3 的可选状态字段；`project_context` 返回布局和构建摘要。浏览器预览没有拖拽编辑、自动刷新或自动重建，不代表 Unity 的运行态渲染结果。

## 独立图像资产与静态组合预览

SmartUI 不内置模型生图或图像编辑后端。先用宿主可用的图像工具制作并查看实际结果，再将需要纳入项目的 PNG、JPEG 或 WebP 文件复制到业务工程中，登记时由工具按原字节归档到受管资产目录。`project_add_reference` 的参考登记语义不同，不能代替生产图像登记。

### 图像登记、来源与选用

`project_register_image` 的完整必需参数为 `project_root: str, expected_revision: str, asset_id: str, name: str, source_path: str, provenance: dict`，没有可省略参数。`source_path` 必须是实际图像的绝对路径。支持 PNG、JPEG、WebP；版本由工具按资产递增分配，从 1 开始，不能覆盖已有版本。文件按原始字节保存到 `paths.sources/images/<asset_id>/<version>/image.<png|jpg|webp>`，并记录尺寸、模式、透明度、SHA-256 和来源。

`provenance` 是 JSON 对象，`kind` 必须为 `generated`、`edited` 或 `imported`。`generated` 和 `edited` 必须包含非空字符串 `tool` 与 `prompt`；`imported` 必须包含非空字符串 `description`。实际可得的模型、种子、参考等额外资料可作为 JSON 字段保留；未知信息不伪造，已有图像不补造提示词。注册只添加候选版本，新资产的 `selected_version` 初始为 `null`。

`project_select_image(project_root, expected_revision, asset_id, version)` 校验图像文件和摘要后设置选用版本，不重建组合，也不代表用户美术确认。`project_get_image(project_root, asset_id, version)` 校验并返回实际图像、来源、摘要及当前选用版本。状态中的 `images` 为可选 schema 3 字段，缺省表示没有登记资产；存在时包含资产 ID、名称、`selected_version` 和按顺序连续排列的 `versions` 记录。

### 静态组合预览

`project_create_composition` 的完整必需顶层参数为 `project_root: str, expected_revision: str, width: int, height: int, background: str, nodes: list[dict]`，均无默认值。画布尺寸为正整数，背景必填 `#RRGGBB`。`nodes` 必须含 1 至 64 个节点，`id` 唯一，按列表顺序绘制；坐标和目标区域使用画布像素，必须位于画布内。节点类型如下：

| `kind` | 必需字段（不含共有的 `id`, `kind`, `x`, `y`） | 可选字段及默认值 |
| --- | --- | --- |
| `component` | `component_id: str, run_id: str, width: int, height: int` | `region: list[int] \| null = null` |
| `image` | `asset_id: str, version: int, width: int, height: int` | `region: list[int] \| null = null` |
| `text` | `text: str, font_path: str, font_size: int, color: str` | `stroke_width: int = 0`, `stroke_color: str = "#000000"` |

组件须引用该组件的一次成功绘制记录。图像可引用任意已登记版本，是否当前选用不改变其固定来源。组件与图像的目标 `width`、`height` 定义摆放矩形；图像按原比例等比缩放并居中，不拉伸、不额外裁切。`region` 是可选的原图像素裁切 `[x, y, width, height]`。预览使用纯色背景和 alpha 合成，不转换 ICC 或 gamma。文字是单行，`font_path` 必须指向实际 TTF/OTF 文件且字体必须包含每个所用字符；越界或缺字会报错。字体文件会随组合保存快照。

组合文件保存在 `.smartui/compositions/<composition_id>/`，包含 overview PNG、组件/图像源裁切和字体快照；项目状态中的可选 `compositions` 保存节点依据、尺寸与摘要。`project_get_composition(project_root, composition_id)` 校验快照后读取历史组合；即使源资产之后更新，历史图仍使用保存时快照。`is_current` 只表示组合中的图像版本仍为当前选用版本，或组件运行仍匹配当前成功运行；文字节点不参与判定。它不表示预览经过用户确认。新版本选用后旧组合不会自动重建。

该工具是固定坐标预览，不提供自动布局、九宫格、自动换行、运行时交互或将位图导入 PhotoCraft 文档。它也不生成图像。关于实际生成、查看、复制、登记、局部替换与组合检查的操作顺序，见[ICON 资产 Skill](../skills/icon-assets/SKILL.md)。
