# 仿真接口与实测边界（Proteus 8.16，本机）

`simulation.py` 提供 `Simulation(session)`，只控制这个 Session 自己创建的 PID。
已实现 start / pause / stop / reset / status、外置固件配置与原生 SDF 回读、
嵌入固件提取、Simulation Log 读取、STM32 GPIO 驱动事件提取、按钮和开关操作及按仿真时间运行。
不依赖 Computer Use、OCR、桌面坐标或外部 Python 包。

```python
from proteus_automatic_api import Session, Simulation

session = Session("your-copy.pdsprj")
sim = Simulation(session)
sim.set_firmware("U1", "firmware.elf")
session.set_properties("U1", TRACE_GPIO="3")
session.save()
run = sim.run_for(1.25, tolerance_seconds=0.003)
events = sim.gpio_events(ref="U1", port=0, pin=5)
sim.stop()
session.close()
```

`run_for` 使用 Debug → Run Simulation (timed breakpoint) 的真实定时断点。
返回实际 start/end/elapsed、requested、overshoot、原始原因及 `completion`。
本机部分续跑会多执行一个 2.5 ms 仿真步，因此默认容许 3 ms，超差抛异常；
这不是绝对精确计时。参数必须是至少 1 µs 的整数微秒。等待的墙钟 timeout 只
用于防止调用挂住，不参与计算仿真时间。正常完成要求达到目标，并出现新的
`Execution timer breakpoint` 原因，返回 `completion='timer_breakpoint'`。
`reset()` 等同停止后重新初始化；保留持久化器件存储。

Proteus 的定时器在连续短时运行后可能留下正的浮点残余。例如实际时间为
0.03 秒、剩余量为 `1.734723475976807e-18` 秒时，双精度加法仍得到
`time + remaining == time`。底层保持运行状态，却无法使剩余量降到零，因此
不会生成定时断点原因。仅当原生状态仍为运行、剩余量为正、满足上述加法条件，
且实际时间已达到本次目标并在容差内时，`run_for()` 主动调用原生暂停，再读回
ISIMCTRL 确认暂停。结果为 `completion='timer_roundoff_pause'`，并以
`timer_remaining_seconds` 保留暂停前的残余量；不会补造断点原因。
单凭时间没有变化、菜单状态或等待时长均不能触发这一处理。

实际时间通过只读原生 status widget 获得。WINCORE.DLL SHA-256 必须是
`944013e93f0d31addbbce797d6eb3746bf06c7497c0d04cb77955fc0acd569bb`。
在自有 PID 主窗口的 `LX_APPLN_NoDC` / control ID 3 中，32 位窗口额外数据
指向状态对象；对象 +4 必须匹配这个 HWND，+0x250 / +0x254 分别指向状态和原因。
只读取固定字段和最多 256 字节文本，不扫描进程，也不写进程内存。
不同二进制版本拒绝按时运行，status 的 simulation_seconds 为 None。
界面在 1 秒前使用 `0.010000000s`，1 秒后使用 `00:00:01.250000`；两者均已处理。
菜单 enable bits 在启动/暂停时可能延迟更新，不能单独证明底层已暂停。
正常完成使用时间及新 timer reason；浮点残余分支额外读取原生定时器状态。
数字 Edit 只用 WM_SETTEXT 会改变显示而不更新绑定数值；这里使用
目标控件 WM_CHAR，读回输入字符串，再确认原生断点实际时间。

`interactive_native.snapshot(session, timer=True)` 是只读定时器适配器。
它复用进程和模块校验，额外要求 VSMDEBUG.DLL SHA-256 为
`b75f01b89b7acb2a3c0c1a3c92244e86b67d242a851ca3a158cc87d34c0322fa`。
ISIS.DLL、NETLIST.DLL 和 PRIMS.DLL 也必须匹配适配器中的指纹。
Schematic Capture 子窗口的原生对象减去 `0x5c8` 得到 editor，
editor 的 `+0x1bcc` 指向 ISIMCTRL；每一步检查 HWND 归属和对象虚表。
ISIMCTRL 的 `+0x1254` 为状态，`+0x1278` 为定时器剩余秒数，`+0x1280` 为当前秒数。
状态 0 表示运行，3 表示暂停，4 表示停止；还需检查底层引擎是否存在。
这些偏移只适用于已验证构建，不作为跨版本接口承诺。

