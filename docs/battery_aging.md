# 电池健康、容量学习与老化补偿

## 结论

当前证据显示“电池健康”涉及三个不同层次，不能混为一个固件变量：

1. 官方控制台的健康比例由 Windows 电池驱动返回的
   `FullChargeCapacity / DesignedMaxCapacity` 计算，保留两位小数；控制台没有用循环次数
   修正这个比例。
2. `FullChargeCapacity` 和 `CycleCount` 来自电池 Fuel Gauge/Smart Battery 数据路径，
   EC 将缓存结果发布到主机可读寄存器。公开循环次数的发布函数没有自增逻辑。
3. EC 另外存在一套明确的老化补偿策略：按循环次数、温度、充电模式和电源状态分档，
   下调最终充电电压限制。这会影响长期电池寿命，但不是 UI 中显示的“健康百分比”。

## 主机端健康比例

官方 GCU Service 的 `BatteryModel.BM_Manager.GetBatteryLife()` 执行：

```csharp
Math.Round(FullChargeCapacity / DesignedMaxCapacity, 2)
```

两个容量值都由 Windows battery class driver 的 `DeviceIoControl` 返回。另一个旧式
`MyControlCenter.BatteryInfo` 类只直接读取 EC 的循环次数；其中容量 getter 在当前程序集里
是返回零的空实现。因此：

- 健康百分比是主机端展示计算；
- EC/Fuel Gauge 提供底层 learned full-charge capacity；
- 循环次数被单独展示，不参与 GCU 的健康比例公式。

## Smart Battery 数据发布

固件发布的字段语义符合 Smart Battery Data 布局，包括：

| SBS 标准命令 | 标准含义 | EC 公开数据 |
|---:|---|---|
| `0x0F` | RemainingCapacity | `EC[0x0436:0x0437]` |
| `0x10` | FullChargeCapacity | `EC[0x0404:0x0405]` |
| `0x17` | CycleCount | `EC[0x04A6:0x04A7]` |
| `0x18` | DesignCapacity | `EC[0x0402:0x0403]` |
| `0x19` | DesignVoltage | `EC[0x0408:0x0409]` |

`CODE:0x69F6 publish_battery_cycle_count` 的机器码只做字段搬运：

```text
XRAM[0x0343:0x0344] -> EC[0x04A6:0x04A7]
```

函数内没有加一、充放电积分或持久化操作。这支持循环次数由电池 Fuel Gauge 维护、EC
负责轮询和发布的判断。实际 Fuel Gauge 内部如何定义一个 cycle，无法从主 EC 镜像证明。

继续向上追 `XRAM[0x0343:0x0344]` 的写入点后，可以修正一处此前过强的推断：当前镜像中
找到的直接 SMBus word-read 并不是标准 SBS `CycleCount (0x17)`，而是
`CODE:0x4433 refresh_vendor_battery_telemetry` 构造的命令 `0x43`。该流程先以 byte
协议读取 `0x1C`、`0x1E` 两个字段，二者成功后才读取 `0x43`；成功返回的两个 data byte
写入 `XRAM[0x0343:0x0344]`，随后才由 `0x69F6` 发布到公开 CycleCount。

因此上表能确认公开字段的 **SBS 语义**，但不能证明本机 Fuel Gauge 在物理总线上直接用
标准命令号 `0x17` 提供循环次数。`0x43` 很可能属于具体 Fuel Gauge 的厂商寄存器或经过
平台封装的命令；在识别电池型号和该芯片命令表以前，不应进一步猜测它的厂商名称。当前也
没有发现 EC 对返回 word 做递增，结论仍然是计数由外部电池侧维护。

`CODE:0x5A8E initialize_battery_telemetry` 是此前 `0x5AFC–0x5BB3` 片段所属的真实入口。
它先校验多组 Fuel Gauge 缓存及镜像值，再计算并发布 `LastFullCapacity`，随后批量发布
DesignCapacity、DesignVoltage、RemainingCapacity 等 ACPI `_BIF/_BST` 数据。因此原先把
`0x5AFC` 和 `0x5BB3` 当作两个待定函数并不正确；它们是同一初始化/发布流程内的基本块。

这段代码可确认 `LastFullCapacity` 不是简单的双字节原样复制：内部容量值和
`XRAM[0x0342]` 的比例/校准字节参与了以 100 为常数的乘除运算，结果才写到
`EC[0x0404:0x0405]`。

