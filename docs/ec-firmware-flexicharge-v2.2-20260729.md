# EC 固件补丁：FlexiCharge v2.2（2026-07-29）

本文记录 `firmware_mods/GXxHXxx_21.200/build-from-official-20261103/patch_ec_flexicharge_v22.py` 从官方镜像生成的改版固件具备什么功能、
改写了哪些字节，以及与 `mfc bat` 主机端的对应关系。生成器只做静态字节变换，

## 1. 用法与产物

| 项 | 值 |
|---|---|
| 输入 | 官方 `GXxHXxx_21.200`，256 KiB（`0x40000`） |
| 输入 SHA-256 | `34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2` |
| 输出 SHA-256 | `ff6683769da655f3cafa364ddc9cfcaad84ad28fa7122191d3fefad00a7a19cf` |
| 相对官方镜像 | 304 字节变化，9 个区段（见第 4 节） |

```bash
uv run python firmware_mods/GXxHXxx_21.200/build-from-official-20261103/patch_ec_flexicharge_v22.py GXxHXxx_21.200 flexicharge-v2.2.bin
```

生成器只保留两道断言：输入必须是上面的官方镜像（摘要锁定所有原始字节），输出必须等于
上面的已复核摘要（锁定所有替换字节与偏移）。任一不符即退出，不会产出未复核镜像。
官方镜像不在本仓库内。

## 2. 四个补丁

| # | 补丁 | 完整偏移 | 原字节 | 新字节 | 目的 |
|---:|---|---:|---|---|---|
| 1 | 容量校准直接提交 | `0x14974` | `90 03 9b` | `02 c9 8e` | 跳过每轮 `±300 mAh` 的 FCC 限幅 |
| 2 | 模式锁定电压分档 | `0x1D303–0x1D31D` | 26 字节五级比较 | 26 字节单档 + 跳 `D3EC` | 降额档只由 `0x07A6[5:4]` 决定 |
| 3 | 忽略 ApExistFlag 清模式 | `0x1D1D7–0x1D1DB` | `12 df 7e 70 07` | `80 0a 00 00 00` | AP 不在线时也不再清 `0x07A6[5:4]` |
| 4 | FlexiCharge v2.2 | `0x0DE54`、`0x1D729`、`0x1D73A`、`0x07E6A–0x07F70` | 3×3 字节 hook + 263 字节空腔 | 见第 4 节 | 迟滞充电 + 下限持久化 |

bank 映射：fixed common `0x00000–0x07FFF`；bank2 逻辑窗口 `0x8000–0xFFFF` 存在
完整偏移 `0x18000–0x1FFFF`。

## 3. 打补丁后的功能

### 3.1 迟滞充电窗口（主功能）

| 寄存器 | 补丁后语义 |
|---|---|
| `0x04AB` | RSOC，实时相对电量 |
| `0x07B9[6:0]` | 上限百分比；`bit7` = 原厂停充/抑制标志 |
| `0x07D0[6:0]` | 下限百分比；`bit7` = 易失 phase（cycle-active） |
| `0x0773` | 持久化下限记录：`bit7` 有效位，`[6:0]` = 下限 |
| `0x077E`/`0x077F` | 原厂配置块提交握手，v2.2 写 `A5`/`78` 请求提交 |

| 当前条件 | `0x07D0.bit7` | `0x07B9.bit7` | 行为 |
|---|---:|---:|---|
| `SOC <= low` | 1 | 0 | 开始/继续低点启动的充电周期 |
| `low < SOC < high`，phase=1 | 1 | 0 | 中途拔插再插入，继续该周期 |
| `SOC >= high` | 0 | 1 | 结束周期并请求停充 |
| `low < SOC < high`，phase=0 | 0 | 1 | 首次插入或已结束周期，保持停充 |

phase 位区分「首次在中间区间插入」与「低点启动后中途拔插再插入」；只看 SOC 无法区分。

### 3.2 配置合法性

| 配置 | 补丁后行为 |
|---|---|
| `low=0, high=0` | disabled，等价 0/100 不限制 |
| `low=0, high>0` | 原厂 upper-only 兼容 |
| `high=0, low>0` | invalid：清运行位，跳过上限限制 |
| `high <= low` 或 `low>=96` | invalid：清运行位，不退化成错误的 upper-only |
| `1 <= low < high` | v2.2 迟滞窗口 |

invalid 路径只清 `0x07D0.bit7` 与 `0x07B9.bit7`，不擦低 7 位，避免主机分两步写
`0x07D0`、`0x07B9` 时被 EC 抢先清掉配置值。

### 3.3 持久化与重启

