# 游戏 UI 组件的可编辑制作与交接

制作按钮、边框、面板和其他基础组件前，先确定接手的人会改什么。宽高、圆角、主题色、描边和光影都可能需要单独调整。把这些内容保存在对应的形状属性、图层效果或独立图层中，让修改可以从源文件直接完成。

## 按接手后的修改选择做法

| 常见修改 | 制作方式 |
| --- | --- |
| 改按钮宽高、圆角或轮廓 | 把底板保留为矩形、圆角矩形或可编辑路径，在形状属性或路径节点上修改。底色用形状填充，几何和填色可以分别调整。 |
| 改形状边线的粗细、颜色或内外位置 | 与底形共用轮廓的边线使用同一形状的路径描边（`shape.stroke`），还可单独控制线帽、转角和虚线。 |
| 给图层内容加一圈可开关的装饰边 | 需要沿图层可见内容勾边，或将额外一圈边和投影等效果一起管理时，用图层样式（Layer Style）的描边效果。只有确实需要两道不同边线时，才同时使用路径描边和样式描边。 |
| 改材质、亮暗层次或立体感 | 以形状填充控制基底色或基底渐变。希望随底形变化的着色、高光和阴影，可以用渐变叠加、内阴影、投影、内外发光或斜面和浮雕等图层效果。把效果挂在它所依附的图层上。 |
| 改异形镶边、缺口、装饰或纹理 | 有独立轮廓的镶边、高光片和装饰保留独立形状。开孔可以用路径相减，需要控制显示范围时可用蒙版。划痕、颗粒和笔刷肌理可以留在像素层，按需要用蒙版约束范围，让底形和装饰描边仍能单独调整。 |

路径描边沿形状路径绘制，图层样式描边沿图层内容的轮廓绘制。修改原轮廓后，关联的描边应随之更新。Photoshop 将图层样式转换为独立图像层时会解除这种关联。 [Adobe 形状描边](https://helpx.adobe.com/photoshop/desktop/draw-shapes-paths/create-shapes/set-and-manage-shape-stroke-options.html)、[Adobe 转换图层样式说明](https://helpx.adobe.com/photoshop/desktop/create-manage-layers/apply-layer-effects/convert-layer-styles-to-image-layers.html)

图层数量跟着编辑需求走。手游 UI 设计师ミア的按钮示例把渐变、描边和投影挂在同一个底形上，另用一个形状制作有独立轮廓的高光。遇到需要独立配色、显隐或移动的装饰，同样可以给它自己的图层。 [ミア的按钮制作示例](https://note.com/mia_missun/n/na702915a0b28)

## 组织图层、配色和状态

按组件或可独立替换的部件分组，图层名说明用途，例如「底板」「独立高光」「手绘纹理」。同类组件从共用几何和按用途命名的颜色参数取值，例如底色、边框色和高光色。需要做按下、禁用等状态时，明确实际变化的颜色、效果、几何或装饰可见性，并在各状态使用的背景和目标尺寸下检查辨识度。

明确资产在布局里占用的内容区域，以及外描边、投影和光晕伸出内容区域的范围。导出透明图时检查外扩是否完整、有无意外底色或多余空边，确认不同状态切换时布局主体的位置符合设计。需要切片伸缩的组件，按实际使用的尺寸检查切片边界、圆角与边缘。 [正式资产的内容区域与伸缩几何](asset-geometry.md)

组件改宽、改高或整体缩放后，按实际显示尺寸复查描边粗细和光影范围。Photoshop 变换图层时，效果尺寸不会随图层自动同比缩放，可按需要单独缩放效果。 [Adobe 缩放图层效果](https://helpx.adobe.com/sg/photoshop/desktop/create-manage-layers/apply-layer-effects/scale-layer-effects.html)

## 保存源文件并验证交接

保留绘制程序和可编辑的 `.pcraft`，通过正式导出交付供页面或引擎使用的 PNG。需要随文案变化的文字单独保存为可编辑文本，组件底图独立导出。游戏 UI 资产作者 LoudEyes 同时交付可调形状和图层样式的 PSD，以及可直接使用的导出图。[LoudEyes 的分层源文件与导出实践](https://itch.io/devlog/120668/how-i-make-easy-to-use-ui-asset-packs-and-how-you-can-edit-them-to-fit-your-game.amp)

SmartUI 发布组件产物前会自动执行[保存重开与产物检查](photocraft-contract.md#执行与失败)。首次采用一种画法或编辑入口存疑时，在代表样片的副本上试改约定的属性。例如单独改描边颜色，检查边缘和底色，再临时关闭投影，确认原先被投影覆盖的透明区域恢复透明。交接可伸缩外框时，也可试改底形尺寸或圆角，检查描边是否仍贴合轮廓。查看正式 PNG 在目标背景和尺寸下的效果。受 SmartUI 管理的组件通过绘制程序和参数保存正式修改。

<a id="photocraft-editing"></a>

## 在 PhotoCraft 中找到编辑入口

- **形状填充和路径描边。** 在图层面板选中形状本身，打开 Properties → Appearance。用 Fill 和 Stroke 色块分别修改填充和描边颜色或渐变，在 Stroke 一栏调整线宽和描边选项。 [PhotoCraft 形状属性面板](https://github.com/storytold/photocraft/blob/v0.23.1/crates/ui-egui/src/tools/vector_ui/options.rs#L285-L366)
- **图层效果。** 展开图层的 `fx`，双击效果名称打开 Layer Style（图层样式）对话框。同一图层有多个同类效果时，在对话框列表中选中要修改的那一项。下方源码所示的实现会先选中该类型的第一项。每项旁边的眼睛可以单独切换该效果的显隐。 [PhotoCraft 图层效果列表](https://github.com/storytold/photocraft/blob/v0.23.1/crates/ui-egui/src/panels/layer_drag.rs#L336-L394)、[图层样式对话框的初始选择](https://github.com/storytold/photocraft/blob/v0.23.1/crates/ui-egui/src/dialogs/layer_style.rs#L612-L654)

交接时指出常改项的图层名称和编辑入口。例如「选中『底板』，在外观栏改路径描边的颜色和线宽。投影可以在该层的效果列表中单独关闭。」

诊断源文件、查看外来 `.pcraft` 或需要定位交接编辑入口时，可用 `photocraft_inspect_document` 的 `editable` 视图按图层名查找形状填充、路径描边和图层效果。核对颜色值、线宽或单项效果参数时使用完整视图。 [PhotoCraft 检查接口](photocraft-contract.md#mcp-工具)
