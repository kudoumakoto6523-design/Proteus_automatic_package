# Proteus 原生文件 API：独立实验记录

本轮从本机官方 `C:\ProgramData\program\SAMPLES` 样例分析、编写；没有修改原样例，也没有调用第三方生成器。实现只依赖 Python 标准库。

## 可运行的最小接口

在本目录使用 `py -3.12`：

```python
from proteus_project import Project

p = Project(r"C:\ProgramData\program\SAMPLES\Graph Based Simulation\Rescap.pdsprj")
print(p.components())
p.set_value("C1", "2u")       # 原值 1u，DSN 与 CDB 同步原位改写
p.move("C1", 254000, -254000) # 原始坐标单位；元件及四个文字锚点一起移动
p.save("my_rc_variant.pdsprj")
```

`save()` 只创建新路径；已存在目标和源路径都不能覆盖。`move()` 保留原有导线及连接记录，因此不具备编辑器的拖动重连语义。

已生成待/供软件验证的文件：

- `samples/rescap_value_2u.pdsprj`：只把 C1 从 `1u` 改为 `2u`，ROOT.DSN、ROOT.CDB 各改变 1 字节。
- `samples/rescap_value_2u_moved.pdsprj`：再把 C1 从 `(2794000, 1016000)` 改为 `(3048000, 762000)`，四个文字锚点平移相同位移。

本文件只报告文件层实验。Proteus 实际打开、显示、网表和仿真结果由主任务另外记录，不以 ZIP 校验替代软件验证。

## 已识别结构

`.pdsprj` 是 ZIP 容器，包含 `ROOT.DSN`、`ROOT.CDB`、`PROJECT.XML`，还可能包含图表、脚本等成员。原型保留其余成员原始内容，修改后让 ZIP 标准库计算 CRC。

所支持的 DSN 元件记录具有四个连续文本记录，角色依次是：

1. `COMPONENT ID`：参考编号。
2. `COMPONENT VALUE`：显示值。
3. `COMPONENT VALUE`：器件类型文本。
4. `PROPERTIES`：包含换行的属性文本。

每条记录为 `0xff`、短字符串长度及内容、34 字节文字属性、零结尾字体、零结尾角色名、4 个零字节。文字属性开头是两个 little-endian int32 坐标；其中也包含文字角色编号，解析时同时检查角色编号和角色名。

四条记录之后为属性文本字节数 `uint32`、引脚数 `uint16`、短字符串符号名称，随后出现元件坐标 `int32 x,y`、原始旋转字段 `uint32`、DSN 对象编号 `uint32`。后续连接数据保持不动。不能把某个电阻样例的固定偏移套到其他器件。

CDB v7 尾部属性表中的单条记录包含：5 个 `uint32` 头字段、4 个短字符串（编号、值、器件、封装）、包含长度字段本身的属性区总长度、零结尾属性文本。**CDB 属性表编号与 DSN 对象编号并不总相同。** 原型采用四个语义字段逐字匹配，再完整解析 CDB 属性表、检查记录数和终止位置。

在 `Rescap` 中，元件尾部还出现指向 WIRE 样式块的绝对位置。其他连接实验亦观察到首次定义对象与后续引用有不同标记。因此改变字符串长度、插入或删除字节需要处理对象引用和后续偏移；本原型只允许同字节长度值修改和固定宽度坐标修改。

## 边界与验证

`py -3.12 check_project.py` 是一份不依赖测试框架的集成检查。验证：有边界元件读取、DSN/CDB 同步改值、其他字节保留、拒绝变长、坐标读回、拒绝溢出、反向操作后所有 ZIP 成员逐字节恢复、拒绝覆盖源文件。结果保存在 `samples/project-check.json`。

另按固定顺序读取 49 个官方样例（Graph Based Simulation 文件名排序前 40 个，加 Generator Scripts 的 9 个）：**10 个工程、53 个元件被完整接受；39 个工程明确拒绝**，详见 `samples/read-coverage.json`。这衡量的是当前保守适配器的读取覆盖，不是 Proteus 文件本身是否有效。

当前不接受 CDB 非 v7、未知表头、多实例/未知尾部、非短 ASCII 字符串、DSN 与 CDB 字段不一致等结构。未声明已经支持全部 Proteus 版本、任意元件或所有工程。

后续最有价值的组合是：原生 ADI 负责任意长度属性修改；文件适配器负责受验证的元件查询/坐标改写；原生网表导出作为连线修改的电气检查；确认对象引用格式后再加 `create_component()` 和 `connect()`。完整 API 的关键是实际网表闭环，而不是套一个 HTTP/MCP 壳。

## 后续独立克隆实验（软件验证状态由主任务记录）

`clone_research.py` 针对 SHA-256 固定的官方 Rescap，生成 `samples/rescap_clone_c2_v2.pdsprj`。它追加 352 字节 DSN 元件记录，另在 CDB 的实体表增加 41 字节、属性表增加 63 字节；把 C1 克隆为位于 `(4318000, 1016000)` 的 C2，值仍为 `1u`。C2 的 DSN 对象编号为 14，CDB 属性编号为 3；二者不能混同。原有通用原型文件没有为了此实验而放宽格式限制。

