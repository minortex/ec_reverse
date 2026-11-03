# EC 充电阈值持久化与 FlexiCharge v2.2（2026-07-29）

## 1. 问题与结论

调查结果已经能完整解释观察到的现象：

- `0x07B9` 有原厂的持久化与冷启动恢复闭环。
- `0x07D0` 只是普通 XRAM，原厂没有任何持久化、恢复或消费逻辑。
- 因此“重启保持、拔电关机再开丢失”不是 v2.1 的逻辑错误，而是 EC 供电状态不同造成的：
  - 热重启时 EC/XRAM 没真正掉电，`0x07D0` 还在。
  - 拔 AC 后关机，EC 最终掉电，XRAM 清空，`0x07D0` 变成 0。
  - `0x07B9` 随后被原厂恢复链重新装载，所以仍然存在。

## 2. 原厂上限恢复链

原厂上限恢复链如下：

```text
冷启动/配置恢复
  → 读取 OEM 0x47
  → 读取 OEM 0x48
  → 两个结果分别进入 0x0A47 / 0x0A48
  → 检查两份是否一致
  → 检查阈值范围
  → 写入 0x087F
  → 保留 0x07B9.bit7，合并 0x087F[6:0]
  → 得到恢复后的 0x07B9
```

如果 `0x47/0x48` 不一致或无效，原厂会回退：

```text
0x087F = 100
0x07B9 = 0
```

这里 `0x47` 和 `0x48` 不是 high/low 两个阈值，而是同一个上限值的冗余副本。

## 3. 原厂上限写回链

写回方向也找到了。原厂在 `bank2:D741` 后比较：

```text
0x087F != 当前 high
```

不一致时会：

```text
0x087F = 当前 high
0x0802 = 6
```

`0x0802` 是延迟倒计时。倒计时降到 1 时执行：

```asm
LCALL 0x1B88
```

从原始 opcode 解码，`0x1B88` 是：

```asm
MOV  DPTR,#0xDB98
LJMP 0x1100
```

而 `0x1100` 选择 bank0，所以真正的提交处理是：

```text
bank0:DB98
```

该例程属于原厂通用配置同步/校验框架。后续精确解码表明它包含两个独立映射块：

```text
4 字节主配置块： 0x9400, 0x9401, 0x0706, 0x087F
16 字节配置块：  0x0770 ... 0x077F
```

随后冷启动初始化在 `bank0:DE03` 再把 `0x087F` 合并进 `0x07B9`。所以 `0x07B9` 的持久性是固件明确实现的，不是偶然残留。

## 4. v2.1 冷断电缺口

对于 `0x07D0`，整个原厂二进制中：

```text
MOV DPTR,#0x07D0:0 处
```

也没有：

- 对应的 shadow 字节；
- `0x0802=6` 提交触发；
- 配置表映射；
- 冷启动恢复函数；
- OEM 冗余读取；
- 范围验证。

所以当前 v2.1 在冷启动后的实际状态会变成：

```text
high = 恢复后的 0x07B9
low  = 0
```

按照我们定义的兼容语义，这会退化成“官方 upper-only 模式”。这意味着机器关机且 EC 真正掉电后，仍能保持官方上限停充，但 FlexiCharge 的 lower threshold/迟滞启动条件消失。你担心的“关机时不能完整控制充电”是成立的。

## 5. 修复约束

修复方向不能简单把 `0x07D0` 塞进 `0x47/0x48`：

- 这两个值是原厂上限的冗余副本；
- 改变格式会破坏官方上限兼容性；
- 如果事务最终落在电池 Gauge 的 OEM 非易失区，擅自写未知 OEM 命令还存在锁定、校验失败甚至触发 Gauge 安全机制的风险。

因此 v2.2 采用以下结构：

```text
保留原厂：
  0x087F / 0x47 / 0x48 → high

新增独立持久化槽：
  persistent_low_shadow → low

主机写 0x07D0：
  更新 persistent_low_shadow
  请求原厂配置提交

冷启动：
  原厂恢复 high
  补丁恢复 low → 0x07D0[6:0]
  bit7 phase 根据 SOC 与 low/high 重新构造
```

## 6. 16 字节块与槽位审计

原厂映射表位于 `bank0:602B`，16 个 big-endian 地址连续为：

```text
0770 0771 0772 0773 0774 0775 0776 0777
0778 0779 077A 077B 077C 077D 077E 077F
```

`bank0:DC02` 在冷启动记录有效时把非易失缓冲 `0x0200..0x020F` 依次恢复到这些地址；
`bank0:DC2A` 反向收集 16 个映射字节并进入原厂保存事务。保存请求协议为：

```text
EC[0x077E] = 0xA5
EC[0x077F] = 0x78
```

调度器在 `bank0:C4F5-C527` 识别请求，将握手改成 `0x55/0xAA`，然后调用
`bank0:DC2A` 提交整个块。

对完整 256 KiB 固件搜索 `MOV DPTR,#0x077x` 并检查上下文后的分类如下：

| 地址 | 原厂直接行为 | v2.2 处理 |
|---|---|---|
| `0x0770` | 平台 ID 覆盖值；初始化可写 `0xFF`，其后主要只读 | 不修改 |
| `0x0771` | 平台/机型选择，只发现读取 | 不修改 |
| `0x0772` | 平台子类型；初始化检测可写 `1/2` | 不修改，但整块提交会包含当前值 |
| `0x0773` | 原厂无直接引用 | `bit7=有效标记，bits6:0=low` |
| `0x0774..0x077D` | 原厂无直接引用 | 不修改 |
| `0x077E..0x077F` | 保存握手 | 使用原厂 `A5/78` 请求 |

