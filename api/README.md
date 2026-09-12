# proteus-native

用 Python 读写真实 Proteus 工程、调用原生网表编译器、控制仿真以及操作按钮和开关。版本 0.2.0，测试环境为 Windows / Proteus 8.16 SP3（8.16.36097）。运行时仅依赖 Python 标准库及已安装的 Proteus，不依赖 computer-use 或屏幕坐标。仿真由真实 Proteus 进程执行。

## 安装

```powershell
py -3.12 -m pip install .
```

在仓库根目录执行安装命令。默认程序为 `D:\Proteus\BIN\PDS.EXE`，器件库为 `C:\ProgramData\program\LIBRARY`，初始模板是官方 `Rescap.pdsprj`。路径参数与限制见[安装和配置](../README.md#配置)。这个包不分发 Proteus 软件或器件库。

## 新建、添加与连接

```python
from proteus_api import Circuit, Library

catalogue = Library()
print(catalogue.search('ATMEGA328'))
print(catalogue.get('ATMEGA328P')['pins'])

c = Circuit()
c.add('RESISTOR', 'R1', '10k', 0, 0)
c.add('CAPACITOR', 'C1', '100n', 2540000, 0)
c.connect('R1.2', 'C1.2')
c.import_device('ATMEGA328P')
c.add('ATMEGA328P', 'U1', 'ATMEGA328P', 10160000, 0)
c.save('circuit.pdsprj')
```

`add_component` 是 `add` 的同义接口。`Library.search/get` 提供名称、库来源、引脚、默认参数、模型和封装；目录可查不等于所有器件结构都支持导入。目前已实际生成并保存重开 R/C、1N4007、2N2222、RELAY、LED-YELLOW、ULN2003A、ATMEGA328P。

坐标为原生整数单位：**100000 = 1 mm**，Y 向上；元件位置指模板原点，不保证是中心。用 `pins(ref)` 获取引脚世界坐标和出线方向。旋转参数以度计，支持 90° 的整数倍。

## 编辑已有工程

```python
from proteus_api import Circuit

c = Circuit.open('circuit.pdsprj')
c.update('R1', value='22k')
c.set_properties('R1', API_NOTE='updated')
c.move('R1', -2540000, 2540000)
c.rotate('R1', 90)
c.mirror('R1', 'x')
c.rename('C1', 'C_FILTER2')
c.copy_component('R1', 'R2', 7620000, 2540000)
c.delete_component('R2')
c.save('revised.pdsprj')
# 明确要求覆盖时：c.save('circuit.pdsprj', overwrite=True)
```

`Circuit()` 新建；`Circuit.open(path)` 保留已识别原对象。空工程、删到空工程、变长编号/值/参数均支持。保存先校验完整临时文件，再原子替换；覆盖输入工程时检查是否被其他程序修改。未知对象/未支持结构会拒绝整体重写，避免悄悄删除内容。

| 操作 | 接口 |
|---|---|
| 元件、引脚、网络查询 | `components()`、`pins(ref)`、`connections()`、`nets()` |
| 连线与分支 | `connect('R1.2', 'C1.2')`；共享引脚自动形成真实结点 |
| 手工两端折线路径 | `connect(..., points=[(x,y), ...])` 或 `set_route(first, second, points)` |
| 断开 | `disconnect(first, second)` 删除指定连接；`disconnect(pin)` 脱离网络 |
| 值、位置、朝向、属性 | `update`、`move`、`rotate`、`mirror`、`set_properties` |
| 编号、复制、删除 | `rename`、`copy_component`、`delete_component` |

`nets()` 根据显式连线、端子和真实导线标签计算；`nets(physical=True)` 只看物理连线。隐藏电源和模型规则以 `Session.netlist()` 原生网表为准。删除/断开某个元件引脚时，仍有其他引脚的共享网保留端子和支路标签；单独断开端子用 `update_terminal(id, pin=None)`。自动布线采用有限的正交路径，复杂布局可能需要调整位置；手工路径可用 `set_route(first, second, None)` 恢复自动路由。

## 电源、地和网络端子

```python
# 支持 ground / power / label / input / output / bidir
terminal_id = c.add_terminal('ground', '', 'C_FILTER2.1')
c.update_terminal(terminal_id, name='GND')
print(c.terminals())
c.configure_power({'GND': 0, 'VCC': 3.3}, {'VDD': 'VCC', 'VSS': 'GND'})
print(c.power_rails())
# 独立端子可先放置、之后再连接
port = c.add_terminal('input', 'ENABLE', position=(0, -5080000))
# c.update_terminal(port, pin='U1.PB0')  # 引脚名以 pins('U1') 为准
# c.delete_terminal(port)
```

同名端子可跨位置合网；`ground` 的空名称由 Proteus 解释为 GND。已有真实导线标签保留其原类型、名字和显示属性。新建命名网络可使用 `kind='label'` 的标准端子。

## 原生工程会话

```python
from proteus_api import Session, Simulation

s = Session('revised.pdsprj')
netlist = s.export_netlist('revised.sdf')
s.set_properties('R1', VALUE='47k')  # 原生 ADI 修改并用 SDF 回读确认
s.save()
s.close()
```

每个 `Session` 启动并只控制自己的 Proteus 进程。修改后先 `save()` 再 `close()`；未知对话框会报告错误，不自动丢弃未保存内容。原生会话通过 Windows 消息和菜单命令工作，不需要按屏幕坐标点击。

## 仿真和 GPIO 读回

下面使用已经放置 STM32F103R6（编号 U1）的工程副本和外置 ELF。固件必须适合工程中的 MCU。

```python
from proteus_api import Session, Simulation

s = Session('stm32_external.pdsprj')
sim = Simulation(s)
sim.set_firmware('U1', 'blink.elf')  # 路径/文件检查，PROGRAM 原生回读
s.set_properties('U1', TRACE_GPIO='3')
s.save()

run = sim.run_for(1.25, tolerance_seconds=0.003)
print(run['start_seconds'], run['end_seconds'], run['overshoot_seconds'])
print(sim.gpio_events(ref='U1', port=0, pin=5))
print(sim.status())
print(sim.errors()['messages'])
sim.stop()
s.close()
```

`start()` 连续运行，`pause()` 暂停，`stop()` 结束；`reset()` 停止当前运行，下次启动重新初始化，保留器件的持久存储。`run_for()` 使用原生仿真定时断点，返回请求时长、实际起止时间、实际运行时长和超出量。参数必须是至少 1 µs 的整数微秒。本机部分续跑存在 2.5 ms 的调度粒度，默认接受最多 3 ms 超出量，超过会抛错；可显式设置 `tolerance_seconds=0` 要求严格检查。墙钟 `timeout` 只限制等待，不计作仿真时间。

正常完成时，接口检查新的原生定时断点原因和实际时间，并返回 `completion='timer_breakpoint'`。Proteus 的浮点计时可能留下无法继续推进时钟的正残余。仅当底层仍在运行、`remaining > 0`、`time + remaining == time`，且实际时间已达到目标并在容差内时，接口才主动调用原生暂停并确认暂停状态。这种完成返回 `completion='timer_roundoff_pause'`，同时保留 `timer_remaining_seconds`。时间暂时不变本身不会被视为完成。

原生时间读取绑定已验证的 WINCORE.DLL 指纹，浮点残余处理还需验证 VSMDEBUG.DLL 及原生对象适配器所用的 ISIS.DLL、NETLIST.DLL、PRIMS.DLL；不匹配时会报错，不用估算时间代替。`log()`、`gpio_events()` 和 `errors()` 通过原生 Simulation Log 的 Copy All 读取文本，**会替换系统剪贴板**；读取时校验剪贴板归属当前 Session。VSM Studio 中没有独立原生窗口的日志 dock 尚不支持。GPIO 解析目前验证 STM32 的 CM3 日志格式，返回数字驱动状态；最后一条事件的时间不是当前仿真时间。

`firmware_info(path)` 检查文件、ELF 头或 Intel HEX 校验和；`extract_firmware(project, destination, member=...)` 提取明确选择的嵌入固件，不执行构建脚本。带 FIRMWARE.XML 的 VSM Studio 工程可能重写 PROGRAM 路径，外置固件工作流与细节见[仿真接口说明](simulation_research.md)。

原生检查通过固件配置、0 → 0.01 → 1.25 s 续跑、PA5 高低事件、reset、启停暂停和正常退出：[结果](artifacts/simulation-check-a7ea5ae3/result.json)、[原生日志](artifacts/simulation-check-a7ea5ae3/simulation.log)。纯格式检查验证 HEX/ELF 错误边界及秒/HMS 时间格式：[离线结果](artifacts/simulation-offline-81effc15/result.json)。

```powershell
py -3.12 api/check_simulation.py --offline  # 不启动 Proteus
py -3.12 api/check_simulation.py            # 官方样例副本与自有进程
```

## 按钮、开关与逻辑输入

交互接口支持 `BUTTON`、`SW-SPST`、`SW-SPST-MOM`、`SWITCH`、`LOGICSTATE` 和 `LOGICTOGGLE` 六类二态器件。工程需已有连线、电源和适合的仿真模型；单片机工程还需配置固件。

先在文件编辑阶段调用 `Circuit.bind_controls(*refs)`，保存后再启动 `Session`。以下工程中 `SW1` 是已经连接好的 `BUTTON`：

```python
from proteus_api import Circuit, Session, Simulation

circuit = Circuit.open('button_circuit.pdsprj')
circuit.bind_controls('SW1')
project = circuit.save('button_bound.pdsprj')

s = Session(project)
sim = Simulation(s)
print(sim.controls())
sim.run_for(0.01)
pressed = sim.press('SW1')
sim.run_for(0.1)
released = sim.release('SW1')
print(sim.control_state('SW1'))  # 0
print(pressed['command_time_seconds'], released['command_time_seconds'])
sim.stop()
s.save()
s.close()
```

绑定使用 Proteus 原生 `INC` / `DEC` 全局执行器键槽。键槽编号为 0–9，每个控件占两个，因此最多绑定 5 个控件。`bind_controls('SW1', 'SW2')` 可同时配置多个控件；其他元件的已有 `INC`、`DEC` 或 `KEY` 绑定会占用键槽。可用数量不足时会报错，文件中的元件配置保持不变。目标控件会重新分配 `INC` / `DEC`，并清除其 `KEY` 绑定。

**修改绑定后必须保存并重新打开工程。** 已打开的 Proteus 会缓存绑定，仅通过 ADI 修改属性不足以刷新该缓存。应结束使用旧工程的会话，再打开保存后的工程。

| 接口 | 行为与返回值 |
|---|---|
| `controls()` | 列出支持的控件，返回 `ref`、`device`、`state`、`kind` 和 `bound`；`kind` 为 `momentary` 或 `latched`，`bound` 表示已加载 INC/DEC 绑定 |
| `control_state(ref)` | 只读返回控件当前的 `0` / `1` 状态，不导出网表 |
| `press(ref)` | 将控件设为 `1` 并保持，直到调用 `release` 或再次修改状态 |
| `release(ref)` | 将控件设为 `0` |
| `set_switch(ref, closed)` | `closed` 必须为 `True` 或 `False`，分别对应状态 `1` 和 `0` |

修改状态前，仿真必须处于运行或暂停状态。连续运行时，接口会暂停、提交状态并确认读回，再恢复运行；原本暂停的仿真保持暂停。未绑定、不受支持或键槽冲突的控件会报错。

`controls()` 和 `control_state()` 在连续运行中也会先短暂停住，读取一致的原生快照后恢复运行。原本暂停或停止的仿真不会因读取而启动。

三种修改接口均返回 `ref`、`state`、`changed` 和 `command_time_seconds`。`changed=False` 表示控件已经处于目标状态，没有重复发送命令。`command_time_seconds` 记录操作所在暂停点的实际仿真时间；它不是下游信号变化或 MCU 响应的时间戳。

`control_state()` 读取的是机械位置或逻辑输入状态，不是下游引脚电压。电路传播、开关转换时间和固件响应需要继续仿真后单独测量，例如读取 MCU GPIO 日志。按压时长应使用前后实际仿真时间计算；`run_for()` 的调度粒度和容差仍然适用。

状态读取和命令派发针对当前 `Session` 的进程，不依赖 computer-use、OCR 或鼠标位置。接口检查已验证 Proteus 8.16 的 `ISIS.DLL`、`NETLIST.DLL` 和 `PRIMS.DLL` 指纹；其他构建会被拒绝。仿真时间读取还受 `WINCORE.DLL` 指纹限制。任意引脚强制输入、多状态旋钮及外部 DLL 交互模型不在这些接口的支持范围内。

[button_mcu 示例](examples/button_mcu)包含读取 STM32 PA0 并驱动 PA5 的固件源码、HEX 和构建文件。[check_button_mcu.py](check_button_mcu.py)在官方样例副本中连接 BUTTON 与 PA0，通过按下、保持、松开操作检查 PA5 GPIO 响应。

```powershell
py -3.12 -B api/check_control_bindings.py  # 离线分配、错误边界和保存重开检查
py -3.12 -I api/check_interactions.py     # 六类控件的原生电气检查
py -3.12 -I api/check_button_mcu.py       # BUTTON → PA0 → 固件 → PA5
```

0.2.0 源码已通过上述原生交互检查：[六类控件结果](artifacts/interactions-add09d8a/result.json)。STM32 检查中 PA5 在 0.002507875s 为 0、0.011635125s 为 1、0.021464375s 为 0，与松开、按下、松开的操作对应：[结果](artifacts/button-mcu-d759e93c/result.json)、[原生日志](artifacts/button-mcu-d759e93c/simulation.log)。这些时间是固件驱动输出的事件时间，与 `command_time_seconds` 分别记录。

## 输入参数、电压、电流与波形

```python
from proteus_api import Session, graphs, export_graph, sample_graph, set_generator_properties

source = r'C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj'
project = set_generator_properties(source, 'amplitude2.pdsprj', 'INPUT', AMP='2')
s = Session(project)
print(graphs(s))
data = export_graph(s, 'ANALOGUE ANALYSIS', 'waveforms.csv', simulate=True)
print(sample_graph(data, 'R1(2)', 0.00025))  # 在导出采样点间作线性插值
print(sample_graph(data, 'ICAP', 0.00025))
s.save()
s.close()
```

`simulate=True` 会清除该图的旧结果并触发新的原生仿真；`False` 只导出现有结果。返回原始轴与所有数值序列，重复列名也完整保留，可用 `occurrence` 选取同名列。`sample_graph` 不向采样范围外推。

目前要求工程已有图表和探针；专用信号发生器支持等字节长度参数修改，其他长度明确拒绝。这条输入通道已实测：正弦幅度 1→2，重新计算后输入电压、节点电压、电容电流均约为原来的两倍，见 [实际数据](artifacts/measurement-check-c048d7fb/result.json)。普通 `VSOURCE` 元件另支持导入及变长值，如 1V→12.345V；`VPULSE` 实例格式暂不支持，会在导入时拒绝。GPIO 数字事件与模拟电压是两种独立读数。

## 范围和验证

结构写入针对已识别的单用户页、CDB v7、FILEVER 840/847 布局。多单元器件、多页/层次原理图、未知绘图/图表对象不是通用重写范围。`Project` 还保留更保守的同长度修改接口，用于尽量保留不认识的字节。原生仿真读数有独立版本限制，以各接口返回的能力状态为准。

```powershell
py -3.12 api/check_project.py
py -3.12 api/check_components.py
py -3.12 api/check_sdf.py
py -3.12 api/check_edit.py
py -3.12 api/check_incremental.py
py -3.12 api/check_device_library.py
py -3.12 api/check_sources.py
py -3.12 api/check_multi_junction.py
py -3.12 api/check_integrity_edits.py
py -3.12 api/check_measurements.py
```

检查结果与真实 `.pdsprj`、`.sdf` 留在 `api/artifacts/`。只统计带通过 `result.json` 的检查；目录里的失败研究探针不代表已实现能力。检查使用样例副本及自有进程。
