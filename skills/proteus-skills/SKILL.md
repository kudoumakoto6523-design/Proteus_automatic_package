---
name: proteus-skills
description: 使用 proteus-native Python 库创建、编辑和验证真实 Proteus/ISIS 工程，加载 MCU 固件、操作按钮/开关并读取仿真结果。用于用户要求 Proteus Skills、操作 .pdsprj、Proteus 自动化或在 Proteus 中测试按钮驱动电路的任务；普通电路讲解和概念配图不需要此技能。
---

# Proteus Skills

通过 `proteus_api` 交付可继续编辑的 `.pdsprj` 及可核对的原生结果。技能名称是 **Proteus Skills**，调用名是 `$proteus-skills`；依赖的 Python 包名仍为 `proteus-native`，导入名仍为 `proteus_api`。

文件构建、修改和连线优先用库；网表、固件执行和交互由真实 Proteus 进程完成。无需 MCP、OCR 或屏幕坐标。用户明确选择 GUI 时遵循其选择；库未覆盖的操作按需使用 Proteus 界面。

## 环境与版本

当前工作流对应 **proteus-native 0.2.0**。已验证环境为 Windows、Python 3.12、Proteus 8.16 SP3（8.16.36097）；Python 最低要求 3.10。先检查实际解释器中的库，避免源码目录掩盖旧安装：

```powershell
py -3.12 -I -c "import proteus_api; print(proteus_api.__version__); print(proteus_api.__file__)"
```

缺失或仍为旧版时，从用户提供的本库目录安装。在含 `pyproject.toml` 和 `dist/` 的仓库根目录执行：

```powershell
py -3.12 -m pip install --no-index --no-deps '.\dist\proteus_native-0.2.0-py3-none-any.whl'
```

源码安装可用 `py -3.12 -m pip install .`，需要 setuptools>=68。不要因技能改名而尝试安装 `proteus-skills` 包。实际库版本不同于本文时，先核对该版本本地文档和签名。

| 资源 | 默认路径 | 配置入口 |
|---|---|---|
| Proteus | `D:\Proteus\BIN\PDS.EXE` | `Session(project, executable=...)`，要求 PDS.EXE |
| 器件目录 | `C:\ProgramData\program\LIBRARY` | `Library(directory=...)`，仅查询 |
| 新建模板 | `C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj` | `Circuit(template_project=...)` |

按任务检查所需路径；库不会自动探测安装。`import_device(..., library=...)` 的 `library` 是库名选择器，导入仍使用默认目录。`Library(directory=...)` 不会改变导入器；可以从有对应定义的 donor 工程导入。空模板初始化也可能回退到默认 Rescap 样例。不要把这些局部路径参数描述为完整可移植配置。

## 按任务选择工作流

| 用户需求 | 接口与必读参考 |
|---|---|
| 新建、修改器件、连线、电源、网表 | [原理图与网表](references/api-workflows.md#原理图与网表)：`Circuit`、`Library`、`Session` |
| 固件、启停、按时运行、GPIO | [单片机仿真](references/api-workflows.md#单片机仿真)：`Simulation`、固件工具 |
| 按下/松开按钮、切换开关、改变逻辑输入 | [交互控制](references/interactive-controls.md)：`bind_controls` → 保存重开 → `press/release/set_switch` → 继续仿真验证响应 |
| 电压、电流、波形 CSV | [图表与模拟量](references/api-workflows.md#图表与模拟量)：已有图表/探针、`export_graph`、`sample_graph` |

只读取任务需要的参考。复用公共 API；不把研究脚本里的私有内存快照、DLL 偏移或演示型函数当成通用接口。

## 编辑和执行顺序

1. 确定用户要新建还是继续已有工程，以及输出位置、器件/固件和待验证行为。先读取现有元件与网络；引脚、电源、时钟按确切型号和固件确定。
2. **新建用 `Circuit(template_project=...)`，修改用 `Circuit.open(path)`。** 构造器只取模板定义并创建空电路，不能用来保留原图。原生修改或仿真已有项目时准备工作副本，同时处理其相对固件路径。
3. 用 `pins(ref)` 的真实逻辑名称和世界坐标布线。单位为整数 `100000 = 1 mm`，Y 向上；位置不一定是器件中心。带封装映射的 MCU 引脚编号可能是组合编号或 `*`，不要按符号上显示的数字猜接线。
4. 完成文件编辑和所需的 `bind_controls()` 后保存，再启动新的 `Session`。Proteus 会缓存控件绑定，不能靠打开会话中的 ADI 修改立即刷新。文件写入和会话操作串行进行；原生保存关闭后，继续文件编辑必须重新 `Circuit.open()`。同一 Session 的调用也串行执行，库没有并发锁。
5. 验证用户要求：重开检查值与网络；SDF 检查原生连接；继续仿真并读取输出检查电气/固件响应。按钮状态回读不能替代下游响应，网表通过也不能证明布局清晰或电路行为正确。

默认写新输出，已有目标需要明确 `overwrite=True`；用户要求更新原文件时可使用该参数，仍保留源文件变更检测。样例始终使用副本。`Session` 启动并只控制自己的进程，不附着到用户已有窗口。

## 能力边界与失败处理

- 结构重写仅支持已识别的单用户页、CDB v7、FILEVER 840/847。未知对象、图表、多页或多单元结构拒绝重写时，不能删除未知内容或换构造器绕过。只改属性可尝试副本上的 `Session.set_properties()`；其余按实际支持情况使用界面或说明缺口。
- 目录可查询不等于可导入，更不等于可仿真。核对模型、封装及 `pins()`；`VPULSE` 实例导入仍不支持。自动布线只有有限正交路径，复杂布局可能需要调整位置或手工路由。
- 支持六类二态控件，使用当前工程共享的 0–9 执行器键槽，每个控件占两个；其他绑定会减少最多五个控件的容量。不是任意引脚强制输入，也不覆盖多状态旋钮或外部 DLL 交互模型。
- 定时和交互有各自的 DLL 指纹限制。保留接口的拒绝行为；不改白名单/偏移，不用墙钟等待或“时间暂时不变”推断仿真成功。报告 `run_for()` 的实际时间、超出量和 `completion`。
- GPIO 日志是数字驱动事件；模拟量需要已有图表和探针。库尚无通用 ERC、任意图表/探针创建、通用实时引脚电压读取或原理图图片导出接口。图片不能替代真实工程。

`Session` 不是上下文管理器，`close()` 不自动保存。正常结束时 `sim.stop()`（如已仿真）、保存所需修改、`s.close()`。异常保留原始错误、工程和 `s.pid`，尝试正常关闭；未知保存对话框不自动放弃内容。

`s.process.poll() is None` 表示自有进程仍运行。只有明确可丢弃的测试副本，才可用该 Session 的 `s.process.terminate()`、`s.process.wait(timeout=5)` 清理。不要按进程名终止用户的其他 Proteus。失败后依据路径、模型、绑定、版本或对话框原因修正，不原样无限重试。

## 验证与交付

为本次任务保留简短可重跑脚本与必要断言；优先检查应连接/应分离的网络、目标值、输入时序和实际输出。参考中的现有检查按任务选择，不要求每次运行完整库测试。

交付工程绝对路径及实际产生的 SDF、CSV、日志/JSON 和脚本。分别说明文件重开、原生网表、交互状态、下游响应、视觉检查的完成情况，只报告本次证据支持的结论。