进一步检查所有常量地址写入后，`0x0342` 的完整来源也更清楚了。`0x4433` 流程在命令
`0x43` 之前先读取厂商命令 `0x42`，并把返回的单字节直接写入 `0x0342`；之后
`CODE:0x4970` 容量状态机还会从内部候选量取值，与 `XRAM[0x039B]`、`XRAM[0x039C]`
两个边界比较/限幅，在 `0x498E` 基本块覆盖 `0x0342`，并同时计算一组以 100 为常数的
派生缓存：原始机器码明确写出 `XRAM[0x0340:0x0341] = factor * 100`。初始化流程再比较
`0x0342` 与镜像 `0x03C0`，变化时才重新发布容量。这里的边界地址已经按原始 DPTR 立即数
校正，不能直接采用旧 `.d52` 中偏小 1 的符号名。

所以它既不是未经处理的 Fuel Gauge 缓存，也不是完全由 EC 从零生成的健康值；更准确的
数据流是 **Fuel Gauge 厂商命令 `0x42` 提供初值，EC 状态机二次限幅/校准，然后用于容量
缩放**。`0x42` 的厂商寄存器名称尚未确认，仍不能直接把它等同于标准 SOH。

不过，候选量上游状态的物理语义以及内部 ROM 算术 helper 的精确舍入仍未完全恢复，所以
目前仍不能把 `0x0342` 直接命名为 SOH 百分比，也不能据此断言 EC 自己执行库仑积分学习。
更准确的边界是：Fuel Gauge 提供容量相关原始数据和缩放初值，EC 还执行一次状态相关的
校准与发布。

Ghidra 对这个入口的尾部会错误跟进 bank 共享跳转并混入无关代码；入口和上述机器码数据流
可靠，但生成伪 C 的函数尾界不能直接当作厂商源码边界。

## 电量 Trip Point 与循环次数

`CODE:0x863A check_battery_trip_point` 已可完整命名：

- DSDT 把 `EC[0x0704:0x0705]` 定义为 `BTP0`，由 ACPI `_BTP` 写入；
- 函数按充/放电方向比较 `BTP0` 与 `EC[0x0436:0x0437]` RemainingCapacity；
- 越过阈值后发送 SCI query `0x89`，并设置 30 tick 的去抖计数；
- DSDT `_Q89` 随即执行 `Notify(BAT0, 0x80)`，要求操作系统刷新电池状态。

这里还有一处与“新电池统计”有关、但不属于 EC 老化算法的 DSDT 策略：放电时若
`CycleCount < 50`，`_BTP` 会用 `requested_trip * FullChargeCapacity / DesignCapacity`
换算后再写 `BTP0`；达到 50 次后直接写原始 trip point。EC 的 `0x863A` 本身不做这个
比例换算，只负责检测跨越并通知系统。

## 老化充电电压补偿

真实的 `RET` 边界入口是
`CODE:0xD1D1 update_battery_aging_charge_voltage_derating`；此前标注的 `0xD1F7` 只是
同一函数内部在电源条件成立后进入计算的分支标签。2.10 固件中的同一函数迁移到约
`0xD173`，但下述所有阈值和分档机器码均保持一致。

### 电芯数和高压应力累计

进入累计和档位计算之前还有四个 gate，原始机器码应按真实立即数而不是旧 `.d52` 标签
解释：

- `EC[0x0741].0`（已有符号 `ApExistFlag`）为 0 时，函数先清除
  `EC[0x07A6].5:4` 的 Health/Balanced 模式位；
- `XRAM[0x050B].7` 为 0 时，`XRAM[0x09C7:0x09CA]` 全部清零并立即返回；该位来自一组
  内部 16 位有符号量的高字节，当前只能确认它是符号/方向 gate，不能再写成
  `EC[0x0490]` 的电源位；
- `EC[0x0497].0` 为 0 时，不执行后续档位计算；
- `EC[0x0490].2` 和 `.0` 还分别控制辅助 hook 与最终计算路径。任一后续 gate 不成立时，
  本次调用直接返回，并不会主动把 `EC[0x0522:0x0523]` 恢复为未降额值，因此旧目标会
  保留到其他状态机重新发布。

`EC[0x0491].7:6` 被解码为串联电芯数：`11b -> 4`、`10b -> 3`、其他值 `-> 2`。
函数将电芯数乘以常数 `0x1004 = 4100`，并与 `EC[0x0438:0x0439]` 的电池包电压比较：

