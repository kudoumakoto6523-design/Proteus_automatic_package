# DSN/CDB 连通关系研究

本轮只读 Proteus 安装附带的官方原件；所有修改都写到 `api/artifacts/` 副本。

## 后续已打通：新增元件、真实导线和三岔结点

`component_codec.Circuit` 与 `wire_codec.encode_connected_body` 已构成全新 R/C 工程的写入闭环。最终几何修正版证据 `artifacts/circuit-check-b5bf4e3e/result.json` 记录四种电路均通过：RC 多元件链、两条并行连接、三引脚同网分支、三电容并联的上下两个网络。每种都由 Proteus 实际导出 SDF、保存、正常关闭、重开后再次导出 SDF 验证；主任务也已目视三电容并联的上下总线及两个结点。

最终结构与早期推断的区别：

- 二端网络：较早端点保存前向引用；较晚端点的完整 pin 表后放 wire 样式及点数组。
- `0x10` 不是“拥有 wire”所需标志。官方被动 R/C 即使后面带 wire 定义，实例 flag 也是 0；生成器保留模板原值。
- 多引脚网络：每个 pin 分别引用自己的 wire。结点固定 **25 字节**：`0x01 + int32 x/y + 4 个 uint32 wire 引用槽`。三岔最后的零是第 4 个空槽，不是可变数组的结束标记；四岔应直接使用 4 个非零槽。此前对结束标记的推断已由官方 `741.pdsprj` 的四岔记录纠正。
- 先按全部组件和 wire 的真实长度计算布局，再写所有绝对引用，最后更新全局目录和 default sheet 索引。可变长编号、数值及多次添加不依赖原始绝对偏移。

引脚位置和外向方向来自嵌入符号的 anchor/tip 及实例旋转。默认连线试有限正交路径，遵守出线方向；结点选择所有 pin 出线半平面的交集。多于四个引脚或单结点会导致重叠时，尝试水平/竖直总线，以真实结点间导线组成树。仍拒绝共线重合、重复顶点和折返，并不提供完整障碍避让。

2026-09-12 后续验证：`artifacts/four_slot_result.json` 记录三电容引脚加输出端子的四岔修复，`artifacts/six_cap_bus_result.json` 记录六电容双总线和电源端子、共十二个 2/3/4 度结点。两项都通过原生 SDF、保存关闭、API 读取重建、再次原生 SDF/保存关闭。原四岔多写 4 字节确实会导致 Proteus 崩溃，已移除该写法。`net_objects.decode_net_objects` 按四槽读取，树形布局保留节点位置、每个 pin 的路径、节点间导线与真实 WIRE LABEL。

可复跑 `py -3.12 api/check_multi_junction.py`。`artifacts/multi-junction-8d274f26/result.json` 额外覆盖桥段 WIRE LABEL 保真、C1 重命名 CX1、移动后总线重排和标签迁移：编辑前后均实际导出 SDF、保存并正常退出，两网各六个元件引脚、电源端子与 BUS_SENSE 标签均保留。总线重排不会把物理网络之外的同名引脚拉成实体导线。

电源、地、命名端子、输入/输出/双向端口，以及没有元件的独立端子，已通过 `artifacts/net-objects-0925491a/result.json` 和 `artifacts/label-roundtrip-c2346bda/result.json` 的原生保存/重建验证。同名远端网络仅作电气合并，不会在 API 读回时添加虚构物理连线。隐藏电源的 PWRRAILS 配置可保存读取；实际特定 IC 隐藏电源模型行为尚未单独验证。

下面保留早期单变量探针，说明为什么单改坐标和顶层追加 wire 不能实现连接。

## 最小样本和已获得的事实

`C:\ProgramData\program\SAMPLES\Interactive Simulation\Animated Circuits\Comb01.pdsprj` 是一个适合做单变量实验的样本：ROOT.DSN 14,125 字节、ROOT.CDB 546 字节，1 个 AND、2 个 LOGICSTATE、1 个 LOGICPROBE，以及 3 条导线。

由本机 Proteus 8.16 导出的 `artifacts/comb01_baseline.SDF` 给出真实网络：

| 网络 | 引脚 |
| --- | --- |
| 0 | U1.D0 — A-INPUT.Q0 |
| 1 | U1.D1 — B-INPUT.Q0 |
| 2 | U1.Q — Q-OUTPUT.Q0 |

**DSN 同时保存几何折线和显式连接引用。不能把导线理解为只有坐标的独立图形。**

## 已定位的二进制结构

三个导线的样式块偏移分别为 `0x2638`、`0x27D9`、`0x298A`。在这个样本里，每个块前面有 `0x10` 和指向块本身的绝对 32 位文件偏移。这是观察到的 inline 定义形态，`0x10` 不应被推广成所有文件里的“独立导线对象类型”。

