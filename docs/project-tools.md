# 项目工具与执行记录

SmartUI Krita v0.6.0 提供十六个项目工具，与五个底层 `krita_*` 工具共存。项目管理参考、风格规则、Python 绘制程序、组件规格、运行记录、参考对照、偏差反馈和多产物视觉检查。具体画法属于项目程序。

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
| `project_plan_component` | `project_root: str, component_id: str` | 执行程序并预检计划，不调用 Krita、不登记绘制成功 |
| `project_draw_component` | `project_root: str, expected_revision: str, component_id: str, layers: list[str] \| null = null` | 执行、保存、重开检查并记录；省略 layers 完整绘制 |
| `project_compare_component` | `project_root: str, expected_revision: str, component_id: str, run_id: str, reference_id: str, reference_region: list[int] \| null = null, reference_coordinate_size: list[int] \| null = null, artifact_region: list[int] \| null = null, reference_scale: float = 1.0, artifact_scale: float = 1.0` | 保存参考与运行预览的对照，返回 JSON 和拼图 |
| `project_get_comparison` | `project_root: str, comparison_id: str` | 校验并读取对照快照、拼图与反馈详情 |
| `project_create_visual_check` | `project_root: str, expected_revision: str, items: list[dict], background: str, columns: int = 3, grayscale: bool = true` | 保存多份运行预览的彩色总览与可选灰度图 |
| `project_get_visual_check` | `project_root: str, check_id: str` | 校验并读取已保存的视觉检查图与来源记录 |
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
| `sources` | `art/ui/sources` | `<component_id>.kra` |

目录映射不改变 `project_root` 的含义。参考输入在工程外时，登记操作将其复制到 `<references>/<reference_id>/<原文件名>`，后续读取和摘要检查针对该副本。工程内输入可保留其原有位置。

项目状态 JSON 中的工程内文件引用保存为工程相对 POSIX 路径，即用 `/` 分隔；明确的工程外路径可以使用绝对路径。运行时 API 返回可直接读取的绝对路径，程序元数据的 `source_path` 除外，它保持工程相对路径。生成文档的资源链接相对于 `documents` 目录，移动整个工程后仍可使用。历史 `operations.json`、`result.json` 等原始执行证据保留当时调用的路径和字节，继续制作使用项目状态中解析后的文件引用。

创建项目时，工具在 `.smartui/.gitignore` 中写入缓存、锁、临时文件及 `migration-backups/` 的忽略规则，并保留已有规则。状态、运行、对照及视觉检查记录应保留，不要整体忽略 `.smartui/`。旧项目必须先显式迁移，正常读取不查找旧位置，也不自动升级 schema：`uv run --locked python scripts/migrate_project_layout.py --source-root <旧数据目录绝对路径> --project-root <业务工程绝对路径>` 默认只展示计划，增加 `--apply` 才执行迁移。迁移器先验证完整备份，再创建新布局并核对摘要；旧目录保持原样，确认新入口可用后再归档旧目录。

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

`layers=["surface"]` 中的值是计划节点 key，不是显示名称或固定角色。可选择绘画图层、分组和透明度蒙版；选择分组只更新该分组自身属性，不隐式包含子树；需要修改子节点时，逐一列出它们的 key。工具比较新计划与上次实际计划，拒绝非目标节点及绘制内容的变化，因此任何参数都可能用于局部调整，前提是它只影响选中范围。

已有源文件通过记录的 UUID 定位。无论完整重画还是局部更新，当前画布和图层结构都必须匹配既有记录；改变尺寸或结构时暂用新组件 ID 创建变体。源文件被外部改动、Krita 存在未保存修改或 UUID 不符时直接报错。完整重画不能用来绕过结构检查。没有旧 plan 的历史资产，需要先完整绘制一次才能局部更新。

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
| `feedback` | 可选，记录对照反馈、当前状态与复查历史 |
| `document_hashes` | 自动生成 style.md 和 components.md 的摘要 |

程序源码默认路径为工程相对路径 `art/ui/programs/<id>/<version>.py`。读取时核对实际源码与记录摘要；源码变化通过工具保存为版本，不手改文件绕过记录。程序更新使依赖组件的 `needs_review` 为 true；历史 run 仍记实际程序版本与源码哈希，不把旧产物改成新版本。

