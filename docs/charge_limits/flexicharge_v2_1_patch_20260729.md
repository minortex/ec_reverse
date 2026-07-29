# FlexiCharge v2.1 EC 固件补丁记录（2026-07-29）


## 1. v1/v2 的修正背景

仅 hook `bank2:D729` 不足以实现稳定迟滞，因为 `D713`、`D718`、`D722`、`D727` 的四条前置条件失败路径会直接进入原厂 `bank2:D73A`，执行无条件清 `0x07B9.bit7`：

```nasm
MOV  DPTR,#0x07B9
MOVX A,@DPTR
ANL  A,#0x7F
MOVX @DPTR,A
```

因此 v2 起使用双 hook：

```text
full 0x1D729 / bank2:D729 -> fixed-common cave
full 0x1D73A / bank2:D73A -> fixed-common cave
```

v2.1 在 v2 的基础上进一步整理 `0/100` 与 invalid pair 语义。

## 2. v2.1 寄存器定义

```text
EC[0x07B9].bit[6:0] = high，上限；raw high=0 表示 100%/unrestricted
EC[0x07B9].bit7     = 原厂 stop/inhibit 状态位
EC[0x07D0].bit[6:0] = low，下限；low=0 表示禁用迟滞下限
EC[0x07D0].bit7     = cycle-active phase，v2/v2.1 新增的易失历史状态
```

有效 FlexiCharge 迟滞窗口必须满足：

```text
1 <= low <= 95
high != 0
low < high
```

正常迟滞状态机：

| 当前条件 | `0x07D0.bit7` | `0x07B9.bit7` | v2.1 决策 |
|---|---:|---:|---|
| `SOC <= low` | 1 | 0 | 开始/继续一个 low-started charge cycle |
| `low < SOC < high`，phase=1 | 1 | 0 | 中途拔插后继续该周期 |
| `SOC >= high` | 0 | 1 | 结束周期并请求停充 |
| `low < SOC < high`，phase=0 | 0 | 1 | 首次插入或已结束周期，保持停充 |

`cycle-active` 必须存在，因为只看当前 SOC 无法区分“首次在中间区间插入”和“从低点启动后中途拔插再插入”。

## 3. v2.1 对 `0/100` 与 invalid pair 的语义

v2.1 采用以下定义：

| 配置 | 行为 |
|---|---|
| `low=0, high=0` | canonical disabled，等价 0/100 unrestricted，跳过 FlexiCharge 逻辑 |
| `low=0, high>0` | 兼容官方 upper-only，只按原厂上限逻辑运行 |
| `high=0, low>0` | invalid/unrestricted，清 stop 与 phase，跳过上限限制 |
| `high <= low` | invalid/unrestricted，清 stop 与 phase，不退化成错误的 upper-only |
| `low>=96` | invalid/unrestricted，清 stop 与 phase |
| `1 <= low < high, high!=0` | v2.1 FlexiCharge 迟滞 |

invalid/unrestricted 路径只清运行态 bit：

```text
0x07D0.bit7 = 0
0x07B9.bit7 = 0
```

不强制擦除两个寄存器的低 7 位，以避免主机分两步写 `0x07D0`、`0x07B9` 时被 EC 抢先清掉配置值。功能上则已经等价 disabled/unrestricted，不会把错误的 high 值当作有效上限导致停充。

## 4. 插桩位置与产物

v2.1 基于 `cal-direct-mode-locked-apexist-ignore-20260723`，固件产物放在 build 目录：

```text
build/flexicharge-v2.1-20260729/
  GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-flexicharge-v2.1-20260729.bin
```

生成结果：

```text
size:   262144
sha256: 41a193ce525e1d87cea5d31b30e29eb9755e60b9d25862b187ed234980749bd8
```

原始 opcode 解码后的 hook：

```text
full 0x1D729 / bank2:D729: 02 7E 6A -> fixed-common:7E6A
full 0x1D73A / bank2:D73A: 02 7E 9E -> fixed-common:7E9E
fixed-common cave: 0x7E6A-0x7F41，共 216 字节
返回点: bank2:D741
```

相对基础镜像，仅修改：

```text
fixed-common cave: 0x07E6A-0x07F41
bank2 hook:        0x1D729-0x1D72B
bank2 hook:        0x1D73A-0x1D73C
```

补丁只读写 `0x04AB`、`0x07B9`、`0x07D0`，不发起 Gauge/Charger SMBus 命令，不直接修改充电电压或电流，也不绕过原厂温度、适配器、故障和充电器安全门。

## 5. `0x07B9` 恢复与 `0x07D0.bit7` phase 的重启语义

`0x07B9` 有运行期重新装载/合并路径，但现有证据不支持“EC 将完整 0x07B9 跨掉电保存在内部 NVRAM”。已确认路径更像：OEM 配置读取结果 `0x0A47/0x087F` 提供低 7 位，上层 BIOS/GCU 也可能从主机 NVRAM 启动后重写阈值；`0x07B9.bit7` 是当前运行态，重启后通常由调度器重新计算。

v2.1 的 `0x07D0.bit7` 应视为易失 phase：普通拔插适配器不会重置 EC/XRAM，所以可保留中断周期；真正 EC reset 后 phase 可能丢失，丢失时按 0 处理是保守选择，即中间区间默认保持停充，直到 SOC 回落到 low。
