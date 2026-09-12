# proteus-native

**简体中文** | [English](README.en.md)

proteus-native 是一个用于创建、编辑和仿真 Proteus 电路的 Python 库。它提供原理图文件读写、网表导出、单片机固件加载、按钮和开关控制及仿真结果读取接口。仿真由已安装的 Proteus 程序执行，代码不依赖 computer-use 或屏幕坐标。

**版本 0.2.0 · 开发者预览版。** 测试环境为 Windows、Python 3.12 和 Proteus 8.16 SP3（8.16.36097）。

## 环境要求

- Windows，Python **3.10 或更高版本**。
- 已安装 Proteus 及电路所需的器件模型。当前测试使用 **8.16 SP3（8.16.36097）**。
- 快速开始需要 Proteus 官方 `Rescap.pdsprj` 样例；单片机集成测试还需要官方 STM32 闪灯样例。
- Python 运行时仅使用标准库。Proteus 程序、器件库和官方样例需要单独安装。

## 安装

在项目根目录的 PowerShell 中安装 [wheel](dist/proteus_native-0.2.0-py3-none-any.whl)：

```powershell
py -3.12 -m pip install --no-index --no-deps '.\dist\proteus_native-0.2.0-py3-none-any.whl'
```

也可从源码安装：

```powershell
py -3.12 -m pip install .
```

包名为 `proteus-native`，导入名为 `proteus_api`。以下命令应输出 `0.2.0`：

```powershell
py -3.12 -c "import proteus_api; print(proteus_api.__version__)"
```

源码安装需要 `setuptools>=68`；安装 wheel 不需要构建工具。

## 配置

下表列出当前版本的内置路径及对应参数。使用前请核对安装位置；路径不会自动检测。

| 用途 | 默认路径 | 参数 |
|---|---|---|
| Proteus 程序 | `D:\Proteus\BIN\PDS.EXE` | `Session(project, executable=...)` |
| 器件目录查询 | `C:\ProgramData\program\LIBRARY` | `Library(directory=...)` |
| 新建工程模板 | `C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj` | `Circuit(template_project=...)` |

`Circuit.import_device(..., library=...)` 仍从默认器件目录导入。`library` 用于选择库名，不能指定目录；`Library(directory=...)` 的设置也不会传给导入器。空模板初始化可能回退到默认样例。因此，自定义路径尚未覆盖所有操作。

## 快速开始

将 `template` 改为实际的官方样例路径。下面的代码创建包含电阻、电容、连线和地端子的 `.pdsprj` 工程。

```python
from proteus_api import Circuit

template = r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj"
circuit = Circuit(template_project=template)
circuit.add("RESISTOR", "R1", "10k", 0, 0)
circuit.add("CAPACITOR", "C1", "100n", 2540000, 0)
circuit.connect("R1.2", "C1.2")
circuit.add_terminal("ground", "", "C1.1")
project = circuit.save("rc.pdsprj")
print(project)
```

`rc.pdsprj` 会保存在当前目录，可用 Proteus 打开和编辑。该示例演示工程生成；运行仿真前还需配置电源、输入和模型。已有文件默认不会被覆盖，覆盖时需显式传入 `overwrite=True`。

在同一脚本中继续执行以下代码，并按安装位置修改 `executable`，即可导出 Proteus 编译的 SDF 网表：

```python
from proteus_api import Session

session = Session(project, executable=r"D:\Proteus\BIN\PDS.EXE")
netlist = session.export_netlist("rc.sdf")
print(netlist["parts"])
session.save()
session.close()
```

输出的元件表包含 `R1` 和 `C1`，网表保存在 `rc.sdf`。每个 `Session` 启动独立的 Proteus 进程；修改后调用 `save()` 保存，再用 `close()` 关闭。

`Circuit(template_project=...)` 使用模板中的器件定义新建空电路。编辑已有工程请使用 **`Circuit.open(path)`**。

坐标单位为 `100000 = 1 mm`，Y 轴向上。元件位置以模板原点为准；`pins(ref)` 返回实际引脚坐标。

## 功能

| 功能 | 接口与支持范围 |
|---|---|
| 器件查询与导入 | `Library.search/get` 查询引脚、模型和默认属性；`import_device`、`import_from_project` 导入支持的器件定义 |
| 原理图编辑 | 添加、复制、删除、重命名元件，修改属性、位置和朝向；读取支持的工程格式并原子保存 |
| 连线与网络 | 按引脚连接和断开，正交布线，多引脚结点与总线，六类端子、网络标签和电源轨 |
| Proteus 会话 | 打开工程、导出 SDF，通过 ADI 修改属性并回读，保存和关闭工程 |
| 单片机仿真 | 检查和加载 ELF / HEX 固件，启动、暂停、停止、复位，按时长运行并读取实际仿真时间 |
| 按钮与开关 | `bind_controls` 配置控件；`press`、`release`、`set_switch` 操作二态输入，`controls` 和 `control_state` 读取控件状态 |
| 仿真结果 | 读取 STM32 CM3 GPIO 日志事件，导出已有图表的电压、电流和波形 CSV，对采样数据进行插值 |

完整示例见 [API 使用说明](api/README.md)。

## 按钮与开关

下面使用已有可仿真工程，其中 `SW1` 是连接好的 `BUTTON`。先绑定控件并保存，再用新的 `Session` 打开保存后的工程：

```python
from proteus_api import Circuit, Session, Simulation

circuit = Circuit.open("button_circuit.pdsprj")
circuit.bind_controls("SW1")
project = circuit.save("button_bound.pdsprj")

session = Session(project)
sim = Simulation(session)
sim.run_for(0.01)
pressed = sim.press("SW1")
sim.run_for(0.1)
sim.release("SW1")
print(sim.control_state("SW1"))  # 0
print(pressed["command_time_seconds"])
sim.stop()
session.save()
session.close()
```

