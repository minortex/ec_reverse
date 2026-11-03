# 07 固件补丁

## 1. 原则

1. **二进制是唯一蓝本**。`.d52`、伪 C、GCU 反编译只用于定位，不能作为补丁地址依据。
2. **版本本地化**：每个补丁只针对一个 baseline（长度 + SHA-256 + 原字节都锁定），
   脚本拒绝输入输出同路径、拒绝交叉喂版本。
3. **fail-closed**：原字节不匹配、代码洞不全是 `0xFF`、越界、跳转目标不在白名单 → 直接失败。
4. **差异可枚举**：产物只允许改动预先声明的区间；用 `firmware_tool.py diff` 核对字节数与簇。
5. **先离线、后真机**：静态检查通过 ≠ 可刷；每条补丁都要写验收寄存器和回退条件。

```sh
# v2.2 只接受自己的基线：cal-direct-mode-locked-apexist-ignore（SHA aa4a52ab…）
python3 firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-flexicharge-v2.2-20260729/patch_flexicharge_v22.py \
  firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-20260723/GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-20260723.bin \
  /tmp/GXxHXxx_21.200-v2.2.bin
```

## 2. 版本树

仓库中现存的可执行补丁（`firmware_mods/GXxHXxx_21.200/<版本>/`）：

| 目录 | 基线 | 改动 | 验证状态 |
|---|---|---|---|
| `cal-direct-mode-locked-20260723` | 官方 | 校准值直通 + 模式锁定（仅脚本） | 未实机 |
| `cal-direct-mode-locked-apexist-ignore-20260723` | 官方 | 上述 + 忽略 APExist | 未实机 |
| `cal-direct-mode-locked-apexist-ignore-flexicharge-v2.2-20260729` | 锁定版 | 上述 + FlexiCharge v2.2 | 寄存器级真机验证 |
| `build-from-official-20261103` | 官方 | 一次合成上面三步 + FlexiCharge v2.2，输出哈希锁定 `ff668376…` | 生成结果与 v2.2 产物逐字节一致 |
| `cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727` | 锁定版 | 只读 SMBus 探针（v13） | 真机只读验证 |
| `cal-direct-mode-locked-apexist-ignore-sbs-identity-v11-live-veneer-observer-20260727` | 锁定版 | 现场观察器（v11） | 真机只读验证 |

只在文档中留档、仓库里已无脚本的历史版本：

| 版本 | 改动 | 结果 |
|---|---|---|
| `int1-r7-20260722` | INT1 ISR 保存/恢复 bank2 R7（29 B） | **已实机初验**：17600 mV 稳定 |
| `cal-direct-only-exp-20260723` | 只跳过校准 ±300 mAh 夹逼（3 B） | 未实机 |
| `int1-r7-cal-direct-exp-20260723` | 上述两者叠加 | 未实机 |
| `mode-only-voltage-20260723` | 只保留模式降压，绕过 loop/stress/aux | 未实机 |

补丁之间**不能**互相冒充：cal-only 版本不含 INT1-R7 修复；叠加版必须从对应基线生成。
每个目录的脚本都锁定输入长度、SHA-256 与原始字节，拒绝同路径输入输出。

## 3. INT1-R7 修复

