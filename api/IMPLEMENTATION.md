# 基础库实施与验收

2026-09-12，Windows，Proteus 8.16 SP3（8.16.36097）。统一入口 `proteus_api`，包名 `proteus-native`，版本 0.2.0。
运行依赖为 Python 标准库和已安装的 Proteus；库通过原生文件和 Windows 接口工作，不依赖 computer-use、OCR 或截图定位。

| 基础能力 | 本机实际验收 | 证据 |
|---|---|---|
| 器件库与添加 | 56,694 条目录；R/C、二极管、三极管、继电器、LED、ULN2003A、ATMEGA328P 原生工程生成与保存重开；官方样例导入 STM32 | [六类新器件](artifacts/device-import-3bbbda14/result.json)、[已有复杂器件修改](artifacts/device-edit-3f82eedb/result.json)、[样例定义导入](artifacts/donor-import-cd4b6a90/result.json) |
| 元件与连线增删改查 | 变长值/编号/属性、复制、移动、旋转、镜像、删除、断线、重新布线；错误编辑不提交；删改保留共享网端子及幸存支路标签 | [编辑](artifacts/edit-check-6b33bf76/result.json)、[拓扑编辑](artifacts/topology-edits-11de383d/result.json)、[共享网络保留](artifacts/integrity-edits-b9b10ded/result.json) |
| 电源、地、端子与标签 | 六种端子、跨位置同名合网、PWRRAILS、孤立端子、真实 WIRE LABEL、零元件端子工程 | [命名网络](artifacts/net-objects-0925491a/result.json)、[标签与空端子工程](artifacts/label-roundtrip-c2346bda/result.json) |
| 多引脚网络 | 三/四路结点及超过四路的多个结点和总线；桥段真实标签及重命名/移动后保留 | [四路端子修复](artifacts/four_slot_result.json)、[六电容双总线](artifacts/six_cap_bus_result.json)、[桥段标签编辑](artifacts/multi-junction-8d274f26/result.json) |
| 已有工程与保存 | 保留已识别连线形状、端子及电源；空工程/删到空；原子保存，拒绝陈旧输入覆盖；原生 ADI 修改后可继续文件编辑 | [增量与保存](artifacts/incremental-64d8d344/result.json)、[原生属性与文件读写衔接](artifacts/native-property-roundtrip-7be9f18a/result.json) |
| 固件与仿真控制 | ELF/HEX 检查，提取内嵌固件，PROGRAM 回读；启停暂停/复位；读取实际仿真时间及定时断点 | [原生仿真](artifacts/simulation-check-a7ea5ae3/result.json)、[格式边界](artifacts/simulation-offline-81effc15/result.json) |
| 数字与模拟量读回 | STM32 PA5 高低事件及真实时间戳；输入幅值 1→2，清缓存重新仿真，电压/电流/波形同步变化约 2 倍；VSOURCE 支持变长值 | [GPIO 日志](artifacts/simulation-check-a7ea5ae3/simulation.log)、[新仿真测量](artifacts/measurement-check-c048d7fb/result.json)、[直流源](artifacts/source-devices-fb74e7b6/result.json) |
| 按钮与开关（0.2.0 安装包） | 六类控件的输入和探针响应、0–9 键槽、冲突与未知构建拒绝、运行状态恢复、定时器浮点残余处理 | [交互检查](artifacts/interactions-5e95f58e/result.json) |
| STM32 按钮输入（0.2.0 安装包） | BUTTON → PA0 → 固件 → PA5；PA5 在 0.002507875s、0.011635125s、0.021464375s 分别为 0、1、0 | [结果](artifacts/button-mcu-aa66411e/result.json)、[原生日志](artifacts/button-mcu-aa66411e/simulation.log) |
| 可安装库（0.2.0） | wheel 构建、独立环境 `python -I` 导入并完成工程编辑、固件加载、GPIO 和模拟量读取；同时安装至本机 Python 3.12 | [安装包验证](artifacts/installed-447ced83/result.json)、[构建清单](../dist/build_manifest.json) |

所有原生检查使用独立副本和自行启动的 PID。失败研究探针保留用于诊断，不算通过证据。

安装包：`dist/proteus_native-0.2.0-py3-none-any.whl`。本机可直接 `from proteus_api import Circuit, Session, Simulation`；独立环境位于 `.venv-api`。`py -3.12 -I api/check_installed.py` 可复验已安装版本，`-I` 避免源码目录掩盖打包缺失。

