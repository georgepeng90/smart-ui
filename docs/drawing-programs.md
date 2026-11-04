# 项目 Python 绘制程序约定

项目程序把共用参数和组件规格转换成可验证的 Krita 绘制计划。形状、图层数量、材质、笔刷选择和伸缩规则由程序决定，通用工具不内置按钮或其他固定组件画法。

## 保存与执行

用 `project_save_program(project_root, expected_revision, program_id, name, description, source)` 保存完整源码，用 `project_get_program(project_root, program_id)` 读取。保存阶段只编译检查 Python 语法，不执行顶层语句。`project_root` 是业务工程根目录的绝对路径。源码默认保存在工程内 `art/ui/programs/<id>/<version>.py`，可在创建项目时通过 `paths.programs` 指定工程内相对目录；程序元数据的 `source_path` 始终保存工程相对 POSIX 路径。状态位于 `.smartui/project.json`，项目状态保留版本、SHA-256 和历史。

源码提供入口：

```python
def build(parameters, spec, component_id):
    return plan
```

`parameters` 是当前 `style.parameters`；`spec` 是当前组件完整规格，含必需的 `program_id`；`component_id` 是当前组件标识。其余参数字段和业务约束由程序定义，非法配置应直接给出清楚错误。

`project_plan_component(project_root, component_id)` 与绘制都会在独立 Python 进程运行程序，超时为 60 秒。普通 Python `print` 输出被捕获，不混入计划 JSON；语法错误、运行异常、超时和无效计划都明确报错。入口应直接返回只含标准 JSON 类型和有限数值的字典，不能返回自定义对象、集合、NaN 或无穷大。

程序是可信本机 Python 代码，可以访问文件和网络；独立进程不是安全沙箱。程序应把工作集中于计算计划，不在顶层或 build 中修改项目、源文件或 Krita 文档。不要假设项目目录是当前工作目录，也不要依赖程序文件相邻资源自动加入导入路径；必要数据通过参数明确传入。

## 最小程序示例

下例生成一层实色面板。先用 `krita_list_brushes` 核对笔刷精确名称，把它保存到共用参数，例如 `{"brush":"b) Basic-5 Size"}`。组件规格使用 `{"program_id":"flat-panel","width":320,"height":120,"color":"#6AAD3D"}`。

```python
def build(parameters, spec, component_id):
    width, height = spec["width"], spec["height"]
    return {
        "width": width,
        "height": height,
        "nodes": [
            {"key": "surface", "name": component_id + "/surface",
             "kind": "paintlayer", "parent": "root"},
        ],
        "paint": [
            {"node": "surface", "brush": parameters["brush"],
             "color": spec["color"], "size": 1,
             "strokes": [
                 {"kind": "rectangle", "rect": [1, 1, width - 2, height - 2],
                  "fill": "foreground"},
             ]},
        ],
    }
```

通过 `project_save_program.source` 保存以上完整源码，再定义组件，先生成计划。计划预检不连接 Krita，也不表示笔刷已安装或实际画面已验收；实际绘制前仍需查询本机预设并查看产物。

项目程序应根据对应参考和编辑需求定义节点、参数与绘制步骤。示例程序只演示通用接口，不限定组件的图层结构或视觉样式。

## 配色与状态变体示例

[通用操作背景示例](../examples/drawing-programs/semantic_palette.py) 用一套画法生成两套配色和三种状态。主题表提供轮廓、操作底色、禁用底色和选中标记的颜色。状态表引用这些用途，选中态添加圆点，禁用态改用对应底色。各变体共用轮廓、底面和标记位置的几何规则。使用合成参数演示接口，项目可以根据需求定义不同的状态名和配色。

先用 `krita_list_brushes` 确认准确的预设名。在源码或插件根目录运行下面的 Python 代码，将输出的对象作为 `project_update_style.parameters` 保存。

```python
import json
from runpy import run_path

example = run_path("examples/drawing-programs/semantic_palette.py")
parameters = example["example_parameters"]("填入已核对的 Krita 笔刷名称")
print(json.dumps(parameters, ensure_ascii=False))
```

用 `project_save_program` 将示例的完整源码保存为 `semantic-palette`。定义组件时可使用以下规格。

```json
{"program_id":"semantic-palette", "theme":"lagoon", "state":"available", "width":180, "height":52}
```