规则或共用参数变化提升风格版本；只改风格说明或引用清单不升级。`needs_review` 是派生状态：缺少成功产物、当前规格/风格/程序与实际记录不同，或最近尝试失败，都需要重新检查。

源文件默认位于 `art/ui/sources/<component_id>.kra`。每次运行在 `.smartui/runs/<run_id>/` 保存 `program.py`、`plan.json`、`style-snapshot.json`、`operations.json`、`result.json`，成功时另有 `preview.png` 与 `output.kra`；更新已有文件时保留 `input.kra`。运行记录包含实际风格版本、规格摘要、程序版本、源码哈希、节点 UUID、文件摘要及检查数据。

`drawn` 表示已绘制，`verified` 表示保存重开后的技术检查通过，`failed` 表示尝试失败。技术检查不证明视觉接近参考，也不代表用户确认；当前工具不代填 `user_confirmation`。

预检错误直接报告。进入执行阶段后的失败追加实际错误并保留现场，不替换最近成功记录；历史成功不能证明失败后的磁盘文件有效。资产生成后若项目 revision 冲突，会报告记录未更新并给出结果路径，不自动重试。

`.smartui/project.json` 是唯一结构化数据源，受管 Markdown 只展示摘要。手改、缺失或多文件写入中断造成摘要不一致时明确报错。状态写入使用项目排他锁、同目录临时文件和原子替换，不静默修复或吞掉错误。

## 对照与偏差反馈

`project_compare_component` 使用指定成功运行保留的实际 PNG 预览和已登记参考。两个区域均为 `[x, y, width, height]`，缺省时取整图。参考区域可按完整缩略图测量，传入该缩略图的 `reference_coordinate_size` 后映射到原图坐标。产物区域直接使用运行预览的像素坐标。双方分别裁切，再按各自的显式倍率等比缩放，默认均为 1。

项目在 `.smartui/comparisons/<comparison_id>/` 保存两份原始像素尺寸的 RGBA 裁切快照和 RGB 棋盘背景拼图。显示尺寸四舍五入到整数像素并记入对照。每份裁切快照和拼图均限制为每边最多 8192 像素、每张最多 3200 万像素。超限时选择更小区域，若仅拼图超限，也可降低显示倍率。

对照记录用 SHA-256 绑定参考、运行输入和保存证据，读取时校验快照。创建和读取都通过 MCP 返回拼图，不调用 Krita 或改写输入。处理过程不做 ICC 颜色转换，不输出自动质量分。

查看拼图后，用 `project_record_feedback` 分别记录可见现象 `observation` 和建议改动 `suggested_change`。可用 `target_nodes` 关联运行所用绘制计划的节点 key，用 `rule_ids` 关联运行所用风格快照的规则。初始状态为 `open`。

用 `project_update_feedback` 追加复查理由。`open` 表示继续跟进，`resolved` 表示偏差已修正，`dismissed` 表示决定结束跟进。复查对照必须与原对照使用同一组件、同一参考 ID、同一参考哈希及同一原图区域。标记 `resolved` 时，复查对照的成功运行序号必须晚于原对照的运行序号。

原始观察与建议保留，后续更新追加历史。新绘制可能使既有复查不再适用于当前运行，反馈状态和历史仍然保留。`source` 可声明 `model` 或 `user`，工具不验证身份，也不据此写入用户风格确认。

`project_context` 返回对照与反馈摘要，`components.md` 展示对应组件的对照和反馈。schema 3 的可选字段缺失时表示尚无记录。用以下字段区分历史结论与当前绘制结果。

- 对照的 `is_current` 表示对照是否对应当前项目版本与最新运行。它根据项目记录计算，磁盘文件校验在相关操作中执行。
- 反馈的 `reviewed_comparison_id` 标识当前反馈状态所依据的对照，`applies_to_current_run` 表示它是否适用于当前运行。
- 组件的 `open_feedback_count` 统计仍为 `open` 的反馈。

## 多产物视觉检查

用 `project_create_visual_check` 并排查看已实际绘制的样片。适用场景包括检查同一组件的配色和状态、多个尺寸的变体，以及两次绘制之间的差异。工具读取指定运行保存的 PNG，不连接 Krita。

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