```text
high_voltage = battery_voltage >= series_cells * 4100 mV
```

高压条件需经过两级计数器 `XRAM[0x09C7]`、`XRAM[0x09C8]`，两级比较常数均为 60；
达到条件后才更新 `XRAM[0x09C9:0x09CA]` 高压应力累计值。

调用链和时间尺度现已恢复：`CODE:0x8FFB periodic_720ms_power_maintenance` 调用该函数；
common bank 的 Timer0 分相调度器每 144 个 tick 运行一次 `0x8FFB`。Timer0 每次从
`F106h` 计到溢出，即 3834 个 timer count。IT5570 A 手册说明 8051 timer 使用固定
9.2 MHz；按标准 8051 的 `/12` machine-cycle 计数得到：

```text
Timer0 tick = 3834 / (9.2 MHz / 12) = 5.0009 ms
aging task  = 144 * tick             = 720.1 ms
first 60    = 43.2 s
60 * 60     = 43.2 min
```

因此在电压条件成立时，大约每累计 43.2 分钟才向应力值加一次温度权重。低于电压门槛的
窗口不会增加应力。内部符号/方向 gate `XRAM[0x050B].7` 失效时，函数会把
`0x09C7–0x09CA` 全部清零，所以它是当前运行状态内的高压热应力，不是写入 Flash 的
终身累计量。该 gate 的极性和对应物理量仍需用真机的充/放电方向变化验证。

温度不会直接乘最终电压补偿，而是决定每次向高压应力累计值增加多少。这里必须按
common-bank `CODE:0x7B9E` 的真实 16 位加法核对：调用点令 `A=0`、`B=1/3/7`，helper
先递增 DPTR 并把 B 加到 `XRAM[0x09CA]` 低字节，再把进位加到 `0x09C9` 高字节。因此
增量是 `0x0001/0x0003/0x0007`，不是 `0x0100/0x0300/0x0700`。

| 电池温度原始值 | 约摄氏温度 | 应力增量 |
|---:|---:|---:|
| `< 0x0BD6` | `< 29.85°C` | `0x0001` |
| `0x0BD6–0x0C3A` | `29.85–39.85°C` | `0x0003` |
| `> 0x0C3A` | `> 39.85°C` | `0x0007` |

累计更新前还检查 `0xFDE8`（65000）附近的饱和值。因此同样处于 4.10 V/cell 以上时，
约 30–40°C 的老化累计速度是低温档的 3 倍，约 40°C 以上是 7 倍。

档位比较使用 `SETB C; SUBB low; SUBB high`，实际触发条件是严格大于表中常数。按每个
合格窗口约 43.2 分钟计算，从零达到各档所需的最短合格高压时间为：

| 每电芯降额 | 应力边界 | `+1` 低温档 | `+3` 中温档 | `+7` 高温档 |
|---:|---:|---:|---:|---:|
| `50` | `> 0x10E0` | 129.63 天 | 43.23 天 | 18.54 天 |
| `100` | `> 0x1950` | 194.43 天 | 64.83 天 | 27.78 天 |
| `150` | `> 0x21C0` | 259.23 天 | 86.43 天 | 37.05 天 |
| `200` | `> 0x2D00` | 345.63 天 | 115.23 天 | 49.38 天 |
| `250` | `> 0x3DE0` | 475.23 天 | 158.43 天 | 67.89 天 |

这里的“合格高压时间”不要求墙钟时间连续：低于电压门槛时第二级计数器保持而不递增。
但 `XRAM[0x050B].7` 失效会把两级计数器和应力全部清零，所以跨越该 gate 的时间不能累积。

### 补偿档位决策

函数按从严重到轻微的顺序选择每电芯基础降额。循环次数不是唯一输入；高压应力、保护模式
和少量尚未命名的状态门槛可以把结果提升到更高一档：

| 每电芯降额 | 循环次数门槛 | 高压应力门槛 | 其他已确认条件 |
|---:|---:|---:|---|
| `250` | `>= 550` | `> 0x3DE0` (15840) | 模板辅助量 `XRAM[0x0A54] >= 25` |
| `200` | `>= 450` | `> 0x2D00` (11520) | Health 模式 `EC[0x07A6].5:4 == 10b` 至少选此档 |
| `150` | `>= 350` | `> 0x21C0` (8640) | 辅助等级 `XRAM[0x0A54] >= 13` |
| `100` | `>= 250` | `> 0x1950` (6480) | Balanced 模式 `EC[0x07A6].5:4 == 01b` 至少选此档 |
| `50` | `>= 150` | `> 0x10E0` (4320) | 模板中的辅助等级门槛为 7，但本项目构建写入的 `XRAM[0x0A54]` 固定为 1，不能触发 |
| `0` | `< 150` | `<= 0x10E0` | 且所有辅助条件允许 |