样式名 `WIRE\0` 后还有两个零字节、一个 little-endian `uint16` 点数、`点数 × (int32 x, int32 y)`。该样本的点数为 2、4、4。首条输出导线的两个点是 `(1397000, 2032000)`、`(2032000, 2032000)`。

U1 的实例坐标起点位于 `0x24A5`。坐标、旋转、实例编号和一个零字段后，从 `0x24BA` 开始有三个 `uint32`：

| 顺序 | CDB 对应引脚名 | 指向的导线样式块 |
| --- | --- | --- |
| 0 | D0 | `0x27D9` |
| 1 | D1 | `0x298A` |
| 2 | Q | `0x2638` |

每个指针都确实落在上述 WIRE 块开头。原生验证已证明：只修改坐标后，导出的 SDF 完全不变；电气连接不能靠几何重算获得。直接把一个 pin 指针改成已连接其他两个引脚的 wire，则被 Proteus 拒绝为 `Bad object record - circuit data lost.`；还需维护图的结构约束，不能随意复制指针。

Proteus 8.16 另存后的同一模板，ROOT.DSN 扩大为 75,010 字节，header 版本由 800/800 变为 816/847。inline wire 的 `0x10` 与指针之间新增四个零字节，后面的样式及点数组保持同样结构。`wires()` 已能读取这两种观察到的前缀；CDB 则由版本 2 升为版本 7，当前 CDB 解析器没有冒充支持版本 7。

官方 `Tutorials\Styletut.pdsprj` 提供下一步结点/端子样本：有 `$TERDEFAULT`、`$TEROUTPUT`、`$TERGROUND`，标签文本块后有导线绝对指针。其 `0x3B26` 附近出现一个结点：字节 `0x01`、两个 int32 坐标、多个 wire 块指针和零结尾。示例指针 `0x3B3F`、`0x3B71`、`0x40F8` 都值得继续验证。尚未实现通用结点解析器。

## CDB 的作用

该样本的 CDB v2 在 80 字节头部后是实例表：`count`，每实例 `index/kind/DSN对象编号` 三个 uint32、lp8 元件名、引脚数、每引脚两个 lp8 字符串、三个末尾字段。第二个引脚字符串在本样本全部为空。后面另有器件属性表。

这里能读取实例和引脚名，但**没有发现 pin-to-net 编号表**。不能据此断言所有 CDB 都可删除；主实验还需验证无 CDB 的副本能否由 Proteus 打开并重建。

## 已制作的三个单变量探针

| 文件 | 改动 | 原生验证 |
| --- | --- | --- |
| `artifacts/comb01_rerouted_v1.pdsprj` | A 导线的点坐标，14 字节变化，保留长度/CDB/引用 | 打开成功，SDF 保持原网表；几何不会改变连接 |
| `artifacts/comb01_pointer_only.pdsprj` | U1.D0 的导线引用从 A 导线改到 Q 导线，几何不变 | 打开拒绝；引用图有结构约束 |
| `artifacts/comb01_added_wire_v2.pdsprj` | 首 sheet 末追加 71 字节导线，更新全局目录指针和第二 sheet 索引 | **失败**：Proteus 报 `Bad object record - circuit data lost.`；不能作为新增导线功能使用 |
| `artifacts/comb01_swap_inputs.pdsprj` | 交换 U1.D0/D1 引用，同时调整 A/B 折线 | **打开成功，真实 SDF 确认 D0—B、D1—A，输出网不变** |

`swap_inputs_demo(source, destination)` 是已经经过原生验证的受限连接重分配函数，可直接 import 调用；它以 DSN/CDB SHA-256 快照检查拒绝其他模板，避免让固定偏移悄悄应用于不同文件。其他三个仍是研究探针，不能称为通用 `connect()`。`connectivity_research.py` 能重现修改，并有 ZIP CRC、原件 SHA-256 不变、长度/CDB 保留等断言。首次试写留下的 `comb01_rerouted.pdsprj` 不是本轮验证目标，使用 `_v1` 版本。

## 对 API 的直接启示

先支持单 sheet、已知模板里的元件和引脚，通过原生导出 SDF 验证操作后真实网络。下一步的 `connect()` 应同时处理引脚引用、结点引用与几何折线。新增元件还涉及实例编号、嵌入器件定义和 CDB 索引，不能仅复制屏幕上的外形。

最有价值的闭环是：独立脚本写副本 → Proteus 原生打开 → 导出 SDF → 比较预期引脚集合。这个闭环比对截图判断“看起来连上了”更直接，也能逐步扩大受支持的格式范围。
