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

固件轮询符合 Smart Battery Data 命令布局的字段，包括：

| SBS 命令 | 标准含义 | EC 公开数据 |
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

`CODE:0x5A8E initialize_battery_telemetry` 是此前 `0x5AFC–0x5BB3` 片段所属的真实入口。
它先校验多组 Fuel Gauge 缓存及镜像值，再计算并发布 `LastFullCapacity`，随后批量发布
DesignCapacity、DesignVoltage、RemainingCapacity 等 ACPI `_BIF/_BST` 数据。因此原先把
`0x5AFC` 和 `0x5BB3` 当作两个待定函数并不正确；它们是同一初始化/发布流程内的基本块。

这段代码可确认 `LastFullCapacity` 不是简单的双字节原样复制：内部容量值和
`XRAM[0x0342]` 的比例/校准字节参与了以 100 为常数的乘除运算，结果才写到
`EC[0x0404:0x0405]`。但 `0x0342` 的 Fuel Gauge 命令来源以及运行库算术 helper 的精确
舍入方式尚未恢复，所以目前应称它为“容量比例/校准因子”，不能直接命名为 SOH 百分比，
也不能据此断言 EC 自己执行库仑积分学习。

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
窗口不会增加应力。电源状态 `EC[0x0490].1` 失效时，函数会把 `0x09C7–0x09CA` 全部清零，
所以它是当前供电/充电会话内的高压热应力，不是写入 Flash 的终身累计量。

温度不会直接乘最终电压补偿，而是决定每次向高压应力累计值增加多少：

| 电池温度原始值 | 约摄氏温度 | 应力增量 |
|---:|---:|---:|
| `< 0x0BD6` | `< 29.9°C` | `0x0100` |
| `0x0BD6–0x0C39` | `29.9–39.8°C` | `0x0300` |
| `>= 0x0C3A` | `>= 39.9°C` | `0x0700` |

累计更新前还检查 `0xFDE8`（65000）附近的饱和值。因此同样处于 4.10 V/cell 以上时，
约 30–40°C 的老化累计速度是低温档的 3 倍，约 40°C 以上是 7 倍。

以第一档 `0x10E0` 为例，考虑离散增量后，持续处于高压条件约需：低温档 17 个窗口
（约 12.2 小时）、中温档 6 个窗口（约 4.3 小时）、高温档 3 个窗口（约 2.2 小时）。
这些时间会因不满足电压/电源门槛而延长；断开相应电源状态则重新从零开始。

### 补偿档位决策

函数按从严重到轻微的顺序选择每电芯基础降额。循环次数不是唯一输入；高压应力、保护模式
和少量尚未命名的状态门槛可以把结果提升到更高一档：

| 每电芯降额 | 循环次数门槛 | 高压应力门槛 | 其他已确认条件 |
|---:|---:|---:|---|
| `250` | `>= 550` | `>= 0x3DE0` (15840) | 还存在一个通常恒真的 GPIO 镜像范围检查，见下文 |
| `200` | `>= 450` | `>= 0x2D00` (11520) | Health 模式 `EC[0x07A6].5:4 == 10b` 至少选此档 |
| `150` | `>= 350` | `>= 0x21C0` (8640) | 内部 battery-pack 状态 bit 4 可将最低档提高到此处 |
| `100` | `>= 250` | `>= 0x1950` (6480) | Balanced 模式 `EC[0x07A6].5:4 == 01b` 至少选此档 |
| `50` | `>= 150` | `>= 0x10E0` (4320) | 模板中的辅助等级门槛为 7，但本项目构建写入的 `XRAM[0x0A54]` 固定为 1，不能触发 |
| `0` | `< 150` | `< 0x10E0` | 且所有辅助条件允许 |

表格中的逻辑是“任一老化维度达到该档即可”，不是只有循环次数同时满足才降额。更高档先
匹配，所以例如循环 200 次但高温高压累计达到 `0x21C0` 时仍会选 150 档。

`XRAM[0x0857]` 已追到硬件来源：它的 bit 0 经过 5-tick 去抖后镜像 GPIO data-mirror
`0x1664` bit 3。按 IT5570 A 的 GPIO map，`0x1664` 是 GPDMRD、bit 3 对应 GPD3；该相邻
芯片的封装表把 GPD3 复用为 `ECSCI#`，但 IT5571 D 板上的实际 pin mux 尚未验证。当前
找到的所有直接写入只会翻转 `0x0857`.0，所以函数中的“值小于 25”检查在正常取值 0/1
下恒真，不会主动把降额推到 250 档。它更像复用模板留下的保护条件，不能再把它描述为已知
会触发的老化门槛。