表格中的逻辑是“任一老化维度达到该档即可”，不是只有循环次数同时满足才降额。更高档先
匹配，所以例如循环 200 次但高温高压累计超过 `0x21C0` 时仍会选 150 档。

各行条件之间是 OR；完整的额外门槛为：`XRAM[0x0A54]` 达到 `25/19/13/10/7` 时依次可
触发 `250/200/150/100/50` 档，Health 模式至少触发 200 档，Balanced 模式至少触发
100 档。这里没有 `XRAM[0x0857] >= 25` 或“battery-pack status bit 4 触发 150 档”的
老化条件；这些旧结论分别来自共享尾入口和函数边界的误读。

原始机器码能解释 `0x0857` 误归因的原因。RGB 状态机从 `CODE:0xE051` 进入 helper，先
执行 `MOV DPTR,#0x0857; MOVX A,@DPTR`，再顺序落入 `CODE:0xE055` 的“比较 A 与 25”
公共尾部。老化函数在 `CODE:0xD31A` 已经把 `XRAM[0x0A54]` 读入 A，并直接调用
`CODE:0xE055`，所以它不会执行前面的 `0x0857` 读取。可读 C 把共享尾部显示成普通
`func_0xe055()` 时，若不同时检查调用点的 A 和真实入口地址，就会错误地把前导函数读取的
`0x0857` 传播到老化路径。

`XRAM[0x0857]` 的真实静态引用属于共享固件中的可选 RGB 键盘模板路径：`CODE:0xC080`
根据官方字段 `ADDR_RGBKB_MUSIC_NO` 对应的 `EC[0x076F]` 分派状态，以 `0x0857` 的 0–25
步进值索引 `CODE:0x7813/0x782C` 表，并更新 `XRAM[0x1803]`、`0x1805`、`0x1808`；到达
边界后清除计数和模式字段。这只能证明代码模板的语义，不能证明当前机器安装了 RGB 键盘。
GPIO 去抖函数的原始立即数实际是 `0x0858/0x0859`：`0x0858`.0 镜像 GPIO
data-mirror `0x1664`.3，`0x0859` 是 5-tick 计数器。旧 `.d52` 的 `org -1` 标签分别显示
为 `dptr_0857/dptr_0858`，正是此前地址错位的来源。RGB 步进计数不参与电池降额。

当前机器的只读实测进一步排除了这条模板路径：`EC[0x0766] = 0x90`，官方 capability map
中的 RGBKeyboard bit 2 为 0；`EC[0x0769:0x076F]` 全为 0，`EC[0x078C] = 0x21` 对应本机
实际存在的单区背光控制。因此本机只有三档单色背光不与上述可选 RGB 模板矛盾，也不能用
`0x0857` 解释当前 250 mV/cell 降额。

`XRAM[0x0A54]` 不是全固件专用的电池寄存器；完整 bank0/bank1 TSV 导出显示其他无关
算法也把该地址当共享 scratch 使用。因此全局符号表将它保守命名为 `shared_scratch_0a54`。
这里讨论的“值固定为 1”只限于一次老化函数执行中的局部数据流，而不是该地址在任意时刻
或任意 bank 的全局不变量。

老化路径内的到达值分析仍然推翻了“保护等级聚合器”解释。老化函数先调用
`CODE:0xD1BB` 检查 `EC[0x0497].0`；该位无效时函数直接退出，有效时 helper 明确令
`R7 = 1`。随后 `EC[0x0490].2` 条件成立才调用 `CODE:0x1D08`，返回后未经变换便执行：

```text
R7 = 1
call CODE:0x1D08 apply_platform_aux_derating_hooks
XRAM[0x0A54] = R7
```