`controls()` 和 `control_state()` 在连续仿真中会短暂停住，读取一致的控件快照
后恢复运行。它们读取机械位置或逻辑输入状态，不代表下游电压。
`press()`、`release()`、`set_switch()` 操作前需通过 `Circuit.bind_controls()`
绑定、保存并重新打开工程，具体设备和键槽范围见 [API 使用说明](README.md#按钮开关与逻辑输入)。

`log()` 激活原生 Simulation Log，使用官方提供的 Copy All，读取 CF_UNICODETEXT。
**该操作会替换系统剪贴板。** 读取时锁住剪贴板，并确认 owner PID 是当前 Session，
不会把其他进程的剪贴板内容当作日志。VSM Studio 中无独立原生 HWND 的 dock 会
明确失败。GPIO 解析目前针对 CM3 的 `[GPIO] drive PIOx.y = n [REF_SYSINTERFACE]`
格式；是数字驱动状态，不是引脚电压。事件时间来自 Proteus，最后事件时间不是
当前仿真时间。`errors()` 返回关键字匹配的原始诊断以及完整日志，不保证机器判定
所有模型的诊断级别。

`firmware_info` 检查非空文件、ELF 魔数/架构号或 Intel HEX 长度/校验和/EOF；
不能仅凭这些检查证明固件适合某一 MCU。`extract_firmware` 明确选择 ZIP 中的
固件成员并保留扩展名，不执行构建脚本。带 FIRMWARE.XML 的 VSM Studio 项目可能
重新生成 PROGRAM 路径。实测使用官方 STM32 样例的独立副本，移除副本中的嵌入
构建项，再把同一份 Debug.elf 提取为外置文件；原始样例未改动。

可运行 `py -3.12 api/check_simulation.py` 复核。它生成自己的副本与 PID，验证
STM32F103R6 的 PROGRAM、GPIO 高低事件（含超过 1 秒的 HH:MM:SS 时间）、定时
续跑、reset 后从零开始、start/pause/stop，并保存原始日志与 JSON 报告。
只有全链路通过且进程正常退出才写 `result.json`；失败保留 `check.json`。

2026-09-12 的通过证据：`artifacts/simulation-check-a7ea5ae3/result.json` 与同目录
`simulation.log`（PID 8188 正常退出码 0）。实际 0 → 0.010000 → 1.250000 秒，
reset 后回到 0 → 0.010000 秒。PA5 驱动事件为 0.004620500s=0、0.004626375s=1、
0.511087250s=0、1.011087s=1。另有重复半秒运行观测到 2.5 ms 超出量，接口明确返回。

0.2.0 源码交互检查覆盖六类控件、0–9 键槽、绑定冲突、未知 DLL 构建拒绝、
运行状态恢复和定时器浮点残余处理：[检查结果](artifacts/interactions-add09d8a/result.json)。
真实 STM32 按钮检查通过公开接口驱动 BUTTON → PA0 → 固件 → PA5，PA5 事件为
0@0.002507875s、1@0.011635125s、0@0.021464375s：
[结果](artifacts/button-mcu-d759e93c/result.json)、[原生日志](artifacts/button-mcu-d759e93c/simulation.log)。
固件与构建文件位于 [button_mcu](examples/button_mcu)。

```powershell
py -3.12 -B api/check_control_bindings.py  # 离线绑定检查，不启动 Proteus
py -3.12 -I api/check_interactions.py     # 六类控件的真实电气响应
py -3.12 -I api/check_button_mcu.py       # STM32 按钮与固件响应
```

本机官方证据在 `%TEMP%/proteus-interface-audit-20260912`：

- `ISIS/DIALOGUES/DebugDlgs/Debug_EXCT_SPEC_TME.htm`：按仿真秒数执行后停止。
- `LISA/MICROPROCESSORS/Diagnostics.htm`：Simulation Advisor 的 Copy All 上下文菜单。
- `CM3/Properties.htm`、`CM3/Loaders.htm`：PROGRAM 支持 ELF / HEX。
- `CM3/Toolchain.htm`：CM3 固件可向 0xFFFFFF00 写零结尾文本，输出到 Simulation Log。
- `VSMSDK/VSMSDK.htm`：安装包只有申请 SDK 的说明，没有可直接使用的 SDK 头文件。

运行时输入控制限于文档列出的六类二态器件，不提供任意 GPIO 外部强制输入。
模拟测量由 `graphs()`、`export_graph()` 和 `sample_graph()` 读取已有图表的电压、
电流及波形；专用信号源由 `set_generator_properties()` 修改支持的参数。
这些接口要求已有图表、探针或信号源，并各自保留格式限制，详见 [测量示例](README.md#输入参数电压电流与波形)。
