# EC 交流/电池性能模式与功耗限制逻辑调查（2026-07-26）

## 1. 调查范围与结论摘要

本次调查针对以下现象：

- 官方控制台通过写 EC 寄存器切换 Gaming / Office / Turbo 等性能模式；
- `ryzenadj -i` 能观察到 STAPM、PPT Slow（SPPT）和 PPT Fast（FPPT）随模式变化；
- 同一个模式的功耗限制会受到电池电量、是否插入电源等状态影响；
- 直接通过 `ryzenadj` 写入 SMU 的限制，之后可能又被 EC 覆盖。

调查对象为当前机器及其匹配固件：

```text
Model:           WUJIE 14 Series GX4HRXL
Board:           GX4HRXL
Firmware family: GXxHXxx 21.200
```

主要结论：

1. `EC/XRAM[0x0751]` 是官方控制台使用的“请求模式”寄存器，但它不是直接的瓦数寄存器。
2. 固件使用 `0x0751 & 0x90` 区分 Gaming、Office、Turbo；FanBoost 所在的 bit6 不参与功耗表选择。
3. EC 会先检查经过处理的 AC 状态 `EC[0x0490].bit0`。未插电且 `EC[0x06E6] == 1` 时，进入电池功耗表路径。
4. 电池路径还会根据平台 ID、基础性能模式、`EC[0x0983].bit1` 和 RSOC `EC[0x04AB]` 选择 ROM 表项。
5. ROM 表中的 STAPM/SPPT/FPPT 是候选值或上限，不一定直接成为最终值。
6. 最终仲裁函数会在多组非零候选约束中选择最严格的值，结果存入 `0x086B/0x086C/0x086E`，再由后续路径发布给 SMU。
7. 当前 GX4HRXL 在插电 Turbo 模式下实测为 `54/65/70 W`，EC 最终值与同时读取的 `ryzenadj -i` 完全一致。
8. 因此，在电池上写入 Turbo 只会选择“Turbo 的电池表”，不会获得 Turbo 的插电功耗；而直接用 `ryzenadj` 修改 SMU，不会同步修改 EC 的候选状态，之后可能被 EC 重新发布的值覆盖。

> 注意：本文以 EC 固件二进制为蓝本。反汇编标签、函数边界、伪 C 和已有文档只用于辅助阅读；涉及 `LCALL/LJMP` 的关键控制流均从调用点原始 opcode 核对。

---

## 2. 信息来源与可信度顺序

### 2.1 固件与反汇编材料

主要固件和拆分映像：

```text
samples/GXxHXxx_21.200
samples/disasm/main-bank0.bin
samples/disasm/main-bank1.bin
```

本文采用以下可信度顺序：

1. 原始 EC 固件二进制及调用点 opcode；
2. 从二进制解析出的 ROM 表内容；
3. 当前机器 EC 寄存器与 `ryzenadj` 的同时实测；
4. d52 反汇编和伪 C；
5. 旧调查文档或其他机型的经验结论。

### 2.2 官方控制台逆向资料

参考资料位于：

```text
/home/texsd/codes/mech-forza-control/docs/llm/ec-mode-switch.md
/home/texsd/codes/mech-forza-control/docs/perf.md
```

其中模式写入接口有参考价值，但旧文档中的固定功耗数字不能直接套用于当前 GX4HRXL/21.200。例如旧资料中的 Turbo 数值与本机当前插电实测的 `54/65/70 W` 不一致。

---

## 3. 官方控制台的模式寄存器

官方 GCU 使用：

```text
EC/XRAM[0x0751]
```

作为请求模式寄存器。不考虑 FanBoost 时，已知写入值如下：

| 模式 | GCU 写入值 | 固件用于功耗表选择的 `0x0751 & 0x90` |
|---|---:|---:|
| Gaming | `0x00` | `0x00` |
| Office | `0xA0` | `0x80` |
| Turbo | `0x10` | `0x10` |

FanBoost 使用 bit6。由于功耗表选择路径只保留 `0x90` 掩码，bit6 不影响 Gaming/Office/Turbo 的功耗表选择。

官方控制台的模式状态组合还会使用 `EC[0x0726].bit7`，但后续固件核对表明它不是 RAM 风扇表的唯一或直接主 gate；实际 AP Custom 路径还依赖 `EC[0x0727].bit6`、APExist 和 AP 风扇管理位，详见第 15–20 节。

调查到的电池功耗表选择函数不读取这些 Custom 风扇 gate。因此 Custom 没有独立的第四套电池功耗表，而是仍落在 Gaming/Office/Turbo 三个基础功耗模式之一，再叠加 AP 提供的 TCC、功耗/profile 和风扇控制。

这里需要区分：

- `0x0751`：模式/策略选择输入；
- ROM 功耗表：该模式在特定供电、电量和平台条件下的候选限制；
- `0x086B/0x086C/0x086E`：经过全局仲裁后的最终功耗限制。

---

## 4. 从原始 opcode 核对关键控制流

### 4.1 bank1 调用点

在 bank1 的原始代码中，相关调用点为：

```asm
8E47: 12 1C 36    LCALL 0x1C36
8E4A: 12 1C 3C    LCALL 0x1C3C
```

8051 的 `LCALL` opcode `0x12` 后接 16 位绝对地址，因此目标由调用点原始字节直接解码为：

```text
0x1C36
0x1C3C
```

两者都位于 fixed common 区，不能依据 d52 的函数名或 `jump_xxxx` 标签反推其真实目标。

### 4.2 fixed-common veneer

在 `main-bank0.bin` 对应 fixed common 区核对到：

```asm
1C36: 90 B7 61    MOV  DPTR,#0xB761
1C39: 02 11 14    LJMP 0x1114

1C3C: 90 B9 9D    MOV  DPTR,#0xB99D
1C3F: 02 11 14    LJMP 0x1114
```

其中 `0x02` 是 `LJMP`，后两字节直接给出目标 `0x1114`。该公共 veneer 先把 banked 目标放入 DPTR，再跳到公共 bank 调度入口。

地址分类如下：

| 地址 | 区域 | 作用 |
|---|---|---|
| `0x1C36` | fixed common | 设置 bank1 目标 `0xB761` 的 veneer |
| `0x1C3C` | fixed common | 设置 bank1 目标 `0xB99D` 的 veneer |
| `bank1:0xB761` | banked | 最终功耗候选仲裁 |
| `bank1:0xB99D` | banked | AC/电池功耗配置构建 |
| `0x1114` | fixed common | bank 调度入口 |

因此关键调用顺序为：

```text
bank1:0x8E47
  -> fixed-common:0x1C36
  -> bank dispatcher
  -> bank1:0xB761

bank1:0x8E4A
  -> fixed-common:0x1C3C
  -> bank dispatcher
  -> bank1:0xB99D
```

> 上述目标来自原始 opcode，不依赖反汇编器推断出的函数边界。

---

## 5. AC/电池路径的门控条件

### 5.1 `bank1:0xB99D` 中的分支

原始控制流片段：

```asm
B9EA: 12 DF 87    LCALL 0xDF87
B9ED: 70 05       JNZ   0xB9F4
B9EF: 12 DF 77    LCALL 0xDF77
B9F2: 60 11       JZ    0xBA05
```

### 5.2 AC 状态帮助函数 `0xDF87`

原始字节对应：