调用 `0x1D08` 前，`CODE:0xE1A0` 已把 `EC[0x0490].2` 的结果留在 A；调用成立时 A 必为
非零。`0x1D08` 本身是共享函数的中段入口，第一条真实指令是 `JZ`，所以这条调用立即执行
后面的 `LJMP` 进入公共尾部；它不会重新读取 `EC[0x0490]`。后面那组平台检查、GPU 状态
和 trip-point 调用在这条老化调用路径上不可达。公共尾部只调用 wrapper `0x1C72` 和
`0x1C78`；wrapper 最终切到 bank0 的 `0xA71B`、`0xA71D`，而两个目标在本固件中都只是
单字节 `RET`，全程没有改写 `R7`。因此只要执行到降额决策，`XRAM[0x0A54]` 就确定为 1。
从这次写 1 到后面的 `< 25/19/13/10/7` 比较之间，老化控制流调用的计数、比较和应力更新
helper 都不写 `0x0A54`，所以其他模块对共享 scratch 的复用不会成为这次计算的变量贡献。
后续代码虽然保留 `< 25`、`< 19`、`< 13`、`< 10` 和 `< 7` 等模板门槛，但本项目配置下
均不会由这个字段触发。2.12 里不存在需要继续寻找的 `0x0A54`“各保护源”；它是带空平台
钩子的模板接口。

另一个独立状态 `XRAM[0x0623]` 的范围仍明确是 0–7：它会递增、递减、在 7 饱和并在
电源条件变化时清零，还与 `0x0620–0x0622`、`0x081D` 一起取最大值后发布平台保护等级。
但它没有流入 `0x0A54`，不能声称“`0x0623 == 7` 单独触发 50 mV 档”。此前的
`0x0622` 地址还受 `disasm51` 在 `org -1` 下符号名偏小 1 的影响；原始机器码确认该独立
状态的真实地址是 `0x0623`。

继续拆它自己的状态机后，可以把 `0x0623` 命名为电池放电过流保护等级：状态机读取公开
的 `EC[0x0434:0x0435] battery_discharge_current`，并与一个阈值比较；没有启用平台覆盖值
时阈值是 `0x1D4C = 7500 mA`。电流超过阈值时等级上升（最高 7），低于阈值的 70% 时
等级下降，中间区间保持不变，构成迟滞。代码还会合并 `0x04F4:0x04F5` 的第二路量，并可
由 `0x0844` 控制使用 `0x08FE:0x08FF` 的平台阈值；这两个字段的公开名称仍未确认。
因此它确实是电池/平台限功耗的一部分，但不是电池寿命循环计数，也没有证据表明它直接改变
老化 CV 档位。

### 最终电压公式与单位

`XRAM[0x030E:0x030F]` 是未降额的 16 位 charger voltage target，但现在可以继续确认它的
来源。common bank 的 ROM 轮询表 `0x632E` 每项为“目标 XRAM 地址 + 电池侧 byte 命令”：

```text
... { 0x030D, 0x0D }, { 0x030F, 0x0E },
    { 0x030E, 0x0F }, { 0x0311, 0x10 } ...
```

周期状态机以 byte 协议读取命令 `0x0E`、`0x0F`，分别写到 `0x030F`、`0x030E`。
算术代码把 `0x030E` 当高字节、`0x030F` 当低字节，因此它是电池侧提供的未降额电压请求，
不是 EC 内置的固定设计电压。这里的命令是该电池遥测块的两个 byte 索引；不能把 `0x0E`
或 `0x0F` 单独解释成标准 SBS word 命令。EC 向 charger 下发时使用的标准
`ChargingVoltage (0x15)` 是数据流末端的另一条总线事务。

最终机器码先对内部大端基准执行完整的 16 位带借位减法，再交换成公开/队列使用的小端字节
顺序：`EC[0x0522]` 为低字节、`EC[0x0523]` 为高字节。

参与最终限制的参数可以汇总为：

| 参数 | 来源 | 固件中的计算/解释 |
|---|---|---|
| 未降额基准 | 电池 byte 命令 `0x0E/0x0F` -> `XRAM[0x030F/0x030E]` | 组合为 16 位 mV 目标 |
| 串联电芯数 | `EC[0x0491].7:6` | `11b/10b/其他 -> 4/3/2` |
| 当前包电压 | `EC[0x0438:0x0439]` | 与 `cells * 4100 mV` 比较，只决定应力是否计时 |
| 电池温度 | `EC[0x04A2:0x04A3]` | 0.1 K；摄氏温度约为 `raw / 10 - 273.15`，选择 `1/3/7` 权重 |
| 循环次数 | `EC[0x04A6:0x04A7]` | 与 `150/250/350/450/550` 比较 |
| 高压热应力 | `XRAM[0x09C9:0x09CA]` | 每约 43.2 分钟增加 `1/3/7`，与五档严格大于阈值比较 |
| 充电模式 | `EC[0x07A6].5:4` | Balanced 至少 100 mV/cell，Health 至少 200 mV/cell |
| 模板辅助量 | `XRAM[0x0A54]` | 本机构建在老化路径上的可达值为 1，不会提高档位；`XRAM[0x0857]` 属于 RGB 路径，不参与计算 |

