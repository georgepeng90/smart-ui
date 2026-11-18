# PhotoCraft 制作接口与证据

SmartUI 从安装目录或 PATH 查找 PhotoCraft CLI，也可用 `SMARTUI_PHOTOCRAFT_CLI` 指定可执行文件的绝对路径。查询 `photocraft_status` 可查看实际路径、`--version` 的完整输出和可执行文件的 SHA-256。执行前会检查实际使用的 CLI 是否提供该操作所需的接口与命令。

通过无窗口服务协议，SmartUI 先启动一个限制文件读写范围的 JSONL 会话，再查询 `methods`。组件制作还查询 `engine.commands`，核对创建、编辑和检查所用的文档方法、图层、形状、笔画、效果及蒙版命令。预览仅需要 `doc.open` 和 `doc.render`。查询不创建或修改文档，缺项会按 `method:<名称>` 或 `command:<名称>` 列出，并阻止相关操作。执行时由 PhotoCraft 根据当前文档状态和参数处理命令，SmartUI 在保存重开后核对产物。应用缺失、能力查询失败或操作失败时会明确报错。 [PhotoCraft 的无窗口服务协议](https://github.com/storytold/photocraft/blob/v0.23.1/docs/control-protocol.md#headless-server)

直接操作 PhotoCraft 时，社区 Skill 给出了连接桌面文档、处理磁盘文件和查询当前版本可用命令的示例。 [mroxso 的 PhotoCraft Skill](https://github.com/mroxso/artcraft-skills/blob/main/artcraft/photocraft/SKILL.md)、[AHL8192 的 PhotoCraft Skill](https://github.com/AHL8192/opencode-craft-skills/blob/main/photocraft/SKILL.md)

## MCP 工具

| 工具 | 参数与结果 |
| --- | --- |
| `photocraft_status` | 无参数。在独立的临时会话中返回 `cli_path`、`version`、`sha256` 和 `missing_capabilities`。缺失能力列表为空表示该 CLI 的清单包含组件制作所需的全部接口和命令 |
| `photocraft_inspect_document` | `document` 为绝对 `.pcraft` 路径。默认 `view="full"` 返回完整原生属性和摘要。`view="editable"` 返回画布尺寸及各节点的 ID、名称、类型、父节点、按保存顺序排列的效果类型和效果总开关。形状还返回 `fill`、`stroke` 的画法，如 `Solid`、`Gradient` 或 `null` |
| `photocraft_preview` | `document` 为绝对 `.pcraft` 路径；返回原尺寸 PNG |

组件制作使用项目工具执行，不对外暴露任意原生命令批处理。原生节点 ID 为整数，项目计划使用稳定字符串 key，两者分别记录。

## 执行与失败

项目绘制工具在调用 PhotoCraft 前按已登记的 `spec.design` 核对计划画布。发布时还检查保存及重开后的源文件尺寸和实际 PNG 尺寸。多图素源稿逐项核对画布能否容纳指定裁区，正式导出再核对各区及其内部的内容框。[组件的设计尺寸与制作区域](project-tools.md#组件的设计尺寸与制作区域)

项目绘制工具在发布前自动核对候选源文件的原生图层。它逐个比对制作计划声明的形状填充和路径描边是否存在、各自为实色还是渐变，以及效果的类型、顺序和非空效果列表的总开关。没有描边或效果的形状也是有效的。

程序计划预检不生成成功资产。绘制调用返回本机 CLI 路径。项目的制作记录保留 CLI 版本、可执行文件 SHA-256 和能力检查结果，以及输入摘要、计划、源文件、PNG 与产物检查结果。保存并重开输出后检查节点与像素，局部修改还检查未选内容。制作请求、响应和候选文件用于本机诊断，存放在 `.smartui/`。失败的写入请求不会自动重放。

候选文件先在本机临时区制作。预检失败直接返回错误。发布前核对项目 revision 和当前源文件摘要。发布中断时恢复已替换的文件，恢复失败会列出受影响路径。

候选制作或发布失败时，先查看本机诊断和错误信息，再用当前记录的摘要核对源文件。用户美术确认和运行技术检查分别记录。