因此 `0x0773` 是当前平台最合适的保留槽。需要保留一个边界结论：提交粒度是整个块，
不是单字节。v2.2 不会写其他配置字节，但会把它们当时的值一并提交。原厂本身使用同一
握手保存该块，说明该块按设计应在握手时保持配置一致；不过其他机型是否复用保留字节，
仍不能由当前固件证明。

## 7. v2.2 实现

补丁源：

```text
firmware_mods/GXxHXxx_21.200/
  cal-direct-mode-locked-apexist-ignore-flexicharge-v2.2-20260729/
    patch_flexicharge_v22.py
```

持久化格式：

```text
EC[0x0773].bit7     = v2.2 记录有效
EC[0x0773].bits6:0  = lower threshold
EC[0x07D0].bit7     = 易失 cycle-active phase，不写入 NVM
```

策略每次运行都会读取 shadow，但只有以下情况才触发提交：

```text
0x0773.bit7 == 0
或者
(0x0773 & 0x7F) != (0x07D0 & 0x7F)
```

对应核心机器码语义：

```asm
MOV  DPTR,#0x0773
MOVX A,@DPTR
JNB  ACC.7,update_low_shadow
ANL  A,#0x7F
XRL  A,B
JZ   low_shadow_current

update_low_shadow:
MOV  A,B
ORL  A,#0x80
MOVX @DPTR,A
MOV  DPTR,#0x077E
MOV  A,#0xA5
MOVX @DPTR,A
INC  DPTR
MOV  A,#0x78
MOVX @DPTR,A
```

因此周期任务不会每 1-2 秒重复发起 flash 提交。有效且相同的 low 直接跳过；记录尚未
初始化时，即使低 7 位碰巧相同，也会建立一次有效记录。

冷启动 hook 位于原厂 `bank0:DE54`：

```text
原始 file 0x0DE54 / bank0:DE54: 12 DE 03
补丁 file 0x0DE54 / bank0:DE54: 12 7E 6A
```

`fixed-common:7E6A` 包装器先执行原始 `LCALL bank0:DE03` 恢复 high，再检查
`0x0773.bit7`；记录有效时把低 7 位写到 `0x07D0`。phase 初始为 0，随后由实时 SOC
重建：中间区间默认保持停充，`SOC<=low` 才开始周期，`SOC>=high` 保持停充。

完整控制流修改：

```text
file 0x0DE54 / bank0:DE54 -> fixed-common:7E6A  冷启动恢复
file 0x1D729 / bank2:D729 -> fixed-common:7E7B  正常策略入口
file 0x1D73A / bank2:D73A -> fixed-common:7EAF  原厂 preclear 汇合入口
fixed-common cave: 0x7E6A-0x7F70，263 bytes
```

固件产物：

```text
build/flexicharge-v2.2-20260729/
  GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-flexicharge-v2.2-20260729.bin

size:   262144
sha256: ff6683769da655f3cafa364ddc9cfcaad84ad28fa7122191d3fefad00a7a19cf
```

补丁没有新增 Gauge/Charger SMBus 命令，不直接操作充电电压、电流或电池 data flash。

## 8. 真机验证

稳定状态下首次读取：

```text
0770 FF   0771 FF   0772 01   0773 A8
0774-077D FF
077E 55   077F AA
```

解释：

```text
0x0772=0x01：平台子类型已经完成初始化
0x0773=0xA8：v2.2 有效，持久化 low=0x28=40
0x077E/0x077F=55/AA：原厂块标记/先前请求已被消费
```

随后执行：

```text
mfc bat set -d 60 -u 80

0x07D0: 28 -> 3C
0x07B9: D0 -> D0
RSOC: 71%
mode: middle-band hold
```

再次读取配置块：

```text
0770 FF   0771 FF   0772 01   0773 BC
0774-077D FF
077E A5   077F 78
```

这次观测直接证明：

1. lower threshold 从 40 改为 60 后，shadow 从 `0xA8` 正确变为 `0xBC`；
2. v2.2 发出了原厂 `A5/78` 整块提交请求；
3. `0x0770-0x0772` 和 `0x0774-0x077D` 在修改前后保持不变；
4. 上限、stop bit 和中间区间 hold 状态没有被持久化动作扰动。

第二次 dump 捕获的是“提交请求待消费”时刻；它证明触发链已经执行，但单凭该帧不能替代
随后观察 `55/AA` 或冷断电恢复 60 的验证。此前 `A8 + 55/AA` 已证明同一机制至少完成过
40 的记录提交和握手消费。

## 9. 测试与剩余边界

自动测试覆盖 v2.1 回归、三种配置返回值、冷启动 hook、槽位映射、有效位初始化、阈值变化
提交，以及阈值相同时不写 shadow/握手：

```text
26 passed
```

剩余边界：

- 关机后 EC 真正掉电时，`0x07B9.bit7` 和 `0x07D0.bit7` 都会丢失；它们是运行态，启动后
  根据持久化 high/low 与实时 SOC 重建。
- `A5/78` 提交整个 16 字节块。当前平台实测除 `0x0773` 和握手外其他字节未变化，但其他
  机型是否复用保留槽仍需各自审计。
- 配置事务逻辑块标识为 `0x01F0`（16 字节）和 `0x01F4`（4 字节）。介质高概率是 EC
  外接 SPI flash 的配置/EEPROM 仿真区，而非电池 Gauge；逻辑块标识不能直接当成 flash
  文件偏移。

静态分析产物位于：

```text
build/charge-limit-restore-path-20260729/
```
