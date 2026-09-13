# 器件库读取与导入的已验证范围

`Library()` 直接读取本机 `C:\ProgramData\program\LIBRARY` 的 DEVICE LIBRARY v400 目录，当前安装共 56,694 个器件目录条目。`search()` 按器件名/库名查找，`get()` 返回完整来源、器件描述、引脚表、默认属性、模型字段、封装与引脚映射文本。跨库同名器件必须指定库名，例如 `1N4007` 不能仅凭名字选择。

```python
from proteus_automatic_api import Circuit
from device_library import Library

library = Library()
print(library.search("ATMEGA328P"))
print(library.get("ATMEGA328P", "AVR2"))

circuit = Circuit()
circuit.import_device("ATMEGA328P", "AVR2")
circuit.import_device("RELAY", "ACTIVE")
circuit.add_component("ATMEGA328P", "U1", "ATMEGA328P", 0, 0)
circuit.add_component("RELAY", "RL1", "12V", 7620000, 0)
circuit.save("controller.pdsprj")
```

也可从已有工程导入完整嵌入器件定义及其图形依赖，再在干净单页工程中创建新实例：

```python
circuit = Circuit()
circuit.import_from_project(
    r"C:\ProgramData\program\SAMPLES\VSM for Cortex M3\STM32\STMCubeMX LED Blink\STMCubeMX LED Blink.pdsprj",
    "STM32F103R6",
)
circuit.add_component("STM32F103R6", "U_MCU1", "STM32F103R6", 0, 0)
circuit.save("stm32.pdsprj")
```

导入会重建嵌入定义目录、组件记录、CDB 实例/属性记录和绝对偏移；保留模型属性、图形依赖与真实引脚几何，不复制 donor 的已有实例或连线。动画器件的附加图形记录也保留。实例 `value` 与模型已有 `VALUE=` 属性同步，隐藏属性在回读与保存后保持隐藏。

真实 Proteus 8.16 的 SDF→保存→关闭→重新打开→SDF 已验证：

- 从 LIB 添加 `1N4007`、`2N2222`、`RELAY`、`LED-YELLOW`、`ULN2003A`、`ATMEGA328P`，6 个器件共 57 个逻辑引脚。证据：`artifacts/device-import-3bbbda14/result.json`。
- 读取该原生保存工程，将继电器由 12V 改为 5V、移动 MCU，所有未编辑属性显示文本、器件引脚网络和原源文件保持一致。证据：`artifacts/device-edit-3f82eedb/result.json`。
- 从官方 Arduino relay 样例导入 ATMEGA328P/ULN2003A/RELAY/LED-YELLOW，以及从 STMCubeMX 样例导入 STM32F103R6。证据：`artifacts/donor-import-cd4b6a90/result.json`。

当前导入器支持已解码的单单元器件；未知、多单元记录或缺失图形依赖会明确拒绝。目录能检索到的器件不等于全部都已通过创建或仿真验证。上述证据验证了原生工程、引脚与模型选择；具体 MCU 固件运行和器件电气行为需另外进行运行测量。封装 pinout 文本保留，但不声称已实现 PCB 编辑。

普通直流源 `VSOURCE`（`ASIMMDLS` 库）另采用官方样例的 primitive text 布局，已验证从 1V 改为 12.345V 的变长参数、原生 SDF 和保存重开。`check_sources.py` 复验此路径。`VPULSE` 的目录与属性可以读取，但已发现它需要额外的实例格式适配，因此导入时明确拒绝，且不改变 Circuit 状态。

已有图表与发生器的工程可用 `measurements.export_graph()` 触发新仿真并导出数值 CSV，用 `sample()` 按实际轴插值。`check_measurements.py` 对官方 Rescap 副本清空旧结果再分别执行输入幅值 1 和 2 的新仿真；INPUT、R1(2)、ICAP 在 0.25 ms 的数值比均约为 2.000004。证据：`artifacts/measurement-check-c048d7fb/result.json`。当前生成器属性修改仅支持等字节长度，VSOURCE 普通元件的 value 修改不受此等长限制。

`py -3.12 api/check_device_library.py` 可复验六器件；加 `--edit-existing PATH --baseline-sdf PATH` 复验已有六器件工程的修改。`check_catalogue()` 另覆盖歧义、未知名称、错误库、输入类型、目录截断、越界指针、缺失目录和截断定义等边界。