```asm
DF87: 90 04 90    MOV  DPTR,#0x0490
DF8A: E0          MOVX A,@DPTR
DF8B: 54 01       ANL  A,#0x01
DF8D: 22          RET
```

因此该帮助函数精确返回：

```c
EC[0x0490] & 0x01
```

可以确认：

```text
EC[0x0490].bit0 = EC 内部使用的合格/去抖后 AC present 状态
```

它不一定等同于某个瞬时物理 GPIO，而是 EC 功耗策略实际消费的 AC 状态。

### 5.3 工作状态帮助函数 `0xDF77`

原始字节对应：

```asm
DF77: 90 06 E6    MOV  DPTR,#0x06E6
DF7A: E0          MOVX A,@DPTR
DF7B: 64 01       XRL  A,#0x01
DF7D: 22          RET
```

其返回值等价于：

```c
EC[0x06E6] ^ 1
```

结合调用点的 `JZ`，电池配置路径只有在以下条件同时满足时才执行：

```text
EC[0x0490].bit0 == 0
EC[0x06E6]       == 1
```

可以确认 `0x06E6 == 1` 是本路径要求的活动工作状态。其他写入点还会将该寄存器写成 `3`、`5` 等值，表现得像睡眠/关机一类转换状态，但在没有完整状态机证据前，本文只把 `1` 谨慎称为“活动工作状态”，不把它绝对命名为 ACPI S0。

### 5.4 两条路径写入的候选槽位

在 AC 路径中：

- 清除 `EC[0x0983].bit1`；
- 使用 AC/模式候选槽位：

```text
EC[0x0873]
EC[0x087B]
EC[0x088B]
```

在电池路径中：

- 先清除上述 AC 候选槽；
- 再填充电池候选槽位：

```text
EC[0x0872]
EC[0x087A]
EC[0x088A]
```

因此 AC 与电池不是简单对最终瓦数做乘法缩放，而是构建不同来源的候选限制，再交给统一仲裁逻辑。

---

## 6. 当前 GX4HRXL 的平台识别和功耗表选择

### 6.1 调查时的只读平台状态

```text
EC[0x07C3] = 0x05
EC[0x0770] = 0xFF
EC[0x0885] = 0x41
EC[0x06E6] = 0x01
```

平台 ID 判断帮助函数 `0xD4C4` 的实际语义等价于：

```c
return EC[0x07C3] == requested_id ||
       EC[0x0770] == requested_id;
```

所以本机匹配平台 ID `0x05`，不会进入 ID `0x04` 的覆盖分支。

### 6.2 profile `0x41` 的模式表指针

在当前平台/profile 组合下，三个基础模式使用以下 ROM 表：

| 模式 | ROM 表地址 |
|---|---:|
| Gaming | fixed common `0x76F2` |
| Office | fixed common `0x773A` |
| Turbo | fixed common `0x76AA` |

对应指针帮助函数的原始结果为：

```text
E840 -> 0x76F2
E84B -> 0x773A
E856 -> 0x76AA
```

这些地址和表内容是按当前 `GXxHXxx 21.200` 固件确认的；其他 BIOS/EC 版本或其他平台 ID 可能选择不同表。

---

## 7. 电池 ROM 表格式与 RSOC 选择算法

每个模式表包含 8 项，每项 9 字节：

```text
byte0: RSOC 阈值
byte1: STAPM 候选值
byte2: slow PPT / SPPT 候选值
byte3: fast PPT / FPPT 候选值
byte4: 其他平台/电流/策略参数
byte5: 其他平台/电流/策略参数
byte6: 其他平台/电流/策略参数
byte7: 其他平台/电流/策略参数
byte8: 其他平台/电流/策略参数或标志
```

已确认前四字节与功耗表选择、最终候选槽对应。`byte4..8` 的完整物理含义尚未逐项解出，不能仅因为前三个瓦数相同，就认为相邻表项的所有行为完全相同。

### 7.1 两组表项

`EC[0x0983].bit1` 选择表的前后半区：

```text
bit1 = 0 -> entries 0..3，正常电池组
bit1 = 1 -> entries 4..7，降额电池组
```

相关帮助函数原始字节为：

```asm
E944: 90 09 83    MOV  DPTR,#0x0983
E947: E0          MOVX A,@DPTR
E948: 54 02       ANL  A,#0x02
E94A: 22          RET
```

所以“bit1 选择前后半表”是可以直接确认的。

从管理该位的上下文看，它由带迟滞的电池会话/放电能力检查维护，用于选择更低的限制组。本文将其称为：

```text
电池供电能力降额标志
```

但这个名称是对行为的概括，尚不能断言它只代表电池健康、瞬时放电电流、温度、适配器拔出历史或其中某一个单独物理因素。

### 7.2 RSOC 来源

电量百分比来源为：

```text
EC[0x04AB]
```

表选择使用离散阈值，因此功耗是阶梯式变化，不是随电量连续线性缩放。

---

## 8. GX4HRXL 的实际电池功耗表

下表所有三元组均按以下顺序表示：

```text
STAPM / slow PPT (SPPT) / fast PPT (FPPT)，单位 W
```

### 8.1 正常电池组：`EC[0x0983].bit1 == 0`

| RSOC 区间 | Gaming (`0x76F2`) | Office (`0x773A`) | Turbo (`0x76AA`) |
|---|---:|---:|---:|
| `<31%` | 25 / 25 / 25 | 15 / 20 / 25 | 25 / 25 / 25 |
| `31–69%` | 28 / 30 / 35 | 15 / 20 / 25 | 35 / 35 / 35 |
| `70–89%` | 28 / 30 / 35 | 15 / 20 / 25 | 45 / 50 / 50 |
| `>=90%` | 28 / 30 / 35 | 15 / 20 / 25 | 48 / 54 / 60 |

### 8.2 降额电池组：`EC[0x0983].bit1 == 1`

| RSOC 区间 | Gaming | Office | Turbo |
|---|---:|---:|---:|
| `<21%` | 15 / 15 / 15 | 15 / 15 / 15 | 15 / 15 / 15 |
| `21–30%` | 15 / 15 / 15 | 15 / 15 / 15 | 15 / 15 / 15 |
| `31–69%` | 25 / 25 / 30 | 15 / 15 / 15 | 25 / 25 / 30 |
| `>=70%` | 25 / 25 / 30 | 15 / 15 / 15 | 35 / 35 / 40 |

### 8.3 对 80% 电量的具体预测

若机器处于活动工作状态、未插电、请求 Turbo，且 RSOC 为 80%，则电池候选值应为：

```text
正常组，bit1=0：45 / 50 / 50 W
降额组，bit1=1：35 / 35 / 40 W
```

这只是该表贡献的候选值。若温度、电流、平台或其他候选槽给出更低的非零限制，最终值仍会低于这里列出的数字。

### 8.4 相邻行瓦数相同不等于表项完全相同

例如某些相邻 RSOC 区间的前三个功耗字节相同，但 `byte4..8` 仍可能不同。因此在完整策略层面，21%、31%、70%、90% 等阈值仍可能触发功耗之外的电流、标志或保护行为变化。

---

## 9. 候选槽与最终仲裁

### 9.1 电池候选

```text
EC[0x0872] = battery STAPM candidate
EC[0x087A] = battery slow PPT candidate
EC[0x088A] = battery fast PPT candidate
```

### 9.2 AC/基础模式候选

