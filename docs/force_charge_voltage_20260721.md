# 2026-07-21 强制 17600 mV 充电目标工具说明

## 1. 结论

`tools/force_charge_voltage.py` 对当前固件有效，但它不是永久修改 EC 策略，而是在运行期间
持续纠正 EC 的充电目标和发送队列。

实测表明，单独写 `EC[0x0522:0x0523]=17600` 只能维持不到一秒；周期任务
`CODE:0xD1D1` 会再次算出 16600 mV。强制工具同时维护 desired、queued 和 pending：

```text
EC[0x0522:0x0523]   = 0x44C0 = 17600 mV  desired target
EC[0x0836:0x0837]   = 0x44C0 = 17600 mV  queued target
EC[0x0832].bit1     = 1                    ChargingVoltage pending
```

因此它不仅尝试在队列更新前“抢跑”，还直接把 charger worker 实际消费的 queued mirror
保持在 17600 mV，并在每次纠正后重新请求发送 `ChargingVoltage (0x15)`。

## 2. 为什么需要持续纠正

正常控制流为：

```text
D1D1 计算 desired
    -> 0x3196 比较 desired 与 queued
    -> 0x31FC 把 desired 复制到 queued，并置 pending bit1
    -> 0x8EFB 从 queued 准备 SMBus word data
    -> 0x80D2 向 charger 0x09 发送命令 0x15
```

当前异常周期约每秒发生一次：`D1D1` 把 desired 改回 16600 mV。如果后续队列任务先运行，
16600 mV 也会被复制到 queued 并发送给 charger。

工具默认每 20 ms 检查一次。只要发现 desired 或 queued 不是请求值，就按以下顺序纠正：

1. 先写 desired 的低字节、再写高字节；
2. 先写 queued 的低字节、再写高字节；
3. 对 `0x0832` 执行 read-modify-write，置位 bit 1。

从 16600 (`0x40D8`) 改到 17600 (`0x44C0`) 时先写低字节，中间值是 16576 mV，
不会短暂形成高于 17600 mV 的目标。

## 3. 抢跑验证结果

2026-07-21 在 AC 在线、4S、电池基准 17600 mV、包电压约 16114 mV、温度 36.85 C 时，
进行了 8 秒强制测试：

```text
forcing 17600mV for 8s
corrected desired=16600mV queued=16600mV -> 17600mV
corrected desired=16600mV queued=17600mV -> 17600mV
... 后续每个 D1D1 周期均为同一模式 ...
finished corrections=9 desired=17600mV queued=17600mV
```

首次纠正后，后续八次都只看到 desired 被改成 16600，而 queued 仍保持 17600。这说明在本次
负载和 50 ms 轮询间隔下，工具每次都赶在固件把错误 desired 复制进 queued 之前完成纠正。
换言之，实际测试中的“抢跑”成功率为 8/8。

工具停止后不再写入，下一次 `D1D1` 周期恢复为：

```text
desired=16600 queued=16600 pending=0x00
```

这证明效果依赖工具持续运行，不会永久改变固件。

## 4. 能否确认 charger 收到了 17600 mV

从固件控制流可以确认：

- `0x8EFB` 在 pending bit 1 成立时选择命令 `0x15`；
- 数据来自 queued mirror，并经过 `0x00C0:0x00C1` staging；
- 目标为标准 charger 地址 `0x09` 的 8 位写地址 `0x12`；
- 工具直接设置 queued=17600 并置 pending，因此建立了完整的软件发送条件。

但 `0x0836:0x0837` 只是队列镜像，不是 charger readback。`0x0A73:0x0A75` 也是共享的
SMBus 事务状态，单次读数不能唯一归因于本次电压命令。因此当前能确认的是：

```text
工具成功保护 queued，并触发了 ChargingVoltage 发送路径。
```

尚不能仅凭 EC XRAM 证明 charger 模拟输出端已经精确采用 17600 mV。严格验证需要以下任一
手段：

- 抓取 EC 到 charger 的 SMBus，确认命令 `0x15` 数据为 `0x44C0`；
- 找到 charger 型号及其 ChargingVoltage 寄存器读回路径；
- 在充电后期测量电池包端电压，同时确认充电电流和保护状态。