`press()` 将输入设为 1 并保持，`release()` 设为 0；开关也可使用 `set_switch("SW1", True)` 和 `False`。操作前必须启动仿真。连续运行时，接口会先暂停、提交操作，再恢复运行；`controls()` 和 `control_state()` 也会短暂停住，读取一致的快照后恢复。原本暂停的仿真保持暂停。

支持的器件为 `BUTTON`、`SW-SPST`、`SW-SPST-MOM`、`SWITCH`、`LOGICSTATE` 和 `LOGICTOGGLE`。绑定使用 Proteus 原生 `INC` / `DEC` 全局执行器键槽：共 10 个，每个控件占两个，因此最多绑定 5 个控件。其他元件已有绑定会减少可用数量。修改绑定后必须保存并重新打开工程。

`control_state()` 返回控件的机械位置或逻辑输入状态，不表示下游引脚电压。`command_time_seconds` 是操作提交所在暂停点的仿真时间；电路传播和固件响应需继续仿真后读取。交互接口只接受已验证的 Proteus 8.16 `ISIS.DLL`、`NETLIST.DLL` 和 `PRIMS.DLL` 指纹，其他构建会报错。

[STM32 按钮示例](api/examples/button_mcu)提供 BUTTON → PA0 → 固件 → PA5 的固件源码和构建文件；[检查脚本](api/check_button_mcu.py)在官方样例副本中配置电路并读取 GPIO 响应。

## 测试

格式解析检查不启动 Proteus：

```powershell
py -3.12 -B api/check_sdf.py
py -3.12 -B api/check_measurements.py --parser-only
py -3.12 -B api/check_control_bindings.py
```

安装库后，可运行调用 Proteus 的集成测试：

```powershell
py -3.12 -I api/check_installed.py
```

集成测试要求默认目录下的器件库、Rescap 样例，以及 `SAMPLES\VSM for Cortex M3\STM32\STMCubeMX LED Blink\STMCubeMX LED Blink.pdsprj`。测试使用样例副本，结果保存在 `api/artifacts/installed-*/`。`-I` 确保测试导入已安装的库。

交互检查分别覆盖六类控件和 STM32 按钮输入，均会启动独立的 Proteus 进程：

```powershell
py -3.12 -I api/check_interactions.py
py -3.12 -I api/check_button_mcu.py
```

已有测试结果均来自上述 Windows / Proteus 8.16 环境：

| 测试 | 结果记录 |
|---|---|
| 安装与集成（0.2.0） | 从 `site-packages` 导入后完成工程编辑、属性修改、固件加载、GPIO 和模拟量读取：[结果](api/artifacts/installed-447ced83/result.json) |
| 单片机仿真 | 0 → 0.01 → 1.25 秒续跑、PA5 高低事件、复位及启停暂停：[结果](api/artifacts/simulation-check-a7ea5ae3/result.json) |
| 交互控制（0.2.0 安装包） | 六类控件、0–9 键槽、冲突和未知构建拒绝、运行状态恢复及定时器浮点残余处理：[结果](api/artifacts/interactions-5e95f58e/result.json) |
| STM32 按钮输入（0.2.0 安装包） | BUTTON → PA0 → 固件 → PA5，输出随松开、按下、松开变为 0 → 1 → 0：[结果](api/artifacts/button-mcu-aa66411e/result.json)、[原生日志](api/artifacts/button-mcu-aa66411e/simulation.log) |
| 模拟量计算 | 输入幅度由 1 改为 2，重新仿真后的电压和电流约为原来的两倍：[结果](api/artifacts/measurement-check-c048d7fb/result.json) |

## 限制

- **工程格式**：结构编辑支持已识别的单用户页、CDB v7、FILEVER 840/847。未知对象、多单元器件和多页结构会拒绝整体重写。器件可被查询到，不代表其导入和仿真已受支持。
- **布局与布线**：采用有限的正交路径和总线布局，尚无完整避障自动布局。无法布线时会报错。
- **模拟输入与测量**：要求工程已有图表和探针。专用信号发生器参数只能修改为等字节长度的值；尚不支持创建任意图表、探针或在运行时强制设置任意引脚输入。
- **GPIO**：当前解析器已在 STM32 CM3 日志格式上测试，其他 MCU 需要对应解析器。读取日志会替换系统剪贴板内容。
- **定时运行**：`run_for()` 使用原生定时断点，正常完成返回 `completion='timer_breakpoint'`。若底层仍在运行、剩余时间为正且小到 `time + remaining == time`，只有实际时间已达到目标并在容差内才主动暂停，返回 `completion='timer_roundoff_pause'` 和 `timer_remaining_seconds`。该处理额外检查 VSMDEBUG.DLL 指纹；时间读取要求已验证的 WINCORE.DLL。部分续跑有 2.5 ms 调度粒度，默认允许超出 3 ms，详见 [仿真接口说明](api/simulation_research.md)。
- **兼容性**：尚未完成不同机器、不同 Proteus 构建和长期批量运行的全面测试。

## 文档

- [API 使用说明](api/README.md)：原理图编辑、电源、固件、按钮与开关、仿真和波形示例。
- [功能覆盖与测试结果](api/IMPLEMENTATION.md)：各项功能的测试范围及结果文件。
- [器件库格式](api/device_library_notes.md)、[连接格式](api/connectivity_notes.md)、[工程文件格式](api/file_format_notes.md)：文件结构与实现说明。

## 开源协议

本项目原创代码和文档采用 [MIT License](LICENSE)。

第三方项目、Proteus 程序、器件模型及样例不在本项目的 MIT 授权范围内，适用各自的许可证。