```text
EC[0x0873] = AC/mode STAPM candidate
EC[0x087B] = AC/mode slow PPT candidate
EC[0x088B] = AC/mode fast PPT candidate
```

### 9.3 最终值

`bank1:0xB761` 会遍历多组候选数组，并选择最严格的非零值。最终结果存入：

```text
EC[0x086B] = final STAPM
EC[0x086C] = final slow PPT
EC[0x086E] = final fast PPT
```

概念上可写成：

```text
final = 所有非零候选中的最小值

候选来源包括：
- 基础模式/AC 策略
- 电池表
- 温度限制
- 电流限制
- 平台限制
- 其他尚未全部命名的保护或策略来源
```

这里的“最小非零值”是对调查到的仲裁行为的概括；ROM 表值应理解为候选值/上限，而不是一定能在 SMU 端持续看到的保证值。

### 9.4 对外发布的最终功耗镜像

除内部最终值 `0x086B/0x086C/0x086E` 外，固件还把它们发布到较低地址的公开镜像：

```text
EC[0x046A] <- EC[0x086B]    final STAPM
EC[0x046B] <- EC[0x086C]    final slow PPT / SPPT
EC[0x046F] <- EC[0x086E]    final fast PPT / FPPT
```

bank1 `0xB6C1..0xB716` 会在内部最终值变化时更新相应镜像，并设置 `EC[0x09DD]` 中的变更标志。本轮通过 `i2ec_rw.py` 直接读未完整暴露在普通 mmap 中的 XRAM，得到：

```text
EC[0x046A/0x046B/0x046F] = 54 / 65 / 70
EC[0x086B/0x086C/0x086E] = 54 / 65 / 70
```

两组完全一致。尤其要注意：`EC[0x046B]` 是 slow PPT/SPPT 的公开镜像，不是第二风扇 RPM 的低字节。

bank1 `0xE5DE` 也不是“清第二风扇 telemetry”的专用函数。其真实行为是先把累加器 `A` 写入调用者提供的 `DPTR`，再清空 `0x046A/0x046B/0x046F`，使公开功耗镜像失效并等待仲裁路径重新发布。因此本文将它概括为 `store_and_invalidate_published_power_limits`。

---

## 10. 插电状态的动态交叉验证

调查时系统状态：

```text
AC online        = 1
Battery capacity = 80%
EC[0x0490]       = 0x07
EC[0x04AB]       = 0x50
EC[0x0751]       = 0x10  (Turbo)
EC[0x0983]       = 0x20  (bit1 clear)
```

候选槽：

```text
Battery candidates:
EC[0x0872] = 0
EC[0x087A] = 0
EC[0x088A] = 0

AC/mode candidates:
EC[0x0873] = 54
EC[0x087B] = 65
EC[0x088B] = 70
```

EC 最终值：

```text
EC[0x086B] = 54
EC[0x086C] = 65
EC[0x086E] = 70
```

同一时刻运行 `sudo ryzenadj -i` 观察到：

```text
STAPM LIMIT      54.000
PPT LIMIT SLOW  65.000
PPT LIMIT FAST  70.000
PPT LIMIT APU   54.000
```

这组实测同时验证了：

- `0x086B` 对应最终 STAPM；
- `0x086C` 对应最终 PPT Slow/SPPT；
- `0x086E` 对应最终 PPT Fast/FPPT；
- 当前机器插电 Turbo 确实为 `54/65/70 W`；
- 此时电池候选已清零，AC/模式候选成为最终限制。

调查中 `EC[0x0783..0x0785]` 为零，因此它们不是当前路径下可信的 SMU 实时回读值，不能替代 `0x086B/0x086C/0x086E` 或 `ryzenadj -i`。但后续静态分析确认：在 APExist+Custom 条件下，非零值会覆盖 EC 内部 profile 字段；详见第 20.2 节。

---

## 11. 为什么模式切换和 `ryzenadj` 会受 AC/电量影响或被覆盖

EC 内部策略链可以概括为：

```text
GCU/用户写 EC[0x0751]
  -> EC 把它解释为基础模式/表选择器
  -> 检查合格后的 AC 状态 EC[0x0490].bit0
  -> 若在电池上且处于活动工作状态：
       选择平台 ID
       选择 Gaming / Office / Turbo 基础表
       根据 EC[0x0983].bit1 选择正常组或降额组
       根据 EC[0x04AB] 的 RSOC 选择阶梯表项
       写入电池候选槽
  -> 若在 AC 上：
       清除电池相关影响并写入 AC/模式候选槽
  -> bank1:0xB761 对全部候选做最严格非零仲裁
  -> 写入 EC[0x086B/0x086C/0x086E]
  -> 后续 EC 路径重新发布给 SMU
```

由此可以解释几个现象：

### 11.1 为什么电池上切 Turbo 也达不到插电 Turbo

`0x0751 = 0x10` 表示“请求 Turbo 基础策略”，不是“强制写 54/65/70 W”。在电池路径中，它选择的是 Turbo 电池表，然后再受 RSOC、降额位和其他候选限制。

### 11.2 为什么电量变化会改变 `ryzenadj -i`

电池表按 RSOC 阈值选择不同表项，特别是 Turbo 模式在 31%、70%、90% 等区间会发生明显变化。降额组还会进一步限制到更低档位。

### 11.3 为什么直接 `ryzenadj` 写入后可能又恢复

`ryzenadj` 直接修改 SMU 的当前限制，但不会同步更新 EC 的模式寄存器、候选数组和最终仲裁结果。只要 EC 后续周期或事件触发再次向 SMU 发布 `0x086B/0x086C/0x086E`，SMU 就可能恢复为 EC 策略值。

因此若要改变长期策略，不能只考虑一次性的 SMU 写入，还需要处理 EC 的候选生成、最终仲裁或 EC 到 SMU 的重复发布路径。直接扩大某个 ROM 表也不保证最终值提高，因为其他候选仍可能更低。

### 11.4 官方 UI 限制与 EC 限制是两层机制

官方控制台可能在未插电时主动禁用某些模式切换，这是应用层限制；但即使绕过 UI 直接写 `0x0751`，EC 固件仍会独立执行电池功耗策略。因此绕过 UI 不等于绕过 EC 的电池限制。

---

## 12. 已确认、谨慎推断与尚未确认的部分

### 12.1 已确认

- 官方基础模式请求寄存器为 `EC[0x0751]`；
- 固件用 `0x0751 & 0x90` 区分三种基础模式；
- `EC[0x0490].bit0` 是功耗路径实际使用的 AC present 条件；
- 电池路径要求 `EC[0x06E6] == 1`；
- 当前机器匹配 ID `0x05` 表族，不匹配 ID `0x04` 覆盖；
- profile `0x41` 的三个模式表地址为 `0x76F2/0x773A/0x76AA`；
- `EC[0x0983].bit1` 选择每张表的前四项或后四项；
- RSOC 来源为 `EC[0x04AB]`；
- 电池候选、AC 候选和最终值的寄存器映射；
- 当前插电 Turbo 的 EC 最终值与 `ryzenadj -i` 的 `54/65/70 W` 匹配；
- 关键 `LCALL/LJMP` 路径已从原始 opcode 解码，并区分 fixed common 和 banked 区。

### 12.2 有代码支持、但人类语义需谨慎

