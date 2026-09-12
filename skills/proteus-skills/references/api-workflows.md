# Proteus Skills：工程、仿真和测量

对应 `proteus-native 0.2.0`。只读取当前任务对应的小节；按钮与开关操作另见 [交互控制](interactive-controls.md)。示例里的默认安装路径应先验证，输出目录和工程名按用户任务设置。Python 运行时仅需标准库及已安装的库。

## 原理图与网表

### 新建、保存、重开

下面是文件与连线示例，没有输入源，不能据此宣称已完成 RC 电气仿真。可保存为脚本执行；断言验证实际保存后的元件和网络。

```python
from pathlib import Path
from proteus_api import Circuit

out = Path("proteus-output").resolve()
out.mkdir(parents=True, exist_ok=True)
template = Path(r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj")
c = Circuit(template_project=template)
c.add("RESISTOR", "R1", "10k", 0, 0)
c.add("CAPACITOR", "C1", "100n", 2540000, 0)
c.connect("R1.2", "C1.2")
c.add_terminal("ground", "", "C1.1")
project = c.save(out / "rc.pdsprj")

loaded = Circuit.open(project)
assert {p["ref"]: p["value"] for p in loaded.components()} == {"R1": "10k", "C1": "100n"}
assert any({("R1", "2"), ("C1", "2")} <= set(net) for net in loaded.nets())
assert not any({("R1", "2"), ("C1", "1")} <= set(net) for net in loaded.nets())
print(project)
```

需要原生确认时，在同一脚本后继续。SDF 的 `nets` 是字典列表，每个网络有 `name`、`pins`、`terminals`、`properties`；每个 pin 有 `ref`、`kind`、`pin`。先看实际 SDF 端点再写断言：MCU 的 `pin` 可能是 `PA0-WKUP` 这样的逻辑名，不能直接拿 `c.pins(ref)` 的 `number` 当作 SDF 引脚字段。普通电阻/电容可按下面的编号对照。

```python
from proteus_api import Session

s = Session(project, executable=r"D:\Proteus\BIN\PDS.EXE")
try:
    data = s.export_netlist(out / "rc.sdf")
    assert {ref: part["value"] for ref, part in data["parts"].items()} == {"R1": "10k", "C1": "100n"}
    nets = [{(pin["ref"], pin["pin"]) for pin in net["pins"]} for net in data["nets"]]
    assert any({("R1", "2"), ("C1", "2")} <= net for net in nets)
    assert not any({("R1", "2"), ("C1", "1")} <= net for net in nets)
    ground = next(net for net in data["nets"] if net["name"] == "GND")
    assert any(pin["ref"] == "C1" and pin["pin"] == "1" for pin in ground["pins"])
    s.save()
finally:
    s.close()
```

`export_netlist()` 与 `export_graph()` 均要求不存在的输出文件；重跑改用新的输出目录。仅需内存网表时用 `s.netlist()`。样例中 `finally` 尝试正常关闭；如果出现未知保存对话框，关闭会报错，按入口中的异常处理规则保留现场。

### 修改已有电路

```python
from proteus_api import Circuit

c = Circuit.open("proteus-output/rc.pdsprj")
print(c.components(), c.connections(), c.terminals())
c.update("R1", value="22k")
c.move("R1", -2540000, 0)
c.rename("C1", "C_FILTER2")
c.update("C_FILTER2", value="220n")
revised = c.save("proteus-output/revised.pdsprj")
assert {p["ref"]: p["value"] for p in Circuit.open(revised).components()} == {
    "R1": "22k", "C_FILTER2": "220n"
}
```

`Circuit.move(ref, x, y)` 是绝对坐标并重路由；`rotate(ref, degrees)` 是增量旋转，支持 90° 的整数倍；`mirror(ref, "x"/"y")` 切换镜像。`add(..., rotation=90)` 指定新实例朝向。打开旧工程后以 `pins()` 的实际世界坐标为准，`rotation_raw` 不是度数。

仅查看某些无法结构重写的工程时，可用 `Project(path).components()`。`Project.set_value()` 只接受等字节长度值；`Project.move(ref, dx, dy)` 是相对位移且**不会移动导线**，不能代替带连线布局编辑。`Project.save(new_path)` 只写新文件。变长原生属性可用 `s.set_properties("R1", VALUE="47k")`，它通过 ADI 修改并从 SDF 回读，随后 `s.save()`。

### 器件、引脚和拓扑

```python
from proteus_api import Library

catalogue = Library()  # 自定义目录仅对查询生效
print(catalogue.search("ATMEGA328", limit=10))
device = catalogue.get("ATMEGA328P")
print(device["pins"], device["models"], device["pin_parse_status"])
# 在已创建的 c 中导入一次，之后可添加多个实例：
c.import_device("ATMEGA328P")
c.add("ATMEGA328P", "U1", "ATMEGA328P", 10160000, 0)
print(c.pins("U1"))
```