详见 [04 章 §7](04-charging-voltage.md#7-int1-寄存器污染确定的-abi-bug)。

- 改动点：`0x0566`、`0x05B2`、代码洞 `0x7E6A–0x7E80`，共 29 字节；
- 必须 `PUSH 17h`（此时 `PSW=0x10`，`17h` 才是 bank2 的 R7）；
- 不能用 `PSW=0x18` 切 bank3（`bank1:0x9A03` 的 `MOV 0x1C,@R0` 正在用 bank3 R4）；
- 全保存 R0–R7 需再增 8 字节栈深，`SP=0xC0` 余量不足，列为第二阶段。

验收：`0x0490.bit0=1`、`0x0497.bit0=1`、`0x0A54` 稳定为 1、`0x039D` 不再跳 `0x1B`、
Normal + 循环 <150 + 应力 ≤ `0x10E0` 时回到 17600 mV。

## 4. 校准直提

- 数据流：`XRAM[0x0388:0x0389]` → `bank1:E965` capacity_ratio_helper（÷100）→ 与
  `0x039B`（上界）/`0x039C`（下界）比较夹逼 → `0x0342` → `factor×100` → `0x0340:0x0341`。
- 最小补丁：物理 `0x14974`（`bank1:C974`）原 `90 03 9B`（`MOV DPTR,#0x039B`）→
  `02 C9 8E`（`LJMP bank1:C98E`），使 `0x4977–0x498D` 的夹逼不可达。
- 地址更正：提交点为 `C96E`，汇合点 `C98E`（旧文档的 `C970/C98F` 作废）。
- 风险：factor 是 8 位，粒度仍 100 mAh；取消夹逼后单轮异常可让 FCC 大跳；
  helper 对极小值/超容量/舍入未验证；`DA8E` 的 `0x0342 == 0x03C0` 发布条件在大跨度下未测。
- 长期方案应是“接受正常候选 + 绝对容量范围保护”，不是无保护直通。

## 5. 仅模式降压

- 只改 `bank1:D303–D31D`，跳到公共算术尾部 `D3EC`；保留 `D1D1` 的 gate、基准、串数乘法、
  16 位减法与 `0x0522:0x0523` 写回；`D300` 的低基准保护不动。
- 旁路 loop / stress / aux 五级比较，只留 `EC[0x07A6][5:4]`：
  `0x00` Normal 0 mV、`0x10` Balanced 100 mV/芯、`0x20` Health 200 mV/芯。
- 4S、17600 基准下的验收值：17600 / 17200 / 16800 mV，且 `0x0A54` 不再影响 Normal。
- 未实机验证。

## 6. 插桩补丁（只读探针）

- 主循环 hook：`common:0x0272 = 12 15 22`（`LCALL 0x1522`）；SMBus 窗口 hook：
  `common:0x1B22`（`12 1B 22`）。
- 代码洞 `common:0x7E6A–0x7FBA`（337 B）；产物只允许改
  `0x00272–0x00274`、`0x01B22–0x01B24`、`0x07E6A–0x07FBA`。
- mailbox `XRAM[0x0A79–0x0ABD]`（69 B）：`+0` doorbell、`+1` magic `0x53`、
  `+2` state、`+3` version、`+4..7` success bitmap、`+8..67` 30 个 LE word、`+68` count。
  状态：`0x00` idle / `0xA5` pending / `0x5A` accepted / `0xC3` complete。
- 现场必须保存 `ACC/B/DPTR/PSW/R0–R7` **以及** `XRAM[0x00C0:0x00C1]`（原厂 word-read
  的 scratch）；state=2 防重入；每个窗口只做一条只读事务。

这套探针证明了 `0x00–0x1C` 是厂商 byte 窗口、循环数来自 OEM `0x43`，并推翻了
“SBS `0x17` = CycleCount”的旧结论。

## 7. 产物登记

| 产物 | SHA-256 |
|---|---|
| FlexiCharge v2.1 | `41a193ce525e1d87cea5d31b30e29eb9755e60b9d25862b187ed234980749bd8` |
| FlexiCharge v2.2 | `ff6683769da655f3cafa364ddc9cfcaad84ad28fa7122191d3fefad00a7a19cf` |
| int1-r7 / cal-only / mode-only | 见 `_archive/firmware_mods/README_20260723.md`，不要凭记忆引用 |

## 8. 刷前检查

1. 产物大小 262,144 B，`firmware_tool.py analyze` 与基线同构；
2. `diff` 只出现白名单区间；
3. 代码洞边界 `CAVE + len <= 0x8000`，且基线全 `0xFF`；
4. 所有 `LCALL/LJMP` 目标已从原始 opcode 解码并确认 bank；
5. 有恢复手段（外接编程器或已验证的只读 dump）；
6. 记录当前 `EC[0x07B9]`、`0x07D0`、`0x0773`、`0x09C9–0x09CA`，作为回退对照。

## 9. 禁区

- 向 `ManufacturerAccess(0x00)` 写子命令；
- unseal / PF clear / FET / ship / Gauge Flash 写；
- 盲扫 `0x00–0xFF` 全协议；
- 在 ISR 内跑长事务；
- 未经确认的自动刷写；
- 无恢复手段时的任何写入实验。

## 10. 证据

`_archive/firmware_mods/`（五份设计/记录）、
`_archive/ec_firmware_instrumentation_tutorial_20260727.md`（插桩方法论与 v13 实例）、
`_archive/readable_firmware.md`（符号与反汇编流水线）。