```text
pack_derating = series_cells * per_cell_derating
EC[0x0522:0x0523] = base_charge_voltage_target - pack_derating
```

### 当前机器的 250 mV/cell 结果

当前机器 `17600 mV` 基准与 `16600 mV` 保存目标长期失配的实测时间线、gate 真值表和修复
方案另见 [充电电压目标卡在 16.6 V 的固件分析](charge_voltage_stuck_16v6.md)。

2026-07-18 对当前机器的只读采样为：基准请求 `17600 mV`，最终目标 `16600 mV`，4S，循环
次数 57，普通电池模式，因此保存结果对应 `4 * 250 = 1000 mV` 总降额。重新按 TSV 中的
真实函数入口核对后，`XRAM[0x0857]` 与这次计算无关；老化路径中的共享 scratch
`XRAM[0x0A54]` 局部值为 1，也不能触发任何辅助门槛。若 `16600 mV` 是在同一个
`17600 mV` 基准下由 `CODE:0xD1D1` 最近一次有效计算的结果，则当前循环次数和辅助量都
可排除，250 档只能来自当时 `XRAM[0x09C9:0x09CA] > 0x3DE0`。但修正应力增量后，即使
一直处于最高温度权重，也至少需要 67.89 天合格高压时间，且 `XRAM[0x050B].7` 一旦失效
就清零；这使“本次保存结果由应力触发”的实机可信度显著下降。

这不表示当前应力仍达到门槛。采样时 `XRAM[0x050B].7 = 0`、`EC[0x0497].0 = 0`，函数会
清除应力或提前返回，并保留旧的 `EC[0x0522:0x0523]`。初始化流程 `CODE:0x5A8E` 还会
直接把当时的 `XRAM[0x030E:0x030F]` 复制到 `EC[0x0522:0x0523]`。因此更保守的解释是：
`16600 mV` 可能是较早的基准请求或旧状态留下的目标，后来基准变成 `17600 mV`，而 gate
关闭使目标尚未重算。由于 `0x09C9:0x09CA` 和基准历史都不在主机可见时间序列中，仅凭
当前快照不能在“极长期应力触发”和“旧基准/旧状态残留”之间唯一归因；修正增量后后者
至少同样值得优先验证。

按函数内数据依赖拆解当前快照：

| 内部输入/状态 | 当前值 | 对本次调用或保存目标的贡献 |
|---|---:|---|
| `ApExistFlag` | `1` | 不清除模式；不直接改变 mV |
| `XRAM[0x050B].7` | `0` | 清零 `0x09C7–0x09CA` 并立即返回，是当前最早生效的 gate |
| 包电压 | `16302 mV` | 4S 下低于 `16400 mV` 门槛；即使越过 gate 也不会增加应力 |
| 温度 | `3000`，约 `26.85°C` | 若形成合格窗口则应力 `+1`；当前贡献 0 |
| `EC[0x0497].0` | `0` | 另一提前返回 gate；当前不进入档位决策 |
| `EC[0x0490].2/.0` | `1/1` | 两个后续 gate 均开放，但被更早的 gate 遮蔽 |
| 电芯数 | `4` | 若选档，将每电芯降额乘 4 |
| 循环次数 | `57` | 小于 150，档位贡献 0 |
| 电池模式 | Normal (`00b`) | 模式贡献 0 |
| 辅助量 | 老化路径固定为 `1` | 小于 7，档位贡献 0 |
| 当前基准 | `17600 mV` | 有效重算的加法基准 |
| 保存目标 | `16600 mV` | 当前调用没有改写；不能当作当前输入的即时输出 |

反事实地令早期 gate 开放、应力取当前已清零值，其余使用当前可见输入，则函数会选择
`0 mV/cell` 档并写出 `17600 mV`。因此保存的 `16600 mV` 与当前输入之间相差的
`1000 mV` 只能说明它在过去某一状态被写入，不能证明当前存在 `250 mV/cell` 的有效变量
贡献。

