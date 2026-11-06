# PhotoCraft 制作接口与证据

SmartUI 使用正常安装的 PhotoCraft CLI。服务查找正常安装位置（例如 `%LOCALAPPDATA%/Programs/ArtCraft/PhotoCraft/photocraft-cli.exe`）和 PATH，也可用 `SMARTUI_PHOTOCRAFT_CLI` 指定 CLI 的绝对路径。SmartUI 记录实际路径、`--version` 的完整输出和可执行文件的 SHA-256。版本和摘要均匹配已验证的 v0.20.1 构建 `327c4cc82` 时，`build_verification` 为 `verified`；其他构建为 `unverified`，必需能力检查通过即可执行。

通过 [PhotoCraft 的无窗口服务协议](https://github.com/storytold/photocraft/blob/v0.20.1/docs/control-protocol.md#headless-server)，SmartUI 先启动一个限制文件读写范围的 JSONL 会话，再查询 `methods`。组件制作还查询 `engine.commands`，核对创建、编辑和检查所用的文档方法、图层、形状、笔画、效果及蒙版命令。预览仅需要 `doc.open` 和 `doc.render`。查询不创建或修改文档，缺项会按 `method:<名称>` 或 `command:<名称>` 列出，并阻止相关操作。清单检查只核对接口是否可用；参数及产物是否正确仍由实际操作及保存重开检查验证。应用缺失、能力查询失败或操作失败时会明确报错。

## MCP 工具

| 工具 | 参数与结果 |
| --- | --- |
| `photocraft_status` | 无参数；在独立的临时会话中返回 `cli_path`、`version`、`sha256`、`build_verification` 和 `missing_capabilities`；缺失能力列表为空表示组件制作依赖的接口均已发现 |
| `photocraft_inspect_document` | `document` 为绝对 `.pcraft` 路径；返回文档及原生节点信息 |
| `photocraft_preview` | `document` 为绝对 `.pcraft` 路径；返回原尺寸 PNG |

组件制作使用项目工具执行，不对外暴露任意原生命令批处理。原生节点 ID 为整数，项目计划使用稳定字符串 key，两者分别记录。

## 执行与失败

程序计划预检不生成成功资产。实际制作保存当前产物的后端版本、摘要、构建验证状态、输入摘要、计划、源文件、PNG 与检查结果。保存并重开输出后检查节点与像素，局部修改还检查未选内容。制作请求、响应和候选文件用于本机诊断，存放在 `.smartui/`。失败的写入请求不会自动重放。

候选文件先在本机临时区制作。预检失败直接返回错误。发布前核对项目 revision 和当前源文件摘要。发布中断时恢复已替换的文件，恢复失败会列出受影响路径。

候选制作或发布失败时，先查看本机诊断和错误信息，再用当前记录的摘要核对源文件。用户美术确认和运行技术检查分别记录。