同名器件用查询结果的 `library` 消歧。模板已有的器件不要重复导入。需要工程内嵌定义时用 `c.import_from_project(donor_path, device_name)`；它只导入定义及图形依赖，不复制 donor 的电路或固件，也不保证所有器件结构都受支持。

`pins(ref)` 返回含 `name`、`number`、`position`、`direction` 的列表。连接端点支持 `"REF.PIN"` 或 `(ref, pin)`，优先实际逻辑名，也可用 API 能完整匹配的无歧义编号。0.2.0 导入器按选定封装映射引脚，`number` 可能是 `18/31/47/63` 或 `*`；不要拆开组合编号假定每个数字都能作连接端点，也不要拿 `Library.get()["pins"]` 的符号编号代替导入后的映射。编号使用类似 `R1`、`C_FILTER2`、`U1` 的唯一名称；元件值和 ADI 属性使用 API 支持的 ASCII 文本。

| 意图 | 公共 API 与语义 |
|---|---|
| 查元件与网络 | `components()`、`connections()`、`nets()`；`nets(physical=True)` 仅显式物理线网 |
| 连线和分支 | `connect(first, second)`；共享引脚形成真实结点，不重复添加已有连接 |
| 手工两端正交线路 | `connect(first, second, points=[...])` 或 `set_route(first, second, points)`；坐标包含两端实际引脚位置 |
| 恢复自动路由 | `set_route(first, second, None)`；已有手工线路加分支前可能需要此操作 |
| 断开指定连接/脱离线网 | `disconnect(first, second)` / `disconnect(pin)` |
| 属性 | `set_properties(ref, **properties)` 或 `update(ref, value=..., properties={...})` |
| 复制、删除 | `copy_component(ref, new_ref, x, y)`、`delete_component(ref)` |
| 新增端子 | `add_terminal(kind, name, pin)` 返回 ID；kind 支持 ground/power/label/input/output/bidir |
| 独立端子 | `add_terminal(kind, name, position=(x, y))`；之后 `update_terminal(id, pin="U1.PB0")`，引脚以实际查询为准 |
| 修改、断开、删除端子 | `update_terminal(id, name=...)`、`update_terminal(id, pin=None)`、`delete_terminal(id)` |

布线报错时先调整器件位置/朝向、引脚出线方向或使用明确的网络端子；不要无界搜索坐标。同名端子可合网，空名称 ground 对应 GND。电源配置示例：

```python
c.configure_power({"GND": 0, "VCC": 3.3}, {"VDD": "VCC", "VSS": "GND"})
print(c.power_rails())
```

该调用写入整套电源轨配置；修改已有工程时先读取并保留仍需要的轨和映射。它不自动添加物理端子或去耦电容，隐藏电源最终通过 SDF 核对。断开/删除元件引脚可能保留共享网的端子和标签，不能假定端子也被删除。

## 单片机仿真

先在工程副本中确定真实 MCU 编号、型号、时钟及匹配的固件。`firmware_info(path)` 校验 ELF 头/架构号或 Intel HEX 校验和、EOF，不证明固件匹配目标 MCU，也不负责编译。

下面假定 `stm32-work.pdsprj` 已准备好，U1 是匹配固件的 STM32，PA5 为待观察引脚。路径、编号和引脚不能照搬到其他 MCU。

```python
from pathlib import Path
from proteus_api import Session, Simulation, firmware_info

firmware = Path("blink.elf").resolve(strict=True)
print(firmware_info(firmware))
s = Session("stm32-work.pdsprj", executable=r"D:\Proteus\BIN\PDS.EXE")
sim = Simulation(s)
try:
    sim.set_firmware("U1", firmware)
    s.set_properties("U1", TRACE_GPIO="3")  # 已验证 STM32 CM3 的跟踪属性
    s.save()
    run = sim.run_for(1.25, tolerance_seconds=0.003)
    events = sim.gpio_events(ref="U1", port=0, pin=5)
    print(run, events, sim.status())
    print(sim.errors()["messages"])
finally:
    try:
        sim.stop()
    finally:
        s.close()
```

