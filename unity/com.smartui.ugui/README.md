# SmartUI uGUI Editor 包

此包在 Unity 6 Editor 中构建和核对 SmartUI 预制体。基础控件保存为普通预制体，变体保存为 Prefab Variant，页面使用自己的排列容器承载嵌套预制体实例。包使用 uGUI 2.0 的 `Image`、`Button` 和 `TextMeshProUGUI`，支持固定尺寸与有限的属性更新。

通过 Unity Package Manager 添加该包。在目标工程启用 Force Text 序列化，并安装兼容的 Unity Pipeline。CLI 程序集使用 `com.unity.pipeline` `0.10.0-exp.1` 提供的命令注册接口。等待工程完成编译，再通过 `unity command --project-path <工程绝对路径> --detail full` 确认 `smartui execute` 及必填的 `request_path` 参数可用。

用 SmartUI 项目工具生成请求。`plan` 检查目标和基线，`apply` 按需导入 PNG 并保存发生本地变化的预制体，`verify` 重新加载已保存资源并核对对象及覆盖。结果写入同一请求目录。页面实例使用自有容器的 fileID 和槽位的来源资产身份分别定位。

工程应提供已经包含所需字形的 TMP 字体资产和匹配的材质。文字以实际 TMP 测量结果检查溢出。目前支持固定尺寸下的字号、行高、字距、填色、水平对齐和换行，溢出策略须为 `error`。裁切、缩字、最大行数、基线排列、描边及投影需要单独适配。图像参数和变体换图要求源图与替换图的几何和尺寸一致。对已生成预制体增删节点、改变父级、更换嵌套来源、修改 Button 目标或公开槽位，需要单独迁移。

## EditMode 验证

如果从 Git 安装该包，在目标工程的 `Packages/manifest.json` 的 `testables` 数组中加入 `com.smartui.ugui`，再在 Unity Test Runner 中运行 `SmartUI.UGUI.Editor.Tests` 的 EditMode 用例。用例在临时资产路径创建基础控件、变体和页面，核对同源重复实例、场景引用、恢复继承、等值覆盖、Sprite GUID 及文件写入范围。TMP 映射、目标场景中的显示和点击需要用实际工程资源验收。
