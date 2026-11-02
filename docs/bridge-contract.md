# Krita 本地绘制接口与首版验收

首版验证原生笔刷、图层、透明度蒙版、批量操作、可编辑 `.kra` 保存，以及重新打开原文件后的局部修改。暂不实现风格识别、Unity 导入和平台账户功能。

## 进程通信

Krita Python 扩展在 GUI 主线程通过 QTimer 顺序处理本地 JSON 请求。MCP 服务通过文件队列连接扩展。默认目录为 `%LOCALAPPDATA%/SmartUI/bridge`，可通过 `SMARTUI_BRIDGE_DIR` 指定。

- 请求：`requests/<id>.json`，先写临时文件再原子重命名。内容为 `{ "id": "UUID", "method": "...", "params": {} }`。
- 结果：`responses/<id>.json`，同样原子写入。成功为 `{ "id": "UUID", "ok": true, "result": ... }`。
- 失败为 `{ "id": "UUID", "ok": false, "error": { "type": "...", "message": "...", "operation_index": 0, "completed": [] } }`。后两项只用于批量执行错误。
- 扩展处理前将请求移入 `processing`，完成后移入 `archive`。客户端超时不自动重试，避免重复修改；错误必须给出请求 ID 和结果路径。
- `status.json` 是扩展启动信息；实际可用性通过 `status` 请求验证。
- 批量执行不是事务：遇到错误立即停止，返回已经完成的操作。失败的那一项也可能部分生效，应检查实际文档后再继续，不得声称自动回滚。

## 方法

| 方法 | 参数 | 结果 |
| --- | --- | --- |
| `status` | `{}` | Krita 版本、协议版本和支持的操作 |
| `brushes` | `{ "query": "可选名称片段" }` | 按名称排序的原生笔刷预设名称 |
| `inspect` | `{ "document": "绝对 .kra 路径" }` | 文档和递归图层树；节点含 UUID、名称、类型、属性和像素 SHA256 |
| `execute` | `{ "operations": [...] }` | `{ "completed": [...], "elapsed_ms": ... }` |
| `preview` | `{ "document": "绝对 .kra 路径" }` | `{ "path": "导出 PNG 的绝对路径" }` |

文档以绝对 `.kra` 路径标识，节点以 Krita 原生 UUID 标识，或用 `name:唯一名称` 引用。父节点可以用 `root`。同名不明确、资源缺失或参数不合法必须报错。

## 批量操作

每个操作包含 `op`，除创建和打开文档外，文档参数统一叫 `document`。

- `document.create`: `path`, `width`, `height`, `name`。仅允许新路径，初始透明 RGBA/U8/sRGB 文档，不保留 Krita 自动建立的白色背景。
- `document.open`: `path`。读取现有源文件。
- `document.save`: `document`。保存原路径，返回文件路径。
- `document.close`: `document`。仅关闭没有未保存修改的文档。
- `layer.create`: `document`, `name`, `kind` (`paintlayer` / `grouplayer`), `parent` (默认 `root`)。
- `layer.update`: `document`, `node`, `properties`，允许 `name`, `visible`, `locked`, `opacity` (0–255), `blending_mode` (`normal` / `multiply` / `screen` / `overlay`), `inherit_alpha`, `alpha_locked`（只适用于绘画图层）。
- `layer.clear`: `document`, `node`。明确清空指定绘画图层，保留节点和蒙版。
- `mask.create`: `document`, `parent`, `name`, `rects`。建立原生透明度蒙版；`rects` 每项为 `[x,y,w,h,alpha]`，未列区域透明。
- `mask.update`: `document`, `node`, `rects`。替换指定透明度蒙版选区。
- `paint`: `document`, `node`, `brush`, `color` (`#RRGGBB`), `size`, `opacity` (0–1，默认 1), `strokes`。笔刷必须精确匹配已安装预设。

`strokes` 每项包含 `kind`：

- `line`: `from` / `to` 为整数坐标 `[x,y]`（已安装 Krita 5.3 的绑定使用 `QPoint`），可选 `pressure_from` / `pressure_to` (0–1，默认 1)。小数坐标使用 `path`。
- `path`: `commands` 为 `[["M",x,y],["L",x,y],["C",c1x,c1y,c2x,c2y,x,y],["Q",cx,cy,x,y],["Z"]]`，`fill` 为 `none` / `foreground`，默认 `none`。
- `rectangle` / `ellipse`: `rect` 为 `[x,y,w,h]`，`fill` 同上。

描边一律使用 Krita 原生笔刷。几何填色和蒙版选区属于 Krita 原生图形/选区操作，不假装是手绘笔触。

绘制临时关闭全局 Alpha 锁定并启用压力，结束后恢复原画笔设置；图层自身的 Alpha 锁定仍按图层属性生效。文档已有选区或界面启用水平/垂直镜像绘制时，接口明确报错，避免未声明的裁切或额外笔触。首版需在 Krita 中取消这些状态后再绘制。

子图层 UUID 在保存和重新打开后保持，根节点 UUID 属于当前文档实例，不作为持久化标识。`pixel_sha256` 覆盖画布范围内的节点原始像素，不是整份 KRA 的文件哈希。

## 验收证据

1. 在真实 Krita 进程使用两种原生预设画相同路径，记录预设和像素差异。
2. 单次调用完成多条笔触，记录执行耗时。
3. 建立分组、绘画图层和透明度蒙版；导出预览并检查实际遮罩效果。
4. 保存、关闭、重新打开 `.kra`，确认图层、蒙版和 UUID 保留。
5. 在同一源文件中修改指定高光图层或蒙版，对比修改前后像素哈希，确认其他绘画图层未被改动。
6. 通过 MCP 客户端握手和工具调用验证完整入口，而不只测试内部函数。