Ghidra 当前把入口误生成为带 `param_1` 的函数，并把 16 位 `SUBB` 链拆成难以复核的有符号
表达式。结合 `D1D1–D422` 原始机器码、调用点寄存器状态和 common-bank 算术 helper，
审计后的聚焦伪 C 为：

```c
if (!ap_present)
    battery_mode &= ~0x30;

if (!(internal_signed_direction_hi & 0x80)) {
    clear_high_voltage_stress();
    return;                         // does not rewrite the old voltage target
}

cells = ((battery_pack_status & 0xc0) == 0xc0) ? 4 :
        ((battery_pack_status & 0xc0) == 0x80) ? 3 : 2;

if (!battery_gate_0497_bit0 || !power_source_bit2)
    return;

auxiliary_state = 1;               // caller seed survives empty project hooks
if (++sample_counter >= 60) {
    sample_counter = 0;
    if (battery_voltage_mv >= cells * 4100 && ++window_counter >= 60) {
        window_counter = 0;
        if (stress < 65000) {
            stress += temperature_raw < 0x0bd6 ? 1 :
                      temperature_raw <= 0x0c3a ? 3 : 7;
        }
    }
}

if (!power_source_bit0)
    return;

if (base_charge_voltage_target_mv < 500)
    derating_per_cell = 0;
else if (stress > 0x3de0 || cycle_count >= 550 || auxiliary_state >= 25)
    derating_per_cell = 250;
else if (stress > 0x2d00 || cycle_count >= 450 ||
         auxiliary_state >= 19 || battery_mode == HEALTH)
    derating_per_cell = 200;
else if (stress > 0x21c0 || cycle_count >= 350 || auxiliary_state >= 13)
    derating_per_cell = 150;
else if (stress > 0x1950 || cycle_count >= 250 ||
         auxiliary_state >= 10 || battery_mode == BALANCED)
    derating_per_cell = 100;
else if (stress > 0x10e0 || cycle_count >= 150 || auxiliary_state >= 7)
    derating_per_cell = 50;
else
    derating_per_cell = 0;

charge_voltage_limit_mv = base_charge_voltage_target_mv
                        - cells * derating_per_cell;
```

现在可以把 50–250 高置信度解释为 **mV/cell**：同一函数把公开的 mV 电池电压与
`4100 * 电芯数` 比较，随后又把降额档乘同一电芯数并从 charger voltage target 扣除。
所以 4S 电池各档对应总包降额 `200/400/600/800/1000 mV`。这仍应通过真机只读遥测验证，
但已不再只是没有单位的整数猜测。

Health/Balanced 的最低降额与“充到 60%/80% 后停止”是两层不同策略：前者降低充电器的
CV 电压目标，后者限制允许达到的 SOC；两者可以同时生效。

### 计算结果如何进入 charger worker

`CODE:0x3196 is_charge_voltage_update_needed` 比较：

```text
desired: EC[0x0522:0x0523]
queued mirror: XRAM[0x0836:0x0837]
```

电池存在时两者不同便返回 update-needed。进一步追写入点后，`XRAM[0x0836:0x0837]`
应改称 **queued charge voltage**：队列函数调用的 banked copy helper 在排队前直接把
`EC[0x0522:0x0523]` 复制进去；它不是从 charger 读回的 applied value。因此
`0x0522:0x0523` 确实是 charger 控制状态机的实际目标输入，但不能用 `0x0836:0x0837`
证明外部 charger 已接受或实际执行该电压。

该检测函数还有一个此前被空函数边界掩盖的分支：正常电池路径不活动时，它不比较
`0x0522:0x0523`，而是检查 queued mirror 是否等于 `0x3138`（12600 mV）。这很像 3S
平台的安全/初始化回退目标，但触发它的上游条件仍需继续命名，因此暂不解释成通用默认值。

相邻的 `CODE:0x31FC queue_charge_voltage_update` 已能补全动作侧：调用者传入的 gate
为真时，它把当前 `EC[0x0522:0x0523]` 复制到 queued mirror；gate 为假时则明确写入
`0x3138`，两条路径最后都置位 voltage command bit。由此可以确认 `12600 mV` 是一个真实
的排队目标，不只是比较函数里的 sentinel；但 gate 的间接调用者尚未恢复，仍不能确定它
精确对应“无电池”“遥测未就绪”还是某个平台初始化状态。

共享 worker 现可命名为 `CODE:0x8EFB service_charger_smbus_queue`。电压分支消费
`XRAM[0x0833].1`，电流分支消费 bit 0；它们分别发送 Smart Battery Charger 命令
`ChargingVoltage (0x15)` 和 `ChargingCurrent (0x14)`。`CODE:0xDF0B` 构造四字节描述符：