电池包电压不会在写入后立刻变成 17600 mV。该命令设置的是恒压上限；实际升压速度仍取决于
SOC、ChargingCurrent、温度和 charger 自身保护。

### 80% 现场复核

工具持续运行时通过独立的公开 EC 窗口连续三次读到：

```text
desired = 17600 mV
base    = 17600 mV
```

同一时段 Linux power_supply 显示：

```text
capacity:    80% -> 82%
pack voltage: 16.37 V -> 16.56 V
current:      约 1.4–1.7 A
status:       Charging
```

这说明工具已经保持 17600 mV 目标，charger 也没有在 80% 停流。16.56 V 是当时的电池包
端电压，不是 EC 的充电目标；4S 电池在恒流阶段、尚未接近满电时低于 17.6 V 属于正常现象。
只有进入充电后期的恒压阶段，包电压才应逐渐接近目标，同时充电电流开始下降。

继续运行约 6 分钟后又观察到：

```text
capacity:     85%
pack voltage: 16.818 V
current:       2.312 A
status:        Charging
```

包电压已经超过原策略的 16600 mV 上限，且仍有明显充电电流。这构成比 EC 队列镜像更强的
物理证据：charger 没有继续按 16600 mV 恒压限制执行，强制写入已经产生实际效果。

## 5. 使用方法

在仓库根目录直接运行。默认目标为 17600 mV、轮询间隔 20 ms、持续 3 小时：

```bash
sudo python3 tools/force_charge_voltage.py
```

需要短时间验证时只覆盖持续时间，例如运行 10 分钟：

```bash
sudo python3 tools/force_charge_voltage.py \
  --hold-seconds 600
```

`--hold-seconds` 的允许范围为 1–10800 秒。`--millivolts` 和 `--interval` 仍可覆盖，但通常
无需指定。

停止工具可按 `Ctrl-C`；进程停止后不会恢复旧 XRAM 快照，而是让 EC 在下一个周期自然恢复
自己的 16600 mV 策略。

I2EC 工具会独占锁定 `/dev/port`。强制工具运行时不要同时启动 `watch_i2ec_battery.py`、
`i2ec_rw.py` 或其他直接访问同一 I2EC 端口的程序。

工具每 60 秒打印一次 `pack/rsoc/temp/corrections` 状态。判断是否生效应看输出中的 desired、
queued 以及系统是否仍为 `Charging`，不能只用当前 pack voltage 是否达到 17600 mV 判断。

## 6. 安全条件

工具每秒重新检查以下条件，任一不满足就终止：

| 条件 | 限制 |
|---|---|
| 电池提供的 base | 必须恰好等于请求值，当前为 17600 mV |
| 电源 gate | `0x0490 & 0x07 == 0x07` |
| 电池会话 | `0x0497.bit0 == 1` |
| 串数 | `0x0491.bits7:6 == 3`，即 4S |
| 温度 | 低于约 45 C |
| 当前包电压 | 必须低于请求目标 |
| I2EC | `0x200D` 低两位必须为 `11b`，即读写模式 |

17600 mV 对 4S 电池相当于 4.4 V/cell，属于高电压充电。这里允许该值的依据是电池遥测本身
持续提供 17600 mV base，而不是工具自行提高电池声明的上限。即便如此，也不应无人值守长期
运行；出现温度快速上升、鼓包、异常气味、充电电流异常或包电压接近/超过目标时应立即停止。

## 7. 局限与永久修复方向

轮询写入仍然存在调度竞争，不能提供形式上的原子保证。极端系统负载下，固件可能在工具两次
检查之间短暂把 16600 mV 排队。工具直接维护 queued 和 pending 可以缩短该窗口，但不能把
运行时竞争变成永久策略。

永久解决应修改 EC 固件，使 `D1D1` 不再获得异常的辅助等级，或在中断入口完整保存/隔离
`R0..R7`。直接长期篡改 `0x0490` gate、关闭电池会话或持续写共享 scratch `0x0A54` 会影响
更多电源状态机，不适合作为替代方案。