对 `XRAM[0x0A54]` 的完整到达值分析进一步推翻了“保护等级聚合器”解释。老化函数先调用
`CODE:0xD1BB` 检查 `EC[0x0497].0`；该位无效时函数直接退出，有效时 helper 明确令
`R7 = 1`。随后 `EC[0x0490].2` 条件成立才调用 `CODE:0x1D08`，返回后未经变换便执行：

```text
R7 = 1
call CODE:0x1D08 apply_platform_aux_derating_hooks
XRAM[0x0A54] = R7
```

调用 `0x1D08` 的前提正是 `EC[0x0490].2 != 0`，而 helper 返回时 DPTR 仍指向
`EC[0x0490]`。所以 `0x1D08` 的第一条读取必为非零并立即跳到公共尾部；后面那组平台检查、
GPU 状态和 trip-point 调用在这条老化调用路径上不可达。公共尾部只调用 wrapper `0x1C72`
和 `0x1C78`；wrapper 最终切到 bank0 的 `0xA71B`、`0xA71D`，而两个目标在本固件中都
只是单字节 `RET`，全程没有改写 `R7`。因此只要执行到降额决策，`XRAM[0x0A54]` 就确定
为 1。后续代码虽然保留 `< 19`、`< 13`、`< 10` 和 `< 7` 等模板门槛，但本项目配置下均
不会由这个字段触发。2.12 里不存在需要继续寻找的 `0x0A54`“各保护源”；它是带空平台
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

`XRAM[0x030E:0x030F]` 是未降额的 16 位 charger voltage target。最终机器码执行完整的
16 位带借位减法：

```text
pack_derating = series_cells * per_cell_derating
EC[0x0522:0x0523] = base_charge_voltage_target - pack_derating
```

忽略尚未命名的辅助状态后，核心控制流可以整理为：

```c
cells = ((battery_pack_status & 0xc0) == 0xc0) ? 4 :
        ((battery_pack_status & 0xc0) == 0x80) ? 3 : 2;

if (battery_voltage_mv >= cells * 4100 && prescalers_expired()) {
    stress += temperature_raw < 0x0bd6 ? 0x0100 :
              temperature_raw < 0x0c3a ? 0x0300 : 0x0700;
}

derating_per_cell = select_highest_tier(cycle_count, stress,
                                         battery_mode, auxiliary_state);
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
应改称 **queued charge voltage**：`CODE:0x3EF7` 在排队前直接把
`EC[0x0522:0x0523]` 复制进去；它不是从 charger 读回的 applied value。因此
`0x0522:0x0523` 确实是 charger 控制状态机的实际目标输入，但不能用 `0x0836:0x0837`
证明外部 charger 已接受或实际执行该电压。

该检测函数还有一个此前被空函数边界掩盖的分支：正常电池路径不活动时，它不比较
`0x0522:0x0523`，而是检查 queued mirror 是否等于 `0x3138`（12600 mV）。这很像 3S
平台的安全/初始化回退目标，但触发它的上游条件仍需继续命名，因此暂不解释成通用默认值。

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
| `0x5A8E` | 已命名为 `initialize_battery_telemetry`；内部包含原 `0x5AFC/0x5BB3` 片段 | `0x0342` 的 Fuel Gauge 命令语义、算术 helper 的精确舍入 |
| `0xB99D` | 已识别为无 AC 时按请求档位和 RSOC 配置平台功耗/性能表 | 各表项的 PL/TGP 人类单位；它不是容量学习函数 |
| `0x863A` | 已命名为 `check_battery_trip_point`，SCI `0x89` 对应 DSDT `_Q89` | 去抖 tick 的实际时间单位 |
| `0x2B85`、`0x3DD9` | BatteryAlert 与电源状态处理 | 告警位逐项含义 |
| `0xA533/0xA54E/0xA569` 等 | Smart Battery/SMBus 命令读取和缓存 | 两路总线、slave address 与错误码命名 |

当前最有价值的后续验证是周期性读取 `0x030E–0x030F`、`0x0438–0x0439`、`0x0491`、
`0x04A2–0x04A7`、`0x0522–0x0523` 和内部 `0x09C7–0x09CA`，在温度、循环次数和
High/Middle/Health 模式变化时建立只读时间序列。这能直接验证 4.10 V/cell 累计条件、
计数器调度周期和最终 mV 降额，不需要修改或刷写 EC。