```text
XRAM[0x0A6E:0x0A71] = { command, 0x00, 0xC0, 0x00 }
word-data staging     = XRAM[0x00C0:0x00C1]
protocol              = 0x0C
8-bit slave address   = 0x12
controller index      = 0
```

`0x12` 与标准 charger 地址 `0x09` 的 8 位写地址表示相符，但仍不能据此确定具体 charger
型号。共同传输实际位于 common/bank0 的 `CODE:0x80D2 execute_smbus_transaction`。它用
Timer1 做超时，轮询并保存原始 host status 到 `XRAM[0x0A74]`：

```text
未超时且 (host_status & 0x7C) == 0
    XRAM[0x0A73] = 0x00
    XRAM[0x0A75] = 1
    R7 = 1
否则
    XRAM[0x0A73] = 0xEE
    XRAM[0x0A75] = 0
    R7 = 0
```

Timer1 超时或 host status 的 `0x18` 错误位出现时会调用
`bank0:0x8016 recover_smbus_controller` 复位并重新初始化对应控制器。这里的 `0xEE` 是固件
合并后的失败 sentinel，不是 SMBus 规范错误码；区分具体原因要同时采样 `0x0A74`。

还有一个重要限制：charger worker 调用 `0x80D2` 后没有检查返回的 `R7`、`0x0A73` 或
`0x0A75`。它只把共享 staging buffer `0x00C0:0x00C1` 与对应 queued mirror 做逐字节 XOR；
不相等才在 `XRAM[0x0832]` 重新置位（电压 bit 1、电流 bit 0）。这是“发送缓冲区一致性”
检查，不是 charger readback，也不等价于传输成功确认。此前写成“失败必然重新 pending、
形成完整闭环”过强，现已撤回；至少在这条局部路径里，transport failure 是否由更外层状态机
再次排队仍需另行证明。

### IT5570 数据手册能证明什么

`ref/IT5570_A_V0.3.1_U.pdf` 描述的是相邻 IT5570 A stepping，而本机是 IT5571 D，不能
直接套用芯片寄存器偏移。手册的外设总览列出 6 路 SMBus master、3 路 slave 和 ADC，
但没有片上 Fuel Gauge 或 battery charger 模块。这与固件行为相符：EC 通过 SMBus 获取
Smart Battery 数据并计算策略，真正的容量学习和电压执行仍位于外部 Fuel Gauge/charger。
该手册不能单独证明 `EC[0x04xx]` 或 `XRAM[0x09xx]` 的 OEM 软件字段含义。

## 尚未完全命名的电池函数

| 地址/入口 | 已知行为 | 尚缺信息 |
|---|---|---|
| `0x4433` | 批量刷新厂商电池遥测；`0x42` 提供容量因子初值，`0x43` 更新循环次数缓存 | Fuel Gauge 型号、两个厂商寄存器名称 |
| `0x4970` | 容量状态机对候选因子限幅并更新 `XRAM[0x0342]` | 候选量及上下界的物理语义 |
| `0x5A8E` | 已命名为 `initialize_battery_telemetry`；内部包含原 `0x5AFC/0x5BB3` 片段 | 内部 ROM 算术 helper 的精确舍入 |
| `0xB99D` | 已识别为无 AC 时按请求档位和 RSOC 配置平台功耗/性能表 | 各表项的 PL/TGP 人类单位；它不是容量学习函数 |
| `0x863A` | 已命名为 `check_battery_trip_point`，SCI `0x89` 对应 DSDT `_Q89` | 去抖 tick 的实际时间单位 |
| `0x2B85`、`0x3DD9` | BatteryAlert 与电源状态处理 | 告警位逐项含义 |
| `0xA533/0xA54E/0xA569` 等 | Smart Battery/SMBus 命令读取和缓存 | 两路总线、slave address 与错误码命名 |

当前最有价值的后续验证是周期性读取 `0x030E–0x030F`、`0x0438–0x0439`、`0x0491`、
`0x04A2–0x04A7`、`0x0522–0x0523` 和内部 `0x09C7–0x09CA`，在温度、循环次数和
High/Middle/Health 模式变化时建立只读时间序列。这能直接验证 4.10 V/cell 累计条件、
计数器调度周期和最终 mV 降额，不需要修改或刷写 EC。