- `EC[0x06E6] == 1` 很可能对应 S0/正常运行状态，但本文只确认它是电池功耗构建路径要求的活动状态；
- `EC[0x0983].bit1` 明确选择低功耗半表，并由迟滞式电池能力逻辑维护，但它的单一物理含义尚未完全确定。

### 12.3 尚未动态验证或尚未完整逆向

- 本次会话未完成一次受控的物理拔插电源动态记录；
- EC 在什么事件、周期和具体延迟下重新向 SMU 发布限制，尚未精确计时；
- 表项 `byte4..8` 的逐字节物理含义尚未全部解出；
- `0x0983.bit1` 的所有置位/清零条件及其物理传感器来源尚未完整命名；
- 其他候选数组中每一个槽位的来源尚未全部建立符号表。

---

## 13. 后续只读动态验证建议

为了进一步确认拔插电源、电量阶梯和降额位的运行时行为，可在不写 EC 的前提下同步采样：

```text
EC[0x0490]          合格后的 AC 状态
EC[0x04AB]          RSOC
EC[0x06E6]          EC 工作状态
EC[0x0751]          请求模式
EC[0x0983]          降额标志所在寄存器
EC[0x0872/7A/8A]    电池候选
EC[0x0873/7B/8B]    AC/模式候选
EC[0x086B/6C/6E]    最终 STAPM/SPPT/FPPT
ryzenadj -i          SMU 当前限制
```

建议的实验顺序：

1. 插电稳定后记录一组完整快照；
2. 物理拔出适配器，按较短间隔记录 10～30 秒；
3. 观察 `0x0490.bit0` 何时翻转，以及 AC 候选何时清零、电池候选何时填入；
4. 同步观察 `0x086B/0x086C/0x086E` 和 `ryzenadj -i` 的更新时间差；
5. 再插回适配器，记录反向切换；
6. 在不修改电池状态的情况下切换 Gaming/Office/Turbo，确认基础模式选择；
7. 在不同 RSOC 阈值附近重复测试，验证 ROM 表的阶梯切换；
8. 若 `0x0983.bit1` 发生变化，记录变化前后的电流、温度、RSOC、AC 历史和候选值，以缩小其真实语义。

上述地址中相当一部分（尤其内部 `0x08xx`、`0x18xx`、`0x91xx` XRAM）不在普通 H2RAM/mmap 窗口中完整暴露，不能用 mmap 中的零值判断其真实状态。动态验证应使用 `tools/i2ec_rw.py` 经 `/dev/port` 读取；按仓库规则必须使用 `sudo` 并取得用户提权许可。只读采样也应控制频率，避免使用未经验证的写操作。

---

## 14. 最终模型

当前证据支持以下模型：

```text
请求模式不是最终功耗值。

最终功耗限制
  = 基础模式
  + AC/电池状态
  + 平台/profile
  + 电池电量阶梯
  + 电池供电能力降额组
  + 温度/电流/平台等其他候选约束
  -> 取最严格的非零限制
  -> EC 重新发布给 SMU
```

因此，观察到性能模式受“电池电量/插不插电”影响是 EC 固件的预期策略，而不是 `ryzenadj` 显示异常。对当前 GX4HRXL/21.200 而言，任何长期修改方案都应以固件二进制中的候选构建和仲裁路径为准，而不能只改官方控制台写入值或依赖一次性的 SMU 写入。

---

## 15. APExistFlag 的真实作用域与生命周期

### 15.1 精确读取位置

固件中读取 `APExistFlag` 的帮助函数位于 bank1 `0xDF7F`，原始 opcode 为：

```asm
DF7F: 90 07 41    MOV  DPTR,#0x0741
DF82: E0          MOVX A,@DPTR
DF83: 54 01       ANL  A,#0x01
DF85: 22          RET
```

因此本文将其精确定义为：

```text
APExist = EC[0x0741].bit0
```

它不是基础性能模式的一部分，也不是单纯的 UI 状态显示位，而是 EC 判断“是否允许采用 AP/GCU 提供的参数”的总门控之一。

### 15.2 固件会主动撤销 APExist

bank1 `0xE611` 的清理函数为：

```asm
E611: 90 07 41 E0 54 FE F0    ; clear EC[0x0741].bit0
E618: 90 08 E2 E0 54 EF F0    ; clear EC[0x08E2].bit4
E61F: 22
```

已从调用点原始 opcode 确认，`E611` 至少有两个调用点：

```text
C949: 12 E6 11
CB3F: 12 E6 11
```

`C944` 路径先调用 `E009`：

```asm
E009: 90 06 E6    MOV  DPTR,#0x06E6
E00C: E0          MOVX A,@DPTR
E00D: 64 05       XRL  A,#0x05
E00F: 22          RET

C944: 12 E0 09    LCALL E009
C947: 70 04       JNZ   C94D
C949: 12 E6 11    LCALL E611
C94C: 22          RET
```

故可确认：

```text
当 EC[0x06E6] == 5 时，该状态处理路径会清除 APExist。
```

这里仍不把 `0x06E6 == 5` 强行命名为某个具体 ACPI 电源状态；已确认的只是数值条件及其清理结果。

`CB3F` 则位于更大规模的状态退出/初始化清理路径中。它在清 APExist 后继续执行：

```text
clear EC[0x07C5].bit7    独立双风扇 gate
clear EC[0x07C6].bit2    AP 风扇管理 gate
clear EC[0x07A6].bit6    另一项 AP/充电相关状态
clear EC[0x07C5].bit3
EC[0x0807] = 0x0B
EC[0x085D] = 1
```

这说明 APExist 更接近由 AP 服务维持的“在线租约/所有权标志”，而不是应用写一次后便永久有效的配置。系统状态切换、初始化或服务生命周期变化后，EC 可以主动撤销 AP 控制权，GCU 若仍需接管必须重新同步相关 gate 和参数。

官方 GCU 读取三种默认风扇表时也会主动利用这一生命周期：`RefreshDefaultFanTableAll()` 先调用 `Set_APExistToEC(false)`，依次请求 Gaming/Office/Turbo 默认表，全部完成后再调用 `Set_APExistToEC(true)`。所以观察 APExist 短暂变为 0 不一定意味着服务崩溃，也可能是官方“暂时退出 AP 接管、让 EC 导出 ROM 默认表”的正常协议阶段。

### 15.3 APExist 的作用范围不只风扇

在 bank1 中扫描原始 `LCALL 0xDF7F` opcode，可将调用点大致分类如下：

| 调用区域 | 已确认作用 |
|---|---|
| `0x95F0..0x9B0D` | AP 风扇表、独立/联动双风扇、duty 渐变和若干保底路径 |
| `0xA1C3` | 自定义 TCC 温度目标覆盖 |
| `0xA2BF / 0xA32E / 0xA3A3` | AP+Custom 时选择不同的 ROM 风扇/profile 指针 |
| `0xA661` | AP 在线时选择另一项平台 profile 字段 |
| `0xA797` | `0x0783..0x0785`、`0x078B` 参数覆盖 |
| `0xA7FF` | `0x0745/0x0746` 及 `0x0743.bit1` 控制组覆盖 |
| `0xC47C` | AP 不存在时清空 AP 提供的功耗/profile 输入 |
| `0xC944 / 0xCB3F` | APExist 生命周期清理 |
| `0x8AD9 / 0x8EB1` | AP 在线状态及部分 AP 标志同步 |
| 其他调用点 | 温控事件、通知、恢复或状态机路径，尚不宜强行命名 |