- `start()` 连续运行；`pause()` 暂停；`stop()` 结束；`reset()` 停止后在下次启动重新初始化，保留器件持久存储。
- `run_for(seconds, tolerance_seconds=0.003, timeout=...)` 接受至少 1 µs 的整数微秒时长。返回实际 `start_seconds`、`end_seconds`、`actual_elapsed_seconds`、`overshoot_seconds` 等信息；使用这些值报告实际时长。部分续跑观测到 2.5 ms 粒度；`tolerance_seconds=0` 仅收紧验收，不改善调度精度。
- `completion='timer_breakpoint'` 表示新的原生定时断点已确认。`completion='timer_roundoff_pause'` 表示底层仍运行且有正残余，但 `time + remaining == time`、实际时间已到目标且在容差内；库主动暂停并确认后返回，同时保留 `timer_remaining_seconds`。这是 0.2.0 支持的完成分支，不能把任意卡住状态当作这种完成。
- `timeout` 是墙钟等待上限，不是仿真时长。未知 WINCORE 构建拒绝 `run_for()`，`status()` 的时间可能为 None；浮点残余路径还校验 VSMDEBUG.DLL 及 ISIS.DLL、NETLIST.DLL、PRIMS.DLL。不要修改指纹白名单或内部偏移；具体受限能力以实际错误为准。
- `log()`、`gpio_events()`、`errors()` 读取原生日志时**会替换系统剪贴板**。执行前简短告知这一实际副作用即可，不要因此追加与任务无关的确认流程。
- GPIO 解析目前验证 STM32 CM3 日志；无事件不代表引脚为低。最后一条事件时间不是当前仿真时间。`errors()` 是关键词提取而非完整诊断分级，无匹配不能证明仿真无错误。保存关键原始日志作为行为证据。

如果工程使用内嵌固件，先用标准库 `zipfile.ZipFile(...).namelist()` 确认成员，再调用 `extract_firmware(project, destination, member="实际成员名")`；多个候选时按目标 MCU/配置选择，不猜测。

带 `FIRMWARE.XML` 的 VSM Studio 工程可能重新生成 `PROGRAM`，其日志 dock 也可能没有库可读取的独立窗口。需要外置固件时，在明确用于外置固件的副本中移除对应嵌入构建项，再设置提取出的匹配固件并做 SDF 回读；不修改原项目、不执行内嵌构建脚本。具体样例转换可参考库源码 `api/check_simulation.py` / `api/check_installed.py`；无法确认成员作用时保留项目结构并报告限制。

## 图表与模拟量

测量必须复用有图表和探针的工程。不能先 `Circuit(template_project=graph_project)` 再测量，那会得到新空图。已有图表的工程也可能拒绝 `Circuit.open()`，此时保留原文件结构，用原生属性接口或专用发生器编辑接口。

以下是默认安装的官方 Rescap 样例，图名、发生器名及 trace 名仅适用于这个样例：

```python
from proteus_api import (
    Session, generators, set_generator_properties, graphs, export_graph, sample_graph,
)

source = r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj"
print(generators(source))
project = set_generator_properties(source, "analogue-work.pdsprj", "INPUT", AMP="2")
s = Session(project, executable=r"D:\Proteus\BIN\PDS.EXE")
try:
    print(graphs(s))
    data = export_graph(s, "ANALOGUE ANALYSIS", "waveforms.csv", simulate=True)
    print(data["axis"]["name"], [trace["name"] for trace in data["traces"]])
    print(sample_graph(data, "R1(2)", 0.00025))
    print(sample_graph(data, "ICAP", 0.00025))
    s.save()
finally:
    s.close()
```

`set_generator_properties(source, destination, name, **properties)` 只改已存在的发生器属性，值必须保持相同 ASCII 字节长度，并保存到新路径。先查 `generators(source)`；长度不符时改用界面配置，不能补零、截断或绕过校验。普通 `VSOURCE` 元件与专用 generator 不同，前者可通过元件 API 修改变长值。

`graphs(s)` 列出可用图表名称和索引，`export_graph()` 可按唯一名称或索引选择。默认 `simulate=True` 清除该图旧结果并执行新仿真；`False` 只读缓存，不能用来证明改参数后的行为。若要保留新结果，随后保存会话。

CSV 保留轴和全部 trace，包括重名列；`sample_graph(data, name, x, occurrence=0)` 在线性采样间插值、不外推。轴也可能是频率，不能统一当作时间；数值单位按实际图表配置解释。提供原始 CSV，区分实际采样和插值结果。

## 按任务验证

优先用当前任务脚本里的少量断言，核对目标值、应连接/应分离的引脚或预期行为。文件级断言不能代替原生编译，原生编译不能代替电气行为或视觉检查。

库源码中已有检查可复用，无需为常规任务运行整个套件：

```powershell
py -3.12 -B api/check_sdf.py
py -3.12 -B api/check_measurements.py --parser-only
py -3.12 -B api/check_simulation.py --offline
py -3.12 -B api/check_pin_mapping.py
```

这些格式检查不启动 Proteus。确需验收安装、编辑、固件和测量整条链路时，在库根目录运行 `py -3.12 -I api/check_installed.py`；它需要默认目录的器件库、Rescap 与官方 STM32 Blink 样例，会创建副本、启动自有进程并覆盖剪贴板，结果留在 `api/artifacts/installed-*/`。只有本次成功退出且生成通过的 `result.json` 才能作为本次验收证据。