其他变体可以切换到 `ember` 主题，或选用 `selected`、`disabled` 状态。为各样片指定组件 ID，逐一预检和绘制，然后把实际的成功运行加入[多产物视觉检查](project-tools.md#多产物视觉检查)。确认引用该颜色用途的样片得到预期配色，共用几何及不相关状态保持一致。示例只依赖传入的项目参数和规格，缺少笔刷、主题、状态或颜色引用时会报告具体配置项。

## 计划结构

计划顶层只包含 `width`、`height`、`nodes`、`paint`：

```json
{
  "width":320,
  "height":120,
  "nodes":[
    {"key":"component", "name":"panel", "kind":"grouplayer", "parent":"root"},
    {"key":"surface", "name":"panel/surface", "kind":"paintlayer", "parent":"component", "properties":{"opacity":255}},
    {"key":"cutout", "name":"panel/cutout", "kind":"transparencymask", "parent":"surface", "rects":[[0,0,320,120,255]]}
  ],
  "paint":[
    {"node":"surface", "brush":"b) Basic-5 Size", "color":"#6AAD3D", "size":1,
     "opacity":1, "strokes":[{"kind":"rectangle", "rect":[1,1,318,118], "fill":"foreground"}]}
  ]
}
```

画布宽高为 1–16384 的整数。数值必须有限，布尔值不作为数值。未知字段会报错。

### 节点

`nodes` 按声明顺序建立结构；父节点必须先声明。

| 字段 | 约束 |
| --- | --- |
| `key` | 唯一标识，以字母或数字开头，仅含字母、数字、点、下划线和连字符；不能使用保留值 root |
| `name` | 非空且在计划中唯一的显示名称，可以包含斜杠 |
| `kind` | paintlayer、grouplayer 或 transparencymask |
| `parent` | root 或此前节点 key；绘画图层与分组只能放在分组内 |
| `properties` | 可选属性对象，省略的字段使用下述明确默认值 |
| `rects` | 仅透明度蒙版使用，必需；空数组表示全透明 |

透明度蒙版必须挂在绘画图层或分组下，不能直接挂 root，也不能挂在另一个蒙版下。`rects` 每项是整数 `[x,y,width,height,alpha]`，宽高至少为 1，alpha 为 0–255。

`properties` 允许布尔字段 `visible/locked/inherit_alpha/alpha_locked`、整数 `opacity`（0–255）和 `blending_mode`（normal/multiply/screen/overlay）。`alpha_locked` 仅适用于绘画图层。最终锁定属性在绘制完成后应用，避免锁定妨碍本次绘制。

默认属性为 `visible=true`、`locked=false`、`opacity=255`、`blending_mode="normal"`、`inherit_alpha=false`，绘画图层另有 `alpha_locked=false`。计划会补齐这些值；删去先前指定的属性意味着恢复默认值，而不是沿用源文件旧值。

### 绘制步骤

`paint` 按数组顺序执行。每项必须有 `node/brush/color/size/strokes`，可选 `opacity`（默认 1）。`node` 只能指向绘画图层 key，分组和蒙版通过节点属性与 rects 表达，不接受 paint 步骤。

笔刷名为非空精确预设名；颜色为 `#RRGGBB`；size 在 0.01–10000，opacity 在 0–1；strokes 是非空数组。所有坐标已经是最终画布坐标，工具不再对项目参数进行缩放。

| 笔触 kind | 字段 |
| --- | --- |
| line | from、to 为两个整数坐标；可选 pressure_from、pressure_to，范围 0–1，默认 1 |
| rectangle / ellipse | rect 为 `[x,y,width,height]`，宽高至少 0.01；可选 fill |
| path | commands 为命令数组，首项必须 M；可选 fill |

形状 fill 允许 none（默认）或 foreground。路径命令为 `["M",x,y]`、`["L",x,y]`、`["Q",cx,cy,x,y]`、`["C",c1x,c1y,c2x,c2y,x,y]` 和 `["Z"]`。描边使用指定 Krita 原生笔刷，封闭路径与形状填色是几何填色；不要将它们描述为完整自由手绘笔迹。

## 局部更新与版本追溯

绘制时 `layers` 选择计划节点 key，可包含分组或蒙版。工具对比上次成功运行的计划，拒绝所选范围外的变化，不限制某个固定角色或参数名。当前画布与结构也必须匹配；需要新尺寸或结构时用新组件 ID。

选择分组只更新分组自身属性，不隐式更新子树；需要修改子节点时逐一列出它们的 key。局部更新必须有已登记的 `plan.json` 及其摘要，供工具校验和比较。

每次绘制保留实际程序版本、源码摘要、`program.py` 和 `plan.json`，使结果能追溯到真正执行的源码。保存新源码后，旧 run 继续保留旧版本，依赖组件需要重新检查；不能直接修改历史版本号表示已完成重画。完整状态与失败语义见[项目工具说明](project-tools.md)。