例如 `0x8AD9` 会将 APExist 同步到 `EC[0x09E8].bit7`；`0x8EB1` 在 AP 在线时还会处理 `EC[0x0726].bit3` 与 `EC[0x09E8].bit3` 的同步。由此可见，APExist 是一项跨风扇、温控、功耗 profile 和状态同步的全局 AP 在线条件。

### 15.4 同一字节的 bit5 是启动/恢复窗口内的缺转告警

`EC[0x0741].bit5` 与 APExist 共用一个字节，但不是 APExist 的一部分。tach worker `bank1:0x922C` 先检查：

```text
EC[0x06E6] == 1
EC[0x0466].bit4 == 0
EC[0x084C] != 0
```

在有效窗口内，worker 更新两路 RPM；仅当 `EC[0x084C] == 1` 的最后一次检查中发现任一路 RPM 为 0，才执行：

```text
EC[0x0741] |= 0x20
```

随后每次有效调用都让 `0x084C--`。gate 不成立时，路径 `0x927C..0x9282` 则清除 `0x0741.bit5`。fixed-common ROM 初始化描述符包含 `{0x08,0x4C,0x05}`，即把 `EC[0x084C]` seed 为 5；稳定运行中的真机只读样本为：

```text
EC[0x0741] = 0x81    ; bit0、bit7 set，bit5 clear
EC[0x084C] = 0x00
```

因此 `0x084C` 更像启动、恢复或状态重初始化后的有限检查窗口，而不是持续运行的 stall monitor。对固件中直接访问 `0x0741` 的原始 opcode 审计还表明，bit5 只在上述 worker 中置位/清除，尚未发现 EC 内部直接读取它的消费者；它更可能是对 host/BIOS/GCU 发布的诊断告警。这里不排除外部软件读取，也不把初始化描述符的重放事件武断限定为“仅冷启动”。

---

## 16. Custom 模式实际上由多个 gate 共同组成

### 16.1 三个核心 gate

固件中的三个直接帮助函数如下：

```asm
DFD6: MOV DPTR,#0x0727
DFD9: MOVX A,@DPTR
DFDA: ANL A,#0x40
DFDC: RET
```

```text
EC[0x0727].bit6 = CustomerModeLight / AP Custom gate
```

```asm
E181: MOV DPTR,#0x07C6
E184: MOVX A,@DPTR
E185: ANL A,#0x04
E187: RET
```

```text
EC[0x07C6].bit2 = AP fan management gate
```

```asm
E51C: MOV DPTR,#0x07C5
E51F: MOVX A,@DPTR
E520: ANL A,#0x80
E522: RET
```

```text
EC[0x07C5].bit7 = independent/respective second-fan gate
```

因此，自定义风扇控制并不是“写一个 Custom bit”就生效，而是至少包含以下层次：

| 层次 | 条件 | 作用 |
|---|---|---|
| AP 在线 | `0x0741.bit0 = 1` | 允许 AP 参数参与固件决策 |
| Custom 参数有效 | `0x0727.bit6 = 1` | 允许使用 AP 自定义参数/风扇曲线 |
| AP 风扇管理 | `0x07C6.bit2 = 1` | 允许 RAM 风扇表进入主风扇 worker |
| 独立双风扇 | `0x07C5.bit7 = 1` | 允许第二风扇使用自己的 index/duty，而非联动派生 |

### 16.2 主自定义风扇表的精确生效条件

bank1 `0x95F0` 的原始调用序列为：

```asm
95F0: 12 DF 7F    LCALL is_ap_present
95F3: 60 78       JZ    fallback
95F5: 12 E1 81    LCALL ap_fan_management_enabled
95F8: 60 73       JZ    fallback
95FA: 12 DF D6    LCALL customer_mode_light
95FD: 60 6E       JZ    fallback
```

所以主 AP 风扇曲线路径的最小条件是：

```text
EC[0x0741].bit0 == 1
&& EC[0x07C6].bit2 == 1
&& EC[0x0727].bit6 == 1
```

这里没有直接检查 `EC[0x0726].bit7`。

### 16.3 `0x0726.bit7` 不是唯一、也不是可靠的风扇主 gate

官方控制台逆向结果曾把 `0x0726.bit7` 用作 Custom 状态的一部分，但固件在 `0x958E` 附近会根据自己的运行状态和 `XRAM[0x9141]` 温度条件主动 set/clear 该位。例如 `0x95A7..0x95AD` 在温度比较成立时直接置位：

```asm
95A7: 90 07 26    MOV  DPTR,#0x0726
95AA: E0          MOVX A,@DPTR
95AB: 44 80       ORL  A,#0x80
95AD: F0          MOVX @DPTR,A
```

因此：

- `0x0726.bit7` 可以用于观察官方模式状态组合；
- 但它并不是 `0x95F0` 主 RAM 风扇表路径的直接 gate；
- 不能把它解释为“只由 GCU 写入且完全等价于 Custom 模式”；
- 判断实际是否会采用 AP 风扇表，应优先检查 `0x0741.bit0 + 0x07C6.bit2 + 0x0727.bit6`。

---

## 17. 风扇表的 XRAM 位置、ROM 表加载触发字与重叠布局

### 17.1 两组 48-byte 表

固件运行期使用以下 XRAM 布局：

| 地址范围 | 长度 | 内容 |
|---|---:|---|
| `0x0F00..0x0F0F` | 16 | 主/CPU 曲线 UpT |
| `0x0F10..0x0F1F` | 16 | 主/CPU 曲线 DownT |
| `0x0F20..0x0F2F` | 16 | 主/CPU 曲线 Duty |
| `0x0F30..0x0F3F` | 16 | 第二路曲线 UpT |
| `0x0F40..0x0F4F` | 16 | 第二路曲线 DownT |
| `0x0F50..0x0F5F` | 16 | 第二路曲线 Duty / ROM 表加载触发字复用区 |

官方 GCU 写表时会按 16 项写入两组 UpT、DownT 和 Duty，并把 duty 百分比乘以 2 后写入 EC。因此 `0x0F20/0x0F50` 表中的值是 EC raw duty，通常 `raw / 2` 约等于百分比。

### 17.2 官方默认表读取协议与 EC 复制逻辑

官方反编译代码给这三个重叠地址定义了明确名称：

| 地址 | 官方常量名 | 协议作用 |
|---|---|---|
| `0x0F5D` / 3933 | `ADDR_RAMFAN1P5_TABLE_STATUS1` | 状态/触发字 1，写 `0xFD` |
| `0x0F5E` / 3934 | `ADDR_RAMFAN1P5_TABLE_STATUS2` | 状态/触发字 2，写 `0xC9` |
| `0x0F5F` / 3935 | `ADDR_RAMFAN1P5_TABLE_CTRL` | 要求的默认表模式 |

`RefreshDefaultFanTableAll()` 的应用层顺序是：

```text
Set_APExistToEC(false)
RefreshDefaultFanTable(Gaming, mode=2)
RefreshDefaultFanTable(Office, mode=3)
RefreshDefaultFanTable(Turbo,  mode=1)
Set_APExistToEC(true)
```

每次 `RefreshDefaultFanTable()` 都按以下顺序写入：

```text
EC[0x0F5F] = mode
EC[0x0F5D] = 0xFD
EC[0x0F5E] = 0xC9
```