## 按钮与开关交互

`Circuit.bind_controls(*refs)` 在保存工程前分配原生 `INC` / `DEC` 绑定。每个二态控件需要两个全局执行器键槽，Proteus 共提供 0–9 十个键槽，因此最多配置 5 个控件。其他元件已有的 `INC`、`DEC`、`KEY` 绑定会减少容量；容量不足时不提交修改。修改绑定后必须保存并重新打开工程，才能更新 Proteus 的运行时绑定缓存。

公开交互接口为 `Simulation.controls()`、`control_state(ref)`、`press(ref)`、`release(ref)` 和 `set_switch(ref, closed)`。支持范围限定为 `BUTTON`、`SW-SPST`、`SW-SPST-MOM`、`SWITCH`、`LOGICSTATE`、`LOGICTOGGLE`，且必须是具有两个状态的内置执行器。多状态控件、外部 DLL 交互模型和任意引脚强制输入尚不支持。

状态操作通过当前 Session 窗口的原生命令完成。对正在运行的仿真，先暂停，确认控件状态修改后再恢复；对暂停的仿真，保留暂停状态。操作返回 `ref`、`state`、`changed` 和 `command_time_seconds`。最后一个字段表示操作所在暂停点的实际仿真时间；它不表示下游电路已经完成响应。目标状态未变时，`changed=False`，不会重复派发命令。

`control_state()` 从原生组件属性读取机械位置或逻辑输入状态，不导出网表，也不读取下游电压。`controls()` 和 `control_state()` 在连续运行时短暂停住，读取一致的快照后恢复运行。只读适配器以 `0x410` 权限打开当前 Session 的进程，检查进程和工程窗口归属、模块路径、DLL 指纹、对象类型及字段边界，最后关闭句柄。公开结果不包含进程地址。

交互适配器只接受已验证 Proteus 8.16 的 `ISIS.DLL`、`NETLIST.DLL`、`PRIMS.DLL` 指纹；仿真时间还要求已验证的 `WINCORE.DLL`。不匹配时明确报错，不使用屏幕操作替代。接口和保存顺序示例见 [API 使用说明](README.md#按钮开关与逻辑输入)。

交互检查命令分别覆盖离线绑定、六类控件和真实 STM32 固件输入：

```powershell
py -3.12 -B api/check_control_bindings.py
py -3.12 -I api/check_interactions.py
py -3.12 -I api/check_button_mcu.py
```

STM32 检查使用 [button_mcu](examples/button_mcu) 中的 BUTTON → PA0 → 固件 → PA5 示例。上表中的 0.2.0 交互记录使用 `-I` 从已安装的库导入，验证了 wheel 的运行结果。

## 定时运行的完成判定

`run_for()` 保留 Proteus 原生定时断点。正常暂停需要新的 `Execution timer breakpoint` 原因及达到目标的实际时间，返回 `completion='timer_breakpoint'`。

定时器可能保留一个正的浮点残余，使 `time + remaining == time`，底层仍处于运行状态。这种情况下，仅在实际时间已达到目标且超出量符合容差时主动调用原生暂停，再从 ISIMCTRL 确认暂停。返回 `completion='timer_roundoff_pause'` 和处理前的 `timer_remaining_seconds`。该分支不依赖墙钟等待或“时间没有变化”的推测，并额外检查 VSMDEBUG.DLL 指纹。详细字段依据见 [仿真接口说明](simulation_research.md)。

## 当前边界

- 结构编辑面向已识别的单用户页、CDB v7、FILEVER 840/847；不支持的对象/多单元器件/多页结构会拒绝整体重写。目录可搜索不等于每种模型都已验证。
- 布线采用有限的正交路径和总线布局，不提供完整避障自动布局；不可布线的放置会报错。
- 模拟量导出要求工程已有图表/探针；专用 generator 文件编辑目前要求等字节长度。仿真运行中的任意引脚强制输入、任意位置实时模拟探针和图表创建尚未实现。
- GPIO 日志读取验证的是 CM3 格式；其他 MCU 日志需要对应解析。日志读取会替换系统剪贴板。
- 仿真时间读取绑定本机验证过的 WINCORE 指纹；定时续跑可能出现 2.5 ms 的本机调度粒度，接口返回实际时间及超出量，默认容差 3 ms，可配置。

接口用法见 [README](README.md)，内部格式依据见 [连接格式](connectivity_notes.md)、[器件库](device_library_notes.md)、[仿真](simulation_research.md)。