这里还识别出：首个 `ISIS CIRCUIT FILE` 前 4 字节存放尾部 sheet 目录的绝对位置。向第一个 sheet 插入字节时，除目录内第二 sheet 的位置，还必须同步这个全局目录指针。原型同时修改这两处，并递增观察到的下一对象编号字段。

**该孤立克隆已通过 Proteus 8.16 真实打开及网表验证。** `artifacts/clone-and-wire-v2-live.json` 第一项记录打开、导出及正常退出；`artifacts/rescap_clone_c2_v2_verified.SDF` 中 C2 为 `CAPACITOR 1u`、EID 7，`C2.PS.1` 和 `C2.PS.2` 分别属于独立网络 `#00003`、`#00004`，原 R1/C1 连接保持。这确认了该格式中零引脚引用可以表示孤立引脚。

## 模板序列化：从空白用户页添加多个元件

`component_codec.py` 在上述实验证据上增加 `Circuit`。它从官方模板提取电阻/电容四段文本及实例布局，重新序列化短字符串；不再要求新编号或参数与模板字节长度相同。

```python
from component_codec import Circuit

Circuit().add("RESISTOR", "R_INPUT10", "10k", -2540000, 2032000) \
         .add("RESISTOR", "R2", "4.7k", 0, 2032000) \
         .add("CAPACITOR", "C_FILTER3", "100n", 2540000, 1016000) \
         .add("CAPACITOR", "C4", "22u", 5080000, 1016000) \
         .save("new_rc.pdsprj")
```

它重建一个干净的用户 sheet，保留模板中的器件定义和 `__DEFAULT__` 默认图形页，删除旧用户页元件、导线、图表及无主图表缓存。CDB 实体和属性表、对象编号、全局目录及默认页位置同步重新生成。每次 `add` 可选择器件、编号、参数及绝对坐标；可重复添加同类型器件。公开名称也可写作 `add_component`，与 `add` 等价。

当前模板导入范围是经过检查的 FILEVER 840 布局中的 `RESISTOR`、`CAPACITOR`，其余器件会拒绝。编号使用大写字母、数字和下划线，含至少一个数字且至多 63 字节；值支持 1–254 字节可打印 ASCII。旋转保持所选器件模板原始方向。

`py -3.12 check_components.py` 已验证可变长度字段、多次添加、坐标和编号读回、零引脚引用、输出成员内容可重复、拒绝无效输入且不产生部分修改、拒绝覆盖已存在文件及源样例保持不变。

主任务实际使用 Proteus 打开 `samples/generated_rc_four_parts.pdsprj`，`artifacts/four_parts_native.sdf` 正确包含 `R_INPUT10=10k`、`R2=4.7k`、`C_FILTER3=100n`、`C4=22u`，各引脚组成 8 个独立网络；软件另存及正常关闭成功。`samples/generated_two_capacitors.pdsprj` 也实际打开成功。至此模板提取、多个新增元件、变长编号/参数与干净用户页均有原生软件验证。

## 按引脚建立连接

`Circuit.connect("R_LOAD1.2", "C_MID2.1")` 接受逻辑引脚端点，也接受 `(ref, pin)` 二元组。`pin_geometry.py` 从嵌入的器件定义及引脚图元读取电气端点，再应用实例旋转；没有把电阻、电容的坐标常量写进 Circuit。`wire_codec.py` 对所有元件/导线作两遍排列，计算并填写显式引用。

```python
c = Circuit()
c.add_component("RESISTOR", "R_LOAD1", "10k", -2540000, -1016000)
c.add_component("CAPACITOR", "C_MID2", "100n", 0, 0)
c.add_component("RESISTOR", "R_OUTPUT3", "4.7k", 2540000, 0)
c.connect("R_LOAD1.2", "C_MID2.1")
c.connect("C_MID2.2", "R_OUTPUT3.1")
c.save("rc_chain.pdsprj")
```

可选 `points=[(x1,y1), ...]` 用于指定整条正交折线，必须包含与实际引脚一致的首尾坐标；连接加入状态前就检查。自动路径仅采用直线或一个直角，不做避障布线。两端连接已由主任务实际 SDF 证实；多线与共享引脚分支的具体原生验证结果由主任务记录，不能仅根据 Python 编码完成宣称任意网络已验证。

## 后续发现：原生属性修改后的 CDB entity 尾字段

本文件前文保留早期实验记录；当前能力以 README 和 `device_library_notes.md` 为准。CDB v7 的 entity 在 pin 表之后包含 `u32(0), u32(property_index), u32(auxiliary)`。初始样例常见 auxiliary=`0xffffffff`；通过 Proteus 原生 ADI 修改组件属性并保存后，只有编辑过的 entity 可变成 `0`，其余字段不变。这个字段的完整语义尚未确定，所以解析器仅接受这两个实际验证过的值，并在模板与重新序列化时保留它，不泛化接受其他值。

`check_native_property_roundtrip.py` 复现 R1/C_FILTER2/ground 工程，原生将 C_FILTER2 VALUE 改为 470n，然后 Circuit.open/save，再原生 SDF/save/reopen。三阶段均保留 470n、原网络及 auxiliary=0，且合成的 auxiliary=1 仍被明确拒绝。证据：`artifacts/native-property-roundtrip-7be9f18a/result.json`。