然后每 500 ms 轮询 `0x0F5D/0x0F5E`。官方 `IsReadyToRead()` 要求两个字节都已不再分别等于 `0xFD/0xC9`，最长等待 10 次；成功后读取完整 `0x0F00..0x0F5F` 作为该模式的默认风扇表。

与此对应，bank1 `0x93E9..0x940D` 检查：

```text
XRAM[0x0F5D] == 0xFD
XRAM[0x0F5E] == 0xC9
1 <= XRAM[0x0F5F] < 4
```

触发成立后，`0x941B` 调用 profile/ROM 表选择函数，随后两次调用 fixed-common `0x7A97` 复制各 `0x30` 字节：

```asm
944B: 7F 30       MOV   R7,#0x30
944D: 12 7A 97    LCALL 0x7A97    ; destination 0x0F00
...
947E: 7F 30       MOV   R7,#0x30
9480: 12 7A 97    LCALL 0x7A97    ; destination 0x0F30
```

这里的 `LCALL 0x7A97` 已从调用点原始 opcode 解码；目标位于 fixed common 区，不依赖当前 bank 映射。复制完整第二张 `0x30` 字节表会覆盖 `0x0F5D..0x0F5F`，这正是 GCU 用来判断 EC 已完成请求的应答机制。

需要保留一个静态分析边界：`0x0F5F` 在该块中明确被检查为 `1..3`，官方协议也明确给出 `1=Turbo、2=Gaming、3=Office`；但紧接 `0x940F` 的局部反汇编没有出现一个简单的“把 `0x0F5F` 直接传给表选择函数”的赋值，后续还使用 `0x08E6` 和其他 profile 状态。因此模式控制值如何进入最终 ROM 指针选择，可能还经过 EC host-write 副作用或其他镜像状态，不能仅凭这一小段伪 C 简化。

### 17.3 `0x0F5D..0x0F5F` 的正确解释

不能把最后三字节永久判定为“元数据而不是 duty”。从原始 opcode 重新核对 `E417/E41A` 后，比较函数实际接受 `index < 16`，即有效 index 为 `0..15`；只有 index 增长到 16 时才清回 0。因此：

- `0x0F50..0x0F5F` 的 16 个字节在正常运行时都可被 `0x0F50 + index` 读取为 duty；
- 当官方 GCU 写入 `0x0F5F=mode`、`0x0F5D=0xFD`、`0x0F5E=0xC9` 时，同一组地址被解释为“请求 EC 导出某个基础模式 ROM 默认表”的握手命令；
- 触发条件成立后，固件立即把两组各 `0x30` 字节的 ROM 表复制到 `0x0F00` 和 `0x0F30`，所以这三个触发字会被复制进来的真实 duty 数据覆盖；
- 官方 GCU 写自定义表时把 `0x0F50..0x0F5F` 全部作为 duty 写入，这与运行期 16 项 lookup 一致；
- 普通合法 duty 最大约为 `0xC8`，因此尾部同时出现 `FD C9 01..03` 是一个明显的命令哨兵组合，不像正常风扇曲线。

更准确的模型是：

```text
平时：0x0F5D/0x0F5E/0x0F5F 分别是第二路 duty[13]/duty[14]/duty[15]
特殊瞬间：0x0F5F=mode 后写入 0x0F5D/5E=FD/C9，被 worker 识别为 ROM 表导出请求
加载完成：整段 0x0F30..0x0F5F 被 ROM 表覆盖，尾部恢复为实际 duty
```

因此，这里是一个命令触发字与表尾部的时分复用布局，而不是“第二风扇只有 13 或 15 个 duty 点”。

---

## 18. 风扇曲线 index、迟滞和温度源

### 18.1 主/CPU 曲线状态

主曲线使用：

```text
EC[0x0460] = 当前主曲线 index
EC[0x0461] = 由主 duty 表查得的目标 raw duty
```

帮助函数从 `0x0460` 取 index：

```text
E4FC: XRAM[0x0F00 + EC[0x0460]] -> R7    ; UpT[index]
E50C: XRAM[0x0F10 + EC[0x0460]] -> R7    ; DownT[index]
E5CF: XRAM[0x0F20 + A] -> EC[0x0461]     ; Duty[index]
```

在 AP Custom 主路径 `0x95FF..0x9640` 中，当 `EC[0x0668] == 0` 时，迟滞算法使用 `EC[0x043E]`：

```text
若 temperature >= UpT[index]：index++
若 temperature <  DownT[index]：index--
否则保持 index
```

随后 `E417/E41A` 使用带借位的比较序列判断 `index < 16`。因此 `0..15` 都是有效 index；只有 index 增长到 16 时才清为 0。第 16 项 `duty[15]` 确实可进入运行期 lookup。

当 `EC[0x0668] != 0` 时，`0x95FF` 分支改用 `XRAM[0x9413]` 及其他辅助状态参与比较。故不能把主曲线所有运行情况都简化为“只看 `0x043E`”。

### 18.2 第二路曲线状态和温度源

第二路曲线使用：

```text
EC[0x0468] = 当前第二路曲线 index
EC[0x0469] = 第二路目标 raw duty
```

普通/ROM 风扇路径 `0x9784..0x97C7` 明确以 `XRAM[0x9141]` 作为温度输入，根据第二组 UpT/DownT 更新 `0x0468`：

```asm
97A3: 90 91 41    MOV  DPTR,#0x9141
97A6: E0          MOVX A,@DPTR
...
97AB..97B0        index++
...
97B9: 90 91 41    MOV  DPTR,#0x9141
97BC: E0          MOVX A,@DPTR
...
97C1..97C6        index--
```

`XRAM[0x9141]` 在 `0x8E8A` 由前序计算结果写入。当前证据只能把它命名为“第二路/平台温度值”；虽然官方 GCU 将第二张表称作 GPU 表，但在没有把 `0x9141` 的完整传感器来源链解完前，不应仅凭 UI 名称断言它一定是独显 GPU 温度。

### 18.3 Custom 路径中的 `max(index0,index1)`

在 `0x9647`，帮助函数 `E2FB` 比较 `EC[0x0460]` 与 `EC[0x0468]`，随后选择二者较大值并写入 `XRAM[0x0A54]`：

```text
selected_index = max(EC[0x0460], EC[0x0468])
EC[0x0461] = XRAM[0x0F20 + selected_index]
EC[0x0469] = XRAM[0x0F50 + selected_index]
```

这形成默认的“较热一侧推动两路风扇”的联动目标。

进入 `0x95F0` 的 AP Custom 主路径后，会更新主 index `0x0460`，随后直接进入 `0x9647`，跳过普通路径 `0x9784..0x97C7` 对第二 index `0x0468` 的温度推进。对 main-bank0/main-bank1 中所有直接 `MOV DPTR,#0x0468` 引用的原始二进制审计表明：

- 唯一正常的升/降档写入位于非-Custom 路径 `0x9784..0x97C7`；
- Custom 分支只读取旧 `0x0468`，与 `0x0460` 做 `max()`，不会推进它；
- gate 或工作状态失败的清理路径可以把 `0x0468` 清零；
- 没有发现另一个隐藏的周期任务在 Custom 持续生效时更新 `0x0468`。

