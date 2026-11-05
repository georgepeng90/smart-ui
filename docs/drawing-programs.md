# 项目 Python 绘制程序约定

项目程序将风格参数和组件规格转换为 PhotoCraft 原生制作计划。共用几何、颜色用途、状态和材质由项目定义；工具不内置业务组件画法。

## 程序入口与执行

完整 Python 源码必须定义 `build(parameters, spec, component_id)`。`parameters` 来自当前风格，`spec` 为完整组件规格，`component_id` 为稳定标识。用 `project_save_program` 保存源码，通过 `spec.program_id` 引用；保存只检查语法，`project_plan_component` 和 `project_draw_component` 都执行程序并严格预检计划。单独查看或诊断计划时使用前者，已明确的参数调整可直接调用绘制入口。缺少必需配置直接报告具体字段。

程序在当前解释器的独立进程中运行，最长 60 秒，打印不会混入计划结果。这是可信本机代码，可访问文件及网络，不构成安全沙箱。返回值只能包含标准 JSON 类型和有限数值。

## 最小程序示例

```python
def build(parameters, spec, component_id):
    return {
        "schema_version": 1, "width": spec["width"], "height": spec["height"],
        "nodes": [{
            "key": "surface", "name": component_id + "/底面",
            "kind": "shape", "parent": "root",
            "shape": {"kind": "rect", "rect": [0, 0, spec["width"], spec["height"]],
                      "fill": spec["color"]},
        }],
    }
```

组件规格例如 `{"program_id":"flat-panel","width":320,"height":120,"color":"#6AAD3D"}`。示例直接索引必需字段，缺失即失败。

## 计划与节点

顶层字段为 `schema_version:1`、整数 `width/height` 和 `nodes`。节点从底向上排列，父分组先声明。每个节点必须有唯一 `key`、`name`、`kind` 和 `parent`；`parent` 为 `root` 或此前声明的 group key。原生整数 ID 由执行结果记录，不能将它当作项目 key。

| 节点类型 | 制作内容 |
| --- | --- |
| `shape` | `shape` 对象使用原生形状参数：`rect`、`roundedRect`、`ellipse`、`polygon`、`star`、`line`、`path` |
| `pixel` | `strokes` 数组描述原生像素笔触 |
| `group` | 管理父子结构；不隐式将局部选择扩展为全部子节点 |

`properties` 可表达 `visible`、0–1 的 `opacity` 和 `blend`。分组默认使用 `PassThrough`（穿透），其他节点默认使用 `Normal`（正常）。`effects` 为 `kind/params` 列表。像素和分组可使用矢量路径 `mask`；形状不能单独设置 `mask`，需要额外裁剪时放入带蒙版的父分组。程序预检严格拒绝未知或非法字段，不静默近似复杂参数。

形状实色填充使用颜色字符串；描边例如 `{"width":3,"color":"#14294A","align":"inside"}`。圆角矩形示例为 `{"kind":"roundedRect","rect":[8,8,184,84],"radii":12,"fill":"#247CDA"}`。渐变填充例如：

```json
{"gradient":{"stops":[[0,"#6FC6FF"],[1,"#247CDA"]],"angle":90,"scale":100,"style":"linear","reverse":false,"dither":false}}
```

复杂节点的参数约束以当前计划校验器为准。只使用已预检且能由当前原生后端实际执行的参数，不将应用本身的全部能力宣称为计划接口能力。

### 形状几何

| kind | 几何字段 |
| --- | --- |
| `rect/ellipse` | `rect:[x,y,width,height]` |
| `roundedRect` | `rect`；`radii` 为单值或四项数组，缺省 10 |
| `polygon` | `rect`；整数 `sides`（3–100，缺省 5）、`cornerRadius`（缺省 0） |
| `star` | 同 polygon，另有 `starRatio`（0.001–1，缺省 0.5） |
| `line` | `from/to:[x,y]`；`weight`、`arrowStart/arrowEnd`、`arrowWidth/arrowLength/arrowConcavity` |
| `path` | `path` 对象，见矢量路径说明 |

形状尺寸必须为正；坐标范围为 −300000 至 300000。填充缺省黑色，`null` 禁用填充；描边缺省 `null`。描边还可指定 `opacity`（0–100）、`cap`（butt/round/square）、`join`（miter/round/bevel）、`miterLimit`、`dashes` 和 `dashOffset`。图层不透明度使用 0–1，不能混用描边及效果的百分比单位。

