# 03 固件结构

## 1. 镜像布局

256 KiB 裸镜像，按 64 KiB 切四块，含两套独立程序：

| block | 文件范围 | 程序 | 入口 |
|---:|---|---|---|
| 0 | `0x00000–0x0FFFF` | 主 EC：fixed common + bank0 | `0x0000: LJMP 0x0070` |
| 1 | `0x10000–0x1FFFF` | 主 EC：bank1 + bank2 | — |
| 2 | `0x20000–0x2FFFF` | PD：common + bank0 | `0x20000: LJMP 0x83B7` |
| 3 | `0x30000–0x3FFFF` | PD：bank1 + bank2 | — |

主 EC 的 `FF` 空洞主要在 `0x0E928–0x0FDFF`、`0x1E97B–0x1FFFF`、`0x16E92–0x17FFF` 一带；
PD 在 `0x2D26F–0x2FFFF` 有 11,665 B 空洞。**空不等于可写**：新区块要按 02 章先擦后写。

## 2. 地址模型（最容易出错的地方）

主 EC 有 **32 KiB fixed common + 3 个 32 KiB bank window**，不是 64 KiB 平铺：

| 逻辑地址 | 文件偏移 |
|---|---|
| `common:0x0000–0x7FFF` | `0x00000 + addr` |
| `bank0:0x8000–0xFFFF` | `addr` |
| `bank1:0x8000–0xFFFF` | `0x10000 + (addr − 0x8000)` |
| `bank2:0x8000–0xFFFF` | `0x18000 + (addr − 0x8000)` |

例：`bank1:EE92` → `0x16E92`；`bank2:D729` → `0x1D729`。

**文件块号不是 code bank**：`samples/disasm/main-bank1.bin` 覆盖 `0x10000–0x1FFFF`，
其中同时包含 bank1 与 bank2 两个窗口。旧文档里“`main-bank1.bin` 偏移 `0xD729`”实际是
`bank2:D729`。

Bank 切换：`P1.0/P1.1/P1.2` 作为 code-space A16/A17/A18；thunk `0x1100`（bank0）、
`0x1114`（bank1）、`0x1128`（bank2），`R0`（`data[8]`）记当前 bank。
`common:0x1B22` 这类 veneer 的形态是 `MOV DPTR,#<bank内地址>; LJMP 0x11xx`，
**必须从原始 opcode 解出目标 bank**，不能看 `jump_xxxx` 标签。

## 3. 启动、调度、中断

复位链：`LJMP 0x0070` → `SP←0xC0` → `data[0x1001]←0x3F` → 调 `0x110A`(选 bank0) /
`0x159A` / `0x0FC3` / `0x15A0` → `data[0x2006]→data[0x0004]` → 初始化解释器 `0x00D2`
（读 `data[0x79C6]` 清 RAM/XRAM）→ `LJMP 0x0200` 主循环。

主循环派发器 `0x0CF8` 按位处理标志：`26h.5`→`0x0DCA`（Timer0 毫秒 worker）、
`26h.2`→`0x0AB6`、`26h.0`→`0x3F8C`、`26h.6`→`0x0808`、`27h.0`→`0x0D97`。
空闲时 `PCON=0x01`（IDLE）。

中断向量（主 EC）：

| 向量 | 入口 | 作用 |
|---|---|---|
| INT0 `0x03` | `0x0532` | 空（RETI） |
| T0 `0x0B` | `0x0533` | 重装定时器，置 `26h.5` |
| INT1 `0x13` | `0x0559` | 主机邮箱派发；**只保存 ACC/B/DPTR/PSW** |
| T1 `0x1B` | `0x05BF` | 定时 |
| Serial `0x23` | `0x05E7` | 空 |
| T2 `0x2B` | `0x05E8` | 空 |

Timer0 溢出周期 5.0009 ms（9.2 MHz / 12，count 3834）；调度单位 144 tick ≈ 720.1 ms。
`XRAM[0x09C7]` 累计到 60（≈43.2 s），`XRAM[0x09C8]` 再累计到 60（≈43.2 min）才允许
一次老化应力增量（详见 04）。

主机邮箱：`data[0x1110]` 分派；命令 `0x22`→`0x012F`、`0x23`→`0x018B`、`0x24`→`0x029E`，
其余查 `data[0x0637]` 表。`0x029E` 结束会置 `data[0x3290].7`、`data[0x1151].4`、
`0x1107.0/2`、`0x114D.6` 通知主机。

## 4. XRAM 可见性

XRAM 是 64 KiB 内部地址空间，主机**只能**经 `0xFED50000` 看到低 4 KiB 的四个窗口：

| 窗口 | 基址 | 大小 | 读 | 写 |
|---:|---:|---:|---|---|
| 0 | `0x000` | 1024 B | 全部 | 禁止 |
| 1 | `0x400` | 512 B | 全部 | 仅 `0x400–0x4FF` |
| 2 | `0x600` | 512 B | 全部 | 仅 `0x700–0x7FF` |
| 3 | `0xC00` | 1024 B | 全部 | 全部 |

有效权限图：

