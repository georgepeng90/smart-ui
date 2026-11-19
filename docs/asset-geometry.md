# 正式资产的内容区域与伸缩几何

正式 PNG 资产、页面布局和浏览器渲染共享同一份 `geometry`。布局实例的宽高表示内容区域的目标尺寸；PNG 可以在内容区域之外保留描边、阴影或光晕。内容区域是制作时明确指定的布局主体，不是非透明像素包围盒，也不是文字安全区，不能由透明度自动推断。

以项目基础分辨率的 1× 尺寸制作。组件主体、四边外扩和参考依据在绘制前登记，工具先校验源稿画布、导出裁区和收短之前的内容框，再按需裁掉重复的中段。边角的像素细节和从 PNG 四边量取的固定边距始终使用相同单位。单件与多图素源稿的字段见[组件的设计尺寸与制作区域](project-tools.md#组件的设计尺寸与制作区域)。

制作者需在目标显示尺寸下核对圆角半径、线宽、高光及切线位置。工具能核对已声明的尺寸、切线的合法范围、收短后的像素与 Unity 导入值，无法从截图判断某个高光应当画几像素宽。

## 为切片伸缩制作 UI 图形

先确定组件要改变哪个方向的尺寸。仅随文案变宽的按钮或固定高度的条形控件，可以保留左右端部，只伸缩横向中段。宽度固定、只变高度的控件可采用纵向三切片。宽高都可变化的弹窗和面板用九宫格，四角固定，上下边沿横向伸缩，左右边沿纵向伸缩，中心沿两轴变化。 [游戏 UI 资产交接指南](https://github.com/miltoncandelero/game-ui-assets-guide#exporting)、[游戏 UI 九宫格的分区示例](https://create.roblox.com/docs/ui/9-slice)

**先圈定固定区域，再缩短可伸缩的中段。** 切线放在圆角、斜角、端帽、角花和局部高光结束的位置之外。固定区域保留这些细节，以及外描边、阴影、光晕和必要的透明留边。切线两侧的边缘和填色应能自然接上。可编辑源文件和预览保留设计宽度，导出时只缩短中段。例如一块横向伸缩的底图，左右端各占 24 像素，中间保留 4 列相同的像素，导出宽度就是 52 像素。控件的最小显示宽度还取决于固定端部、文案、图标、内边距及交互区域。 [Unity Sprite Editor 的边界设置](https://docs.unity.com/en-us/engine/6000.0/manual/unity2d/sprite/9-slice/set-sprite-9slicing)、[游戏 UI 的尺寸与边界交接](https://github.com/miltoncandelero/game-ui-assets-guide#frames-dialogs-and-borders)

**根据填充和纹理选择中段。** 纯色、沿伸缩轴取色一致的边沿，以及只在垂直于伸缩轴的方向变化的渐变，通常适合拉伸。比如横向三切片的按钮可以只保留少量列，保留每列完整的上下渐变。横向光斑、斜向渐变或跨两轴的高光会随相应方向的拉伸改变形状或色变速度。需要保留原形的装饰可独立叠放。需要保持颗粒或花纹尺寸时，使用能无缝衔接的纹理周期，并在支持平铺的运行端检查接缝和尾块处理。按可用渲染方式决定用拉伸、平铺还是独立图层。 [Godot 的分轴拉伸和平铺模式](https://docs.godotengine.org/en/stable/classes/class_ninepatchrect.html#enumerations)、[Unreal UMG 的 Box 和 Border](https://dev.epicgames.com/documentation/en-us/unreal-engine/umg-styling-in-unreal-engine#setting-image-states)

**依据实际采样检查切线和最小尺寸。** 在最小、常用和最大显示尺寸下，用正式资产、实际背景和运行端采用的纹理采样设置查看结果。横纵双向伸缩的控件还要单独拉宽、拉高，检查边角变形、描边断线、透明边裁切、切缝串色和纹理变形。使用线性过滤时，切线附近可能混入相邻像素的颜色，图集中的素材还要检查实际打包后的采样。Android Draw 9-patch 为其格式建议保留足够大的拉伸区和邻接安全像素，libGDX 的 `NinePatch` 则在使用线性过滤时调整拉伸区域的采样坐标。为实际纹理和输出端留下经预览验证的中段与安全空间。 [Android Draw 9-patch](https://developer.android.com/studio/write/draw9patch)、[libGDX NinePatch 的采样实现](https://github.com/libgdx/libgdx/blob/master/gdx/src/com/badlogic/gdx/graphics/g2d/NinePatch.java)

### 不同引擎如何解释切片

| 工具 | 配置与渲染方式 |
| --- | --- |
| Unity | 从 Sprite 的四边定义固定区域，uGUI 支持拉伸或平铺。 [Unity uGUI 的导入与验收](#unity-ugui-的导入与验收) |
| Android Draw 9-patch | `.9.png` 的边缘标记分别描述可拉伸区和可选的内容区。编辑器可预览不同大小，标出有风险的拉伸区。 [Android Draw 9-patch](https://developer.android.com/studio/write/draw9patch) |
| libGDX | `NinePatch` 可对按钮等 Scene2D 控件绘制固定边角与可伸缩区。TexturePacker 可读取 `.9.png` 的切片标记和内容内边距标记。 [libGDX NinePatch](https://libgdx.com/wiki/graphics/2d/ninepatches)、[libGDX TexturePacker](https://libgdx.com/wiki/tools/texture-packer#ninepatches) |
| Godot | `NinePatchRect` 用四边的 `patch_margin` 标出固定区域，可为两个轴分别选择拉伸、平铺，或通过 `Tile Fit` 调整平铺块的尺寸，使每块都能完整显示。 [Godot NinePatchRect](https://docs.godotengine.org/en/stable/classes/class_ninepatchrect.html) |
| Unreal UMG | Slate Brush 的 `Box` 拉伸边和中间，`Border` 平铺边并省略中心。固定部分由 `Margin` 与 `Image Size` 共同决定。 [Unreal UMG 样式](https://dev.epicgames.com/documentation/en-us/unreal-engine/umg-styling-in-unreal-engine#setting-image-states) |

### Unity uGUI 的导入与验收

- **在 Unity 中手工导入。** 将切片 PNG 导入为 `Sprite (2D and UI)`，将 Mesh Type 设为 `Full Rect`，在 Sprite Editor 的 Border 字段填写从实际 PNG 四边向内量取的像素数。`Sprite.border` 的分量顺序为左、下、右、上。uGUI `Image` 的 `Sliced` 拉伸边和中心，`Tiled` 重复对应区域。用 `RectTransform` 的目标宽高检查变化。 [Unity Sprite 切片设置](https://docs.unity.com/en-us/engine/6000.0/manual/unity2d/sprite/9-slice/set-sprite-9slicing)、[Sprite.border](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityengine/sprite/border)、[uGUI Image.Type](https://docs.unity3d.com/Packages/com.unity.ugui@2.0/api/UnityEngine.UI.Image.Type.html)
- **通过 SmartUI 构建。** `apply` 将 `Assets/SmartUI/Sprites/<asset_id>.png` 配置为受管 Sprite。正式导出在同一路径时直接复用 PNG，其他路径的源图生成构建副本。Unity 将 Single、Full Rect 和 100 Pixels Per Unit（每单位像素数，PPU）等导入设置保存到 Sprite 的 `.meta`。切片 Sprite 的 Border 由导出资产的 `geometry.borders` 按左、下、右、上自动设置，构建回读受管 Sprite 的实际尺寸、Border 和 PPU，并核对切片 Image 使用 `Sliced` 和 Multiplier 1。已加载的宿主 Canvas 使用参考 PPU 100。 [Unity 接入工具](unity-tools.md)
- **按目标尺寸排版。** uGUI 绘制切片时，固定边先按 `源边距 × Canvas Reference PPU ÷ (Sprite PPU × Image PPU Multiplier)` 换算为布局单位。在 SmartUI 的 100 / 100 / 1 设置下，24 像素的固定边正好占 24 个设计单位。圆角和高光也须按这个尺寸绘制。`SetNativeSize` 用实际样片的 `Sprite.rect` 宽高除以 `Sprite PPU ÷ Canvas Reference PPU` 设置 `RectTransform.sizeDelta`，同时令 `anchorMax` 等于 `anchorMin`。这个计算独立于 Image PPU Multiplier，得到的是样片换算后的宽高。使用缩短中段的样片时，按设计指定的目标宽高排版。 [uGUI PPU 实现](https://github.com/Unity-Technologies/uGUI/blob/5e55142c416efc0440b44b1ea7e23811219ed57a/com.unity.ugui/Runtime/UGUI/UI/Core/Image.cs#L763-L782)、[SetNativeSize 实现](https://github.com/Unity-Technologies/uGUI/blob/5e55142c416efc0440b44b1ea7e23811219ed57a/com.unity.ugui/Runtime/UGUI/UI/Core/Image.cs#L891-L900)、[切片边距换算](https://github.com/Unity-Technologies/uGUI/blob/5e55142c416efc0440b44b1ea7e23811219ed57a/com.unity.ugui/Runtime/UGUI/UI/Core/Image.cs#L1180-L1192)
- **核对最小显示尺寸。** uGUI 绘制时，如果某轴的尺寸小于换算后两侧固定边的总厚度，就按比例压缩该轴两侧的边。SmartUI 预览和 Unity 构建校验切片布局时要求目标中心在两个轴上都有正尺寸。检查时用内容框加上外扩得到完整目标图像尺寸。 [Unity 固定边压缩](https://github.com/Unity-Technologies/uGUI/blob/5e55142c416efc0440b44b1ea7e23811219ed57a/com.unity.ugui/Runtime/UGUI/UI/Core/Image.cs#L1507-L1536)、[SmartUI 伸缩模式与最小尺寸](#伸缩模式)
- **图集与实机检查。** 双线性过滤会混合邻近像素，图集 `Padding` 可减少相邻素材串色。Unity 手册建议 Canvas UI 图集关闭 `Allow Rotation`。在目标设备上按实际图集的过滤、留边和平台压缩设置查看最窄、最矮和拉伸后的控件，留意细线、阴影和切缝。英文美术作者记录过顶边少留一行像素的情况。原尺寸下看不出异常，拉高后高光变成了宽带。 [Unity Sprite Atlas 手册](https://docs.unity.com/en-us/engine/6000.0/manual/unity2d/sprite/atlas/sprite-atlas-reference#packing)、[高光切线复盘](https://z3er1n.itch.io/ui-pack-vol1/devlog/1651971/v13-the-buttons-9-slice-border-has-been-wrong-since-v10)

交接时注明实际 PNG 的切线、允许伸缩的方向、内容区域与外扩，以及已核对的显示尺寸和状态。

## 几何字段

```json
{
  "content_rect": {"x": 7, "y": 11, "width": 70, "height": 40},
  "mode": "nine_slice",
  "borders": {"left": 19, "top": 17, "right": 29, "bottom": 13}
}
```

上述数值是说明非对称边距的示例。几何坐标以相应区域或图像的左上角为原点，向右、向下为正，单位为整数像素，矩形采用左闭右开、上闭下开的范围。导出请求中的几何相对于裁切区域，导出资产的几何相对于实际 PNG。布局实例单独指定显示宽高。 [正式 PNG 导出](project-tools.md#正式-png-导出)

| 字段 | 约束 |
| --- | --- |
| `content_rect` | 必须完整包含 `x`、`y`、`width`、`height`。位置非负，宽高为正，矩形完整位于 PNG 内。 |
| `mode` | 必须是 `contain`、`stretch`、`nine_slice`、`horizontal` 或 `vertical`。 |
| `borders` | 仅三个切片模式必须声明；其余模式禁止声明。必须完整包含 `left`、`top`、`right`、`bottom`，各值非负。 |

`borders` 表示从完整 PNG 四边向内量取的固定区域尺寸，不是从内容区域量取的边距。左右之和必须小于 PNG 宽度，上下之和必须小于 PNG 高度，源中心宽高必须为正。伸缩轴上的固定区域必须覆盖对应外扩：例如左边至少为 `content_rect.x`，右边至少为 `PNG宽 - content_rect.x - content_rect.width`。这样原始外扩始终位于固定区域，不会因中心拉伸而改变布局主体的位置。

不接受未知字段、缺失字段、布尔值、浮点源坐标或非有限数值；非法配置直接抛出含字段说明的 `ValueError`。零边距是合法值，不产生零面积补丁。

## 伸缩模式

| 模式 | 内容区域的处理 | 完整 PNG 的处理 |
| --- | --- | --- |
| `contain` | 在目标内容框中等比居中，可留空白 | 按相同比例缩放，包括外扩；完整图像可能超出内容框 |
| `stretch` | 分别缩放到目标宽高 | 外扩按对应轴的比例缩放 |
| `nine_slice` | 横向和纵向均可伸缩 | 外扩和四角保持源像素尺寸，边沿只在对应轴伸缩，中心沿两轴伸缩 |
| `horizontal` | 只改变内容宽度；目标内容高度必须等于原内容高度 | 上下边界必须为零，生成三列补丁，纵向保持原尺寸 |
| `vertical` | 只改变内容高度；目标内容宽度必须等于原内容宽度 | 左右边界必须为零，生成三行补丁，横向保持原尺寸 |

切片模式下，完整目标图像的宽高为目标内容宽高加原始左右、上下外扩之和。最小尺寸必须据此判定：完整目标宽度严格大于 `left + right`，完整目标高度严格大于 `top + bottom`。目标中心也必须具有正宽高；等于边界合计时直接报错，不压缩固定角，也不接受零中心。切片规则描述几何，不代表角部像素在浏览器任意缩放、设备像素比例或颜色管理下始终逐字节相同；以源像素一比一绘制时固定角不缩放。

## Python 接口与补丁坐标

`smartui_mcp.slicing.validate_geometry(geometry, width, height)` 校验源 PNG 的整数宽高和几何，返回独立字典，不修改输入。调用者必须从真实 PNG 读取尺寸，不根据声明猜测文件尺寸。

`smartui_mcp.slicing.image_patches(geometry, source_size, target_size)` 接收 `[宽, 高]`，其中 `source_size` 为正整数像素，`target_size` 为有限正数，后者允许小数。返回：

```json
{
  "image_size": [150, 100],
  "content_offset": [-7, -11],
  "patches": [
    {"source": [0, 0, 19, 17], "target": [0, 0, 19, 17]}
  ]
}
```

上例只展示补丁数组中的第一项；完整结果还包含其余非空补丁。`image_size` 是完整目标图像的宽高；`content_offset` 是完整目标图像左上角相对布局内容框左上角的位移。负值表示向左或向上外扩。`source` 与 `target` 都是 `[x, y, width, height]`，分别相对完整源 PNG、完整目标图像左上角。页面渲染时，补丁最终位置为“实例内容框左上角 + `content_offset` + `target` 的位置”。

`contain` 会先在目标框内居中内容，再计算完整图像的位移；因此 `content_offset` 也可能为正。`stretch` 对原始内容偏移分别乘以横纵比例。切片模式保留外扩，偏移固定为 `[-content_rect.x, -content_rect.y]`。

返回几何不取整，包括等比缩放产生的小数；浏览器可使用子像素坐标。切片补丁按从上到下、每行从左到右排列，覆盖完整目标图像且互不重叠。函数仅计算补丁，不读取图像、不执行 Pillow 渲染，也不处理整页文字。浏览器预览和 PNG 导出应使用同一页面及这份几何，避免另建一套排版与切片逻辑。

## 验证方法

运行 `python -m pytest tests/test_slicing.py`。合成测试覆盖三种内容宽度、两种高度下的固定角 RGBA、边沿和中心，源尺寸不变时整图像素一致，非对称外扩、单轴伸缩、小数坐标、零边距、最小尺寸以及非法输入。测试中的 Pillow 只用于独立合成补丁并核对像素，不是生产页面渲染器。通过这些测试表明几何计算符合约定，不代替浏览器实际渲染或美术确认。