渐变支持 `linear/radial/angle/reflected/diamond`，还可指定 `opacityStops`、`midpoints`、`offset` 和 `align`。颜色及透明度停止点位置为 0–1，必须严格递增；`midpoints` 为空或与渐变区间数量相同。预检会将省略的可选参数补为校验器规定的固定值，便于比较局部更新的实际含义。这些值属于制作计划的约定，不代表 PhotoCraft 图形界面的默认设置。

形状填充的 `offset:[x,y]` 使用相对比例，例如 `[-0.28,-0.4]`，不能把百分数直接写成 `[-28,-40]`。线性渐变在 `angle:-90` 时从上方的首个停止点过渡到下方的末个停止点；反转使用 `reverse`。制作时仍需查看实际预览确认方向和范围。

### 矢量路径与蒙版

形状的 `path` 以及蒙版的 `mask.path` 使用同一路径结构：

```json
{"subpaths":[{"knots":[[0,0],[100,0],[100,80],[0,80]],"closed":true,"op":"combine"}],"fillRule":"nonzero","inverted":false}
```

蒙版对象为 `{"path":上述路径,"enabled":true,"linked":true,"density":100,"feather":0}`；density 为 0–100，feather 为 0–1000。

每条子路径至少两个 knot。点可以是 `[x,y]`，或有 `anchor`、可选 `in/out` 控制点和 `smooth` 的对象。子路径 `op` 支持 combine/subtract/intersect/exclude；`fillRule` 支持 nonzero/evenodd。形状需要额外裁剪时，将它放入带 mask 的父 group。

### 像素笔触

```json
{"key":"detail","name":"细节","kind":"pixel","parent":"root","strokes":[{"color":"#FFFFFF","size":6,"opacity":0.7,"points":[[10,12,1],[32,18,0.5]],"seed":0,"brush":{"hardness":1,"spacing":0.1,"flow":1}}]}
```

`points` 每项必须为 `[x,y,pressure]`，其中压力范围为 0–1。`size` 范围 0.5–5000，`opacity` 为 0–1，`seed` 是非负 64 位整数。`strokes:[]` 表示合法空白像素层。`brush` 仅接受八个受控参数：`hardness`、`spacing`、`flow`、`pressureSize`、`pressureOpacity`、`angle`、`roundness` 和 `aliased`。省略时依次补为 `1`、`0.1`、`1`、`false`、`false`、`0`、`1` 和 `false`；这些是校验器规定的固定值，不代表图形界面默认值，也不依赖外部笔刷预设。

### 图层效果

`effects` 最多 32 项，每项为 `{"kind":"dropShadow","params":{"color":"#000000","opacity":40,"distance":3,"size":4}}`。支持 dropShadow、innerShadow、outerGlow、innerGlow、stroke、colorOverlay、gradientOverlay、bevelEmboss 和 satin；不同效果只接受对应参数。效果 opacity 为 0–100；angle 为 −360 至 360；size/distance 为 0–1000。颜色、blend、轮廓及效果专用字段经过严格校验，未知字段报错。通过空效果数组可以明确移除当前节点的效果。

效果的 `color/from/to` 必须是不透明颜色：可使用六位颜色、alpha 为 `FF` 的八位颜色或 alpha 为 `1` 的 RGBA 数组；透明度通过效果的 `opacity` 设置。形状填充、形状描边和像素笔触可使用颜色自身的 alpha。

## 共用配色与状态示例

[semantic_palette.py](../examples/drawing-programs/semantic_palette.py) 提供 `example_parameters()`，含两套颜色用途和三个状态。将其返回值存入项目风格参数，组件规格指定 `theme/state/width/height`；改变主题只改变颜色，状态只改变声明的颜色用途和标记可见性。示例不需要外部项目或笔刷资源。

为不同样片使用不同组件 ID，绘制后用[视觉检查](project-tools.md#多产物视觉检查)比较目标尺寸下的轮廓、留白和状态。自动测试验证参数复用，不代表参考风格通过。

## 更新与证据

省略 `layers` 更新全部节点；传入节点 key 只更新该范围。新旧计划的画布与结构必须一致，未选节点内容不能变化，尺寸或结构变体当前使用新组件 ID。源文件必须与已登记证据一致；不自动覆盖外部改动。

每次实际运行保存程序、计划、风格快照、原生请求响应及后端构建证据。成功保留输出 `.pcraft`、原尺寸 PNG 和重开检查；更新保留输入文件。局部更新检查未选节点。失败、超时或记录冲突保留现场，不自动重试。详情见[项目工具](project-tools.md)及[PhotoCraft 接口](photocraft-contract.md)。
