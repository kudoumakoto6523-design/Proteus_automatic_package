# 自动求 Proteus 引脚端点

本轮只读本机官方原件，独立解析 DSN 内嵌符号和器件 LIB；未操作 GUI、未修改安装目录、未复制外部项目实现。实现是 `pin_geometry.py`，仅使用 Python 标准库及现有的 `UnsupportedFormat` 校验类型。

## 可直接接入的接口

```python
from pin_geometry import symbol_pins, pin_position, transform

# 符号内部的局部电气端点，可缓存到现有 component template。
pin_offsets = {p['name']: p['tip'] for p in symbol_pins(dsn_bytes, symbol_name)}

# 给实例坐标和实例旋转，直接求供 connect() 使用的真实端点。
xy = pin_position(dsn_bytes, 'CAPACITOR', '1',
                  position=(2794000, 1016000), rotation_raw=63736)
assert xy == (2794000, 0)
```

`symbol_pins` 返回每个引脚的 `index/name/number/glyph/anchor/rotation_raw/flags/record_offset/tip`。`index` 保留符号 pin 表原始次序，方便与实例连接指针顺序核对。`tip` 是符号局部电气端点，`anchor` 只是引脚图形锚点。已有的 component_codec/wire_codec 无需知道这些二进制细节。

## 关键发现：引脚图形锚点不是连线端点

以 AND 门为例，D0 的符号 pin 表坐标是 `(-762000, 254000)`，但 `$PINDEFAULT` 子符号中的 `$MKRNODE` 在 `(-508000, 0)`。叠加之后，D0 的局部电气端点才是 `(-1270000, 254000)`。实例原点 `(127000,2032000)` 得到最终 `(-1143000,2286000)`，恰好等于官方 Comb01 电气导线的首点。

`$PINSHORT` 的 node 偏移是 `(-254000,0)`，同样由子符号图形记录读出。因此电阻和电容不需要每个器件硬编码长度；新 glyph 则必须能解析到唯一 `$MKRNODE`，否则明确拒绝。

## 已验证的结构边界

DSN 内嵌器件定义：`lp8 symbol_name`、4 字节 timestamp，然后从 `body` 开始：

| 相对 body | 观察到的字段 |
| --- | --- |
| +0 | u32 整个定义长度，包含该字段 |
| +4 | u32 几何块长度，`body + length` 指向属性文本长度字段 |
| +8 | u16 pin_count |
| +10 | u16 额外/动画符号数量，本实现只接受 0 |
| +12/+14 | 两个 u16，已验证模板均为 1、1 |
| +16 | u32 主图形块长度；pin 表起点为 `body + 16 + 此长度` |

每个 pin 为固定 16 字节头，加三个 NUL 结尾 ASCII 字符串：

| pin 内偏移 | 字段 |
| --- | --- |
| +0 | 原始 flags，未假装完整解释其电气类型/显示位 |
| +4/+8 | x/y，little-endian int32 |
| +12 | 旋转/镜像 raw u32 |
| +16 起 | glyph 名、逻辑引脚名、显示引脚号三个字符串 |

解析完所有 pin 后，必须精确抵达几何块结束；后续属性文本长度也必须精确抵达完整定义结束，避免依靠字符串碰撞误认记录。

glyph 定义也有 lp8 名称和 timestamp。其 body 是总长度、graphic_count、值为 1 的保留字段，随后按每个 graphic 自带的 u32 长度遍历。`0x0206` 图形实例在 record +28 存子符号名，+16/+20 存 x/y；找到唯一 `$MKRNODE`，并验证 `$MKRORIGIN` 均在零点。

旋转低 16 位按有符号 int16 解读，单位为 0.1 度；目前只支持 90 度整数倍。高位 `0x10000/0x20000` 分别按 X/Y 镜像处理，先镜像再旋转。该规则覆盖已核对的左右侧、上下侧 pin 以及 Rescap 中旋转 -180° 的 C1。其他位和非正交旋转拒绝，不使用近似浮点数去猜网格端点。

## 本机只读验证

执行 `py -3.12 api/pin_geometry.py`：

| 原件 | 结果 |
| --- | --- |
| `Graph Based Simulation/Rescap.pdsprj` | RESISTOR 两端局部坐标 `(0,0)`、`(1270000,0)`；旋转后 CAPACITOR 两端为 `(2794000,0)`、`(2794000,1016000)`，与文件内导线端点相符 |
| `Interactive Simulation/Animated Circuits/Comb01.pdsprj` | AND 的 D0、D1、Q 全局坐标逐个与三条原有导线端点相符 |
| `Tutorials/555.pdsprj` | 自动解析 8 个 pin，逻辑名 R/DC/Q/GND/VCC/TR/TH/CV，标注号集合 1–8；包括 `$PININVERT` |
| `VSM for Cortex M3/STM32/STMCubeMX LED Blink/STMCubeMX LED Blink.pdsprj` | `STM32F103R6` 自动解析 57 个 pin |
| `VSM for Cortex M4/STM32/USART_DMA/project.pdsprj` | `STM32F401VE` 自动解析 92 个 pin |
| `LIBRARY/CM3_STM32.LIB` | `STM32F103R6` 的逻辑 pin 名和锚点，与上述官方 DSN 内嵌定义逐个一致 |

这证明通用几何解析覆盖这些实际器件；不代表 MCU 的任意连接已经完成原生打开/网表验证。后者由主实验独立进行。

## LIB 与 MDF 的路线

已读 `DEVICE.LIB`、`BIPOLAR.LIB`、`CM3_STM32.LIB`，header 为 `DEVICE LIBRARY\x1a\0`，版本字段为 400。目录从 `0x20` 开始，每条 80 字节：64 字节 NUL 名称，+64 为器件 body 文件指针，+68 为 timestamp，其余字段暂未解释。header +20 是目录容量，+24 的低 u16 是有效项数。

例如 `CM3_STM32.LIB` 的 `STM32F103R6` 目录条目在 `0x160`，body 指向 `0x998E`。body 与 DSN 的器件 body 同构，所以 `library_pin_anchors(library_bytes, device_name)` 复用同一解析器。库本身不携带 `$PINDEFAULT` 的完整 glyph，故此接口明确只返回锚点，不把锚点冒充端点。当前连线使用 DSN 已嵌入 glyph，即可避免继续解析外部系统符号库。

MDF 是仿真模型引用；当前几何信息已从 DSN/LIB 获得，不需要依赖 MDF 或反汇编模型文件。

## 明确边界

- **connect 按逻辑 pin name。** `number` 只是显示标注，STM32 模板中存在重复号码，尚未套用器件 PACKAGE 中的封装映射，不能将其直接当物理脚号。电阻/电容的逻辑名本来就是 `1/2`，所以 `connect('R1.1','C1.2')` 可自然使用。
- 不支持带动画 variants 的 LOGICSTATE 等整器件定义；AND 本身没有该限制。未来确实需要时再跳过可验证的 variants 块。
- 不支持 bus glyph、多个 node、非零 glyph origin、多单元封装、非 ASCII 名称或任意角度；均需明确拒绝。
- 本文件只负责几何。连线仍必须由 wire codec 同步维护原生连接引用与 junction 关系，不能凭两个坐标相同就宣称电气导通。