```text
0x000-0x3FF  R     0x400-0x4FF  RW    0x500-0x5FF  R
0x600-0x6FF  R     0x700-0x7FF  RW    0x800-0xBFF  不可达
0xC00-0xFFF  RW    ≥0x1000      不可达
```

`0x800–0xBFF` 没有任何窗口覆盖，换驱动、`acpi_call`、`/dev/mem` 都越不过去。
未映射或只读地址读取返回 `0xFF`，写入被忽略；但“写得进去”不等于“值能保持”，
`0xC00–0xFFF` 多由固件周期改写。

窗口由 `bank0:0xE2D7 configure_h2ram_host_windows` 配置（2.10 在 `0xE2BA`，9 个值相同）：

| 寄存器 | 值 | 意义 |
|---|---:|---|
| `0x105B` HRAMW0BA | `0x00` | 窗口 0 基址 |
| `0x105C` HRAMW1BA | `0x40` | 窗口 1 |
| `0x1076` HRAMW2BA | `0x60` | 窗口 2 |
| `0x1077` HRAMW3BA | `0xC0` | 窗口 3 |
| `0x105D` HRAMW0AAS | `0x36` | 窗口 0 权限 |
| `0x105E` HRAMW1AAS | `0x25` | 窗口 1 |
| `0x1078` HRAMW2AAS | `0x15` | 窗口 2 |
| `0x1079` HRAMW3AAS | `0x06` | 窗口 3 |
| `0x105A` HRAMWC | `0x0F` | 四窗口全启用（memory/FWH path） |

## 5. I2EC（唯一的全地址读通道）

原厂固件已开启 dedicated I2EC：

- `XRAM[0x2012] SPCTRL2.PI2ECEN = 1`、`PI2ECH = 0x06`、`PI2ECL = 0x80`（`CODE:0xDF47–0xDF59`）；
- 端口 `0x681`（addr-H）、`0x682`（addr-L）、`0x683`（data）；
- 权限由 `XRAM[0x200D] SPCTRL1.I2ECCTRL[1:0]` 决定：`00b` 禁用、`10b` 只读、`11b` 读写；
- 初始化表写 `20 0D 43`（`CODE:0x0FF7–0x1035`、`CODE:0xDF66`），公共代码再 `ORL #0xC8`
  → 最终 `0xCB` = 读写。想做只读镜像就把两处 `0x43` 改成 `0x42`（`0xCA`）。

限制：三次端口写是一个逻辑事务，全系统只能有一个客户端；逐字节读不原子；
`0x0000–0xFFFF` 混有 SRAM、未实现地址和片上外设，不能一律当 RAM。
唯一可用工具是 `tools/ec/i2ec_rw.py`（需 root 与 `/dev/port`）。

## 6. 分析流水线

| 工具 | 作用 |
|---|---|
| `tools/analysis/firmware_tool.py` | analyze / split / diff（哈希、非 FF 统计、空洞、差异簇） |
| `tools/analysis/extract_bank_entries.py` | 从 common wrapper 表恢复 bank 入口（2.12：402/62/47） |
| `tools/analysis/export_readable_ec.sh` | Ghidra 8051 伪 C 导出，区分 CODE/INTMEM/SFR/EXTMEM |
| `tools/analysis/annotate_disassembly.py` | 只标注 `MOV DPTR,#const`（2.12 bank1：691 个） |
| `tools/analysis/summarize_pseudoc.py` | 伪 C 语义索引 |
| `tools/analysis/translate_function_symbols.py` | 跨版本按函数体指纹迁移符号 |

已恢复规模：bank1 用 62 个入口得到 382 个函数，成功反编译 381（`CODE:0x1C60` 失败）。
2.10 与 2.12 的 PD bank 完全相同，`92,007` 个差异字节全在主 EC 前 128 KiB。

## 7. 陷阱清单

- `.d52` 由 `disasm51` 生成时带 `org 0-1h`，`dptr_NNNN` 系统性比真实立即数小 1
  （`#dptr_0522` 实际是 `90 05 23` → `0x0523`）。工具默认 `--symbol-bias 1`。
- 函数尾部复用：`CODE:E051` 读 `XRAM[0x0857]` 后落入 `E055`，不能把 `E051` 的读取
  算给所有 `E055` 调用者。
- CODE 与 EXTMEM 是不同地址空间，同名数字标签禁止无上下文全局替换。
- 伪 C 不是可编译源码：间接 DPTR、bank 切换、表跳转、错误函数边界都要人工复核。

## 8. PD 固件

`0x20000` 起是独立程序：RESET→`0x83B7`（清内 RAM `0x00–0x7F`、清 XRAM、`SP←0x17`、
`LCALL 0x500A`、`LJMP 0x8415`），INT0→`0x56`、T0→`0x94`、INT1→`0xB2`、T1→`0xF0`、
Serial→`0x10E`。只与主 EC 共用一片 Flash，内部逻辑尚未反汇编。

## 9. 证据

`_archive/xram_host_access.md`（窗口与 I2EC 全量）、`_archive/firmware_disassembly.md`
（启动与调度）、`_archive/readable_firmware.md`、`_archive/tools_usage.md`（工具细节）、
`_archive/ec_firmware_instrumentation_tutorial_20260727.md`（地址模型与 hook 方法）。