| 项 | 行为 |
|---|---|
| 下限持久化 | 变化时写 `0x0773 = 0x80 \| low`（bit7 有效位 + 低 7 位下限），并用 `0x077E=A5`/`0x077F=78` 握手提交 |
| phase 持久化 | 永不持久化；EC reset 后按 0 处理（中间区间默认停充，直到 SOC 回落 low） |
| 冷启动恢复 | 新增 `bank0:DE54` hook：先保留原厂 `LCALL bank0:DE03`，再恢复持久化下限 |
| 拔插适配器 | 不重置 EC/XRAM，周期可延续 |
| `0x07B9` 整体 | 不支持跨掉电保存；`bit7` 为运行态，重启后由调度器重算 |

### 3.4 双 hook 的原因

只 hook `bank2:D729` 不够：`D713`/`D718`/`D722`/`D727` 四条 gate 失败路径直接进入
`D73A` 无条件清 `0x07B9.bit7`，会摧毁 phase 决策。因此 `D729`（正常上限决策）与
`D73A`（gate 失败清位入口）都重定向到 cave。cave 保存/恢复 `ACC/B/DPTR/PSW/R0–R7`，
并重建 `D741` continuation 的 carry/PSW 约定。

### 3.5 补丁 3 的影响范围（容易误解的一点）

apexist-ignore **不是**关掉 AP 在线检测，只改电池老化降额函数里的一步：

| 范围 | 是否受影响 |
|---|---|
| `bank1:D1D7` 处：AP 不在线时清 `0x07A6[5:4]` | 不再执行 |
| `bank1:D1D1` 处 `0x078E |= 0x08` 的写回 | 不再执行（该位无已确认读取者） |
| `LCALL 0xDF7E`（`is_ap_present`）其余 5 个调用点 `0x1A97C/0x1AB2A/0x1BCC2/0x1C9D2/0x1D832` | 不变 |
| 固件内 19 处直接 `MOV DPTR,#0x0741` 读写 | 不变 |
| EC 自主清 `0x0741.bit0`（`bank1:E611`，调用点 `C949`/`CB3F`） | 不变 |
| 主 AP 风扇表 gate（`0x0741.bit0 + 0x07C6.bit2 + 0x0727.bit6`）、功耗表、模式/背光 | 不变 |
| kmod 的 `probe`/`resume` 设位、`remove`/`shutdown` 清位 | 不受影响 |

`0x07A6[5:4]` 固件从不写入（5 处引用全是读），只由 GCU/BIOS 设置；补丁 2 把它变成
唯一降额档位来源，补丁 3 防止它被清。二者只在使用方依赖充电模式档时才有意义。

## 4. 字节改动

| 区段 | 变化字节数 | 内容 |
|---|---:|---|
| `0x07E6A–0x07F70` | 262 | 全 `FF` 空腔 → v2.2 状态机（263 字节，1 字节恰为 `FF`） |
| `0x0DE55–0x0DE56` | 2 | `12 de 03` → 指向 cave `restore_entry` |
| `0x14974–0x14976` | 3 | `90 03 9b` → `02 c9 8e`（`LJMP bank1:C98E`） |
| `0x1D1D7–0x1D1DB` | 5 | `12 df 7e 70 07` → `80 0a 00 00 00` |
| `0x1D303–0x1D31D` | 26 | 五级比较 → `0x07A6[5:4]` 单档 + 跳 `D3EC` |
| `0x1D729–0x1D72B` | 3 | `90 04 ab` → `LJMP` cave `decision_entry` |
| `0x1D73A–0x1D73C` | 3 | `90 07 b9` → `LJMP` cave `preclear_entry` |

补丁 1 使 `0x4977–0x498D` 成为不可达死代码，不额外覆盖；补丁 2 保留 `D300` 的低基准
保护跳转，跳回原厂公共算术尾部 `bank1:D3EC`。

## 5. 与 `mfc bat` 的对应

| 主机端 | 固件侧 |
|---|---|
| `mfc bat set -d <down> -u <up>` | 先写 `0x07D0` 再写 `0x07B9`，避免两步写被抢先清除 |
| `mfc bat status` 的 stop-bit / cycle-active | `0x07B9.bit7` / `0x07D0.bit7` |
| `mfc bat status` 的保存记录与握手 | `0x0773` 有效位、`0x077E=A5`/`0x077F=78`（待提交）或 `55/AA`（已消费） |
| `mfc bat charge-full` | 同一事务先清 `0x07D0` 再清 `0x07B9` → disabled/unrestricted，只清运行位 |
| `mfc bat set -u <up>`（无 `-d`） | `low=0, high>0` → 原厂 upper-only 兼容路径 |
| `mfc bat status` 显示的 charge mode | 只读 `0x07A6[5:4]`；主机与 kmod 均不写它 |

未刷入本固件时 `0x07D0` 写入可能成功但充电行为不变（原厂无消费者）。