因此 Custom 持续运行时，`0x0468` 是进入该路径前保留下来的值，可能是旧值或已被清零的值，而不是持续按 `0x9141` 独立实时推进。若独立双风扇 gate 成立，后续 `0x986B..0x9883` 仍会分别按 `0x0460` 和这个 retained `0x0468` 重算两个 duty；若 gate 不成立，则使用共享 `selected_index` 或联动/fallback 逻辑。

当前真机的只读样本进一步验证了共享索引行为：

```text
EC[0x0460] = 6
EC[0x0468] = 0
XRAM[0x0F26] = 110
XRAM[0x0F56] = 90
EC[0x0461] = 110
EC[0x0469] = 90
```

也就是 `selected_index = max(6,0) = 6`，两路目标分别从主、第二 duty 表的第 6 项取得。该样本中 `EC[0x07C5].bit7 == 0`，独立双风扇 gate 不成立，所以这不能证明独立模式下旧 `0x0468` 的实际体验合理，只能确认当前 Custom 联动路径的查表结果。

---

## 19. 独立/联动双风扇、duty 渐变和转速读数

### 19.1 独立双风扇的精确 gate

bank1 `0x9857` 的条件链为：

```asm
9857: 12 E5 1C    LCALL EC[0x07C5].bit7 test
985A: 60 2A       JZ fallback
985C: 12 DF 7F    LCALL APExist test
985F: 60 25       JZ fallback
9861: 12 E1 81    LCALL EC[0x07C6].bit2 test
9864: 60 20       JZ fallback
9866: 12 DF D6    LCALL EC[0x0727].bit6 test
9869: 60 1B       JZ fallback
```

因此第二风扇真正按自己的 index/duty 独立运行，要求：

```text
EC[0x07C5].bit7 == 1
&& EC[0x0741].bit0 == 1
&& EC[0x07C6].bit2 == 1
&& EC[0x0727].bit6 == 1
```

成立后：

```text
EC[0x0460] -> XRAM[0x0F20 + index] -> EC[0x0461]
EC[0x0468] -> XRAM[0x0F50 + index] -> EC[0x0469]
```

若 `0x07C5.bit7 == 0` 或任何 AP/Custom gate 失败，则进入 `0x9886` 之后的联动/fallback 分支。那些分支会依据平台条件，从主风扇当前 duty、第二表最低档 `0x0F51`、平台保底值等生成副风扇目标。因此“存在第二张表”不等于“第二风扇永远独立按第二张表运行”。

### 19.2 目标 duty、当前 duty 和发布值

风扇控制链中的主要地址为：

| 地址 | 含义 |
|---|---|
| `EC[0x0461]` | 主风扇目标 raw duty |
| `EC[0x0469]` | 第二风扇目标 raw duty |
| `XRAM[0x1804]` | 主风扇内部当前/渐变后 raw duty |
| `XRAM[0x1809]` | 第二风扇内部当前/渐变后 raw duty |
| `EC[0x075B]` | 对外发布的主风扇 raw duty 读数 |
| `EC[0x075C]` | 对外发布的第二风扇 raw duty 读数 |

`0x075B/0x075C` 不是 RPM。官方数据格式及固件最大值均支持 `raw / 2 ≈ duty 百分比`；例如 raw `0xC8` 对应约 100%。

可以将运行链概括为：

```text
温度/状态
  -> 更新 curve index (0x0460 / 0x0468)
  -> 查 0x0F20 / 0x0F50 duty 表
  -> 目标 duty (0x0461 / 0x0469)
  -> 渐变、联动、保底和钳位
  -> 当前 duty (0x1804 / 0x1809)
  -> 发布 raw duty (0x075B / 0x075C)
  -> 硬件 PWM/风扇实际转速响应
```

### 19.3 duty 渐变速度

每次进入该 worker 时，固件先设置：

```text
XRAM[0x0A51] = 3
```

只有以下条件同时成立时，才允许 `EC[0x0787].low7` 覆盖默认值：

```text
EC[0x0787].bit7 == 1
APExist == 1
EC[0x0727].bit6 == 1
EC[0x0787].low7 > 0
```

对应原始路径为 `0x9810..0x9833`。后续渐变代码通常在计数条件满足时让 `0x1804/0x1809` 朝 `0x0461/0x0469` 每次只 `INC` 或 `DEC` 1，并把最大 raw duty 钳位到 `0xC8`。因此 `0x0787` 控制的是接近目标值的节奏/步进周期，而不是直接指定最终转速。

### 19.4 tach 原始计数、RPM 公式和发布地址

从 bank1 调用点原始 opcode 解码：

```text
0x923C: 12 1C 30    -> fixed common 0x1C30
0x9258: 12 1C 42    -> fixed common 0x1C42
```

两个 fixed-common veneer 分别装入目标 `bank0:0xE58E` 和 `bank0:0xE5AC`，再经 `0x1100` 切到 bank0。结合 bank0 原始实现，得到：

```text
main_count   = (XRAM[0x181F] << 8) | XRAM[0x181E]
second_count = (XRAM[0x1821] << 8) | XRAM[0x1820]

count == 0: RPM = 0
count != 0: RPM = floor(2,156,250 / count)
```

计算结果以大端 uint16 发布：

```text
主风扇 RPM = (EC[0x0464] << 8) | EC[0x0465]
副风扇 RPM = (EC[0x046C] << 8) | EC[0x046D]
```

通过 `i2ec_rw.py` 读取普通 mmap 未完整暴露的 tach XRAM，得到两组完全匹配的动态样本：

```text
main_count = 0x01F3 = 499
floor(2156250 / 499) = 4321 = 0x10E1
EC[0x0464:0x0465] = 0x10E1

second_count = 0x01FC = 508
floor(2156250 / 508) = 4244 = 0x1094
EC[0x046C:0x046D] = 0x1094
```

这是对旧结论的重要纠正：官方 GCU 中存在 `SECOND_FAN_RPM_BYTE1 = 0x046C`、`SECOND_FAN_RPM_BYTE2 = 0x046B` 的常量，但它不适用于当前 21.200 固件，可能是跨机型/旧代际遗留。当前固件的 `0x046B` 实际是 final slow PPT/SPPT 镜像，副风扇 RPM low byte 是 `0x046D`。d52 在这一区域也存在错一字节/重叠解码，不能以其标签反推地址。

建议观测时同时记录：

```text
curve/index: 0x0460,0x0461,0x0468,0x0469
duty:        0x075B,0x075C,0x1804,0x1809
tach count:  0x181E,0x181F,0x1820,0x1821
RPM:         0x0464,0x0465,0x046C,0x046D
power mirror:0x046A,0x046B,0x046F
```

这样既能区分“表 index 已切换”“目标 duty 已变化”“当前 duty 正在渐变”和“机械 RPM 尚在滞后响应”，也不会再把功耗镜像 `0x046B` 误当成 tach 字节。`0x0741.bit5` 与 `0x084C` 控制的有限缺转检查窗口见第 15.4 节。

---

## 20. Custom 除风扇外还会影响的字段

### 20.1 TCC 温度目标

bank1 `0xA1BB..0xA1D6` 先检查：

```text
EC[0x0786].bit7 == 1
APExist == 1
EC[0x0727].bit6 == 1
```

成立时：

```text
XRAM[0x0A4C] = EC[0x0786].low7
```

随后若目标值相对 `EC[0x098C]` 发生变化，固件执行：

```text
EC[0x098C] = new_tcc
EC[0x0463] = new_tcc | 0x80
```

