# PhotoCraft 制作接口与证据

SmartUI PhotoCraft v0.11.0 使用正常安装的 PhotoCraft v0.20.1 CLI，要求 `--version` 的完整输出为 `photocraft-cli 0.20.1 (327c4cc82, 2026-11-03)`。版本号相同但构建不同也会拒绝执行。通过 `SMARTUI_PHOTOCRAFT_CLI` 指定绝对路径，或使用正常安装位置（例如 `%LOCALAPPDATA%/Programs/ArtCraft/PhotoCraft/photocraft-cli.exe`）和 PATH。执行前查询真实路径、完整版本与文件摘要；应用缺失、版本错误或操作失败明确报错，不自动下载或改用其他制作后端。

## MCP 工具

| 工具 | 参数与结果 |
| --- | --- |
| `photocraft_status` | 无参数；查询真实 CLI 路径、版本及摘要 |
| `photocraft_inspect_document` | `document` 为绝对 `.pcraft` 路径；返回文档及原生节点信息 |
| `photocraft_preview` | `document` 为绝对 `.pcraft` 路径；返回原尺寸 PNG |

组件制作使用项目工具执行，不对外暴露任意原生命令批处理。原生节点 ID 为整数，项目计划使用稳定字符串 key，两者分别记录。

## 执行与失败

程序计划预检不生成成功资产。实际制作保存当前产物的后端构建、输入摘要、计划、源文件、PNG 与检查结果。保存并重开输出后检查节点与像素，局部修改还检查未选内容。原生请求、响应和候选文件用于本机诊断，存放在 `.smartui/`。

候选文件先在本机临时区制作。预检失败直接返回错误。发布前核对项目 revision 和当前源文件摘要。发布中断时恢复已替换的文件，恢复失败会列出受影响路径。

候选制作或发布失败时，先查看本机诊断和错误信息，再用当前记录的摘要核对源文件。用户美术确认和运行技术检查分别记录。