因此 Custom/AP 在线可以覆盖固件从 ROM/profile 选择的 TCC 温度目标。`0x0786.bit7` 是此覆盖自身的 enable，低 7 位是目标值。

### 20.2 `0x0783..0x0785` 功耗/profile 参数

bank1 `0xA797` 依次检查：

```text
APExist == 1
EC[0x0727].bit6 == 1
```

然后仅在输入值非零时覆盖内部字段：

```text
EC[0x0783] -> EC[0x08C0]
EC[0x0784] -> EC[0x08C1]
EC[0x0785] -> EC[0x08C3]
```

另有：

```text
若 EC[0x078B] > 0：EC[0x08BB] = EC[0x078B] - 1
```

官方 GCU 将 `0x0783/0x0784/0x0785` 命名为 PL1/PL2/PL4。静态固件证据表明它们不是单纯无效的显示寄存器，而是在 APExist+Custom 条件下确实有 override 路径。需要同时保留以下边界：

- 它们不是 SMU 的实时功耗回读；
- 写入非零值会覆盖 EC 内部 profile 字段；
- 这些字段还要经过后续候选生成、AC/电池分支和最终仲裁；
- 因而不能仅凭地址名就把 `0x0783/84/85` 与最终 `STAPM/SPPT/FPPT` 做无条件一一对应。

AP 不存在时，`0xC47C` 会清空：

```text
EC[0x0783]
EC[0x0784]
EC[0x0785]
EC[0x078B]
```

这进一步说明这些寄存器属于由 AP 服务维持的临时输入，而不是永久 profile 存储。

### 20.3 `0x0743/0x0745/0x0746` 控制组

bank1 `0xA7F7..0xA827` 要求：

```text
EC[0x0743].bit0 == 1
APExist == 1
EC[0x0727].bit6 == 1
```

成立时执行：

```text
EC[0x0745] -> EC[0x09EB]
EC[0x0746] -> EC[0x09EC]
EC[0x0743].bit1 -> EC[0x07C4].bit4
```

官方逆向资料将这组地址与 MyFan2 PWM / TGP DynamicBoost 一类功能关联，但目前尚未完成这些内部目标字段的全链路验证。本文只确认它是一组额外的 AP+Custom profile override，不提前给每个字节下最终物理语义结论。

### 20.4 ROM/profile 选择也会改变

`0xA2BF`、`0xA32E`、`0xA3A3` 都从原始 opcode 确认会连续检查 `0x0727.bit6` 和 APExist；成立时选择与普通/ROM 自动模式不同的表指针。这说明 Custom 的影响不只是在 RAM 表上覆盖几个 duty 字节，还会改变固件所采用的 profile/风扇默认数据来源。

此外，`0xA661` 的 APExist 路径会改变 `XRAM[0x9147]` 的来源：满足额外平台条件时写特殊值 `0x96`，否则 AP 在线路径从 ROM/profile 偏移 `0x1A` 取值，非 AP 路径从偏移 `0x19` 取值。该字段的人类语义尚未完全确认，但足以证明 APExist 本身也参与平台 profile 选择，而不只是 Custom 风扇开关。

---

## 21. 本轮结论分级与动态验证重点

### 21.1 已从固件二进制确认

- `APExist = EC[0x0741].bit0`；
- 固件在特定状态路径会主动清 APExist，并同时清相关 AP 状态；
- 主 AP 风扇表的 gate 是 `0x0741.bit0 + 0x07C6.bit2 + 0x0727.bit6`；
- 独立第二风扇还要求 `0x07C5.bit7`；
- `0x0726.bit7` 不是主 RAM 风扇路径的直接 gate，并可能被 EC 温度/状态逻辑自行修改；
- 两组表位于 `0x0F00..0x0F5F`，每组为 UpT/DownT/Duty 三段；
- 主 index/目标 duty 为 `0x0460/0x0461`，第二路为 `0x0468/0x0469`；
- 主 Custom 曲线通常使用 `0x043E`，第二路普通曲线明确使用 `0x9141`；
- `0x075B/0x075C` 是 raw duty 而不是 RPM；
- RPM 为 `0x0464:0x0465` 和 `0x046C:0x046D`，原始 tach count 位于 `0x181E..0x1821`；
- `0x046A/0x046B/0x046F` 分别是 final STAPM/slow PPT/fast PPT 的公开镜像；
- `0x0741.bit5` 是由 `0x084C` 有限初始化窗口驱动的零转告警，不是 APExist；
- `0x0786` 可在 AP+Custom 下覆盖 TCC；
- `0x0783..0x0785` 和 `0x078B` 在 AP+Custom 下有实际内部 override 路径；
- `0x0F5D..0x0F5F` 平时都是第二路 `duty[13..15]`，但 `FD C9 01..03` 组合会被临时解释为 ROM 表加载触发字；加载后整张表覆盖这些触发值。

### 21.2 有代码支持但仍需谨慎命名

- `0x043E` 可称主/CPU 温度源，但还需继续追溯其完整传感器来源；
- `0x9141` 是第二路/平台温度值，尚不能只凭 GCU 的表名断定等于独显 GPU 温度；
- `0x0783/84/85` 对内部字段的覆盖已确认，但与最终 STAPM/SPPT/FPPT 的逐项对应仍受后续仲裁影响；
- `0x0743/45/46`、`0x9147` 的覆盖和选择已确认，但其最终硬件语义尚未完全命名；
- APExist 的部分调用点属于事件、通知和恢复状态机，需在更完整上下文中继续逆向。

### 21.3 最值得做的只读动态验证

在不写 EC 的前提下，同步采样以下地址：

```text
AP/Custom gates:
  0x0741, 0x0726, 0x0727, 0x0743, 0x07C5, 0x07C6

Custom inputs:
  0x0745, 0x0746, 0x0783, 0x0784, 0x0785, 0x0786, 0x0787, 0x078B

Fan tables and state:
  0x0F00..0x0F5F
  0x043E, 0x0460, 0x0461, 0x0468, 0x0469
  0x075B, 0x075C, 0x0A51, 0x1804, 0x1809, 0x9141

RPM and raw tach count:
  0x0464, 0x0465, 0x046C, 0x046D
  0x181E, 0x181F, 0x1820, 0x1821

Fan startup/zero-RPM window:
  0x0741, 0x084C

Published final-power mirrors:
  0x046A, 0x046B, 0x046F

Internal override targets:
  0x08BB, 0x08C0, 0x08C1, 0x08C3
  0x09EB, 0x09EC, 0x0A4C

Final power arbitration:
  0x086B, 0x086C, 0x086E
```

优先验证四个问题：

1. 切换 `0x07C6.bit2` 时，是仅作为电平 gate，还是其边沿还会触发表重载；
2. 切入/退出 Custom 时 `0x0468` retained 值被保留或清零的具体事件时序；
3. `0x0783..0x0785` 改变后，`0x08C0/0x08C1/0x08C3` 以及最终 `0x086B/0x086C/0x086E` 的传播关系；
4. index、目标 duty、渐变后 duty、发布 duty 与 RPM 之间的时间顺序。

这些内部地址多数不能依赖普通 mmap 读取，动态采样必须使用 `tools/i2ec_rw.py` 经 `/dev/port` 访问；按仓库规则仍须使用 `sudo` 并取得用户提权许可。本轮动态证据均为只读，没有执行新的 EC 写操作。

