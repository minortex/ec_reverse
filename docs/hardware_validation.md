# 真机只读验证记录

## 1. 验证范围

本记录汇总 Linux `ec-read` 在目标机器上执行的只读实验。实验只使用 JEDEC
Read ID (`0x9F`) 和 EFI Verify 路径中的 Fast Read (`0x0B`)；工具没有实现
Write Enable、Erase 或 Page Program。

## 2. JEDEC ID

真机返回：

```text
20 40 14 20
```

前三字节 `20 40 14` 与 XM25QH80 的 JEDEC ID 一致；结合芯片丝印/实物核对，目标器件
应记录为 XMC XM25QH80。该型号容量为 8 Mbit（1 MiB），因此本次 1 MiB 读取覆盖整片
地址空间。第四字节是 EFI 固定读取的扩展/后续返回字节，不作为标准三字节 JEDEC
容量字段解释。

## 3. 完整 Flash dump

文件属性：

| 项目 | 值 |
|---|---|
| 文件 | `ec-full-1m.bin` |
| 大小 | 1,048,576 bytes |
| SHA-256 | `f42b9040c0d0f82732180bb01d989706f6d816132ba06db191fa270b654bf17d` |
| 全文件 byte-sum | `0xE064D9E` |

工具默认执行两次读取并在内存中逐字节比较；本次双读一致后才保存文件。

### 3.1 前 256 KiB

真机 dump 的 `0x00000–0x3FFFF` 与官方 `GXxHXxx_21.200` 逐字节完全一致：

```text
SHA-256: 34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2
cmp exit status: 0
```

各 64 KiB block：

| block | 地址 | SHA-256 | 非 `FF` 字节 | 解释 |
|---:|---:|---|---:|---|
| 0 | `0x00000` | `41cc767b71390fc2ea7ace43006c789e796f8db8edf356ca0c764d02f47ee1c6` | 58,337 | 主 EC bank 0 |
| 1 | `0x10000` | `a19a0b53cdbd1be1e9a173ff6e258f72212c92c0c78d8f083c89179ae02a5626` | 54,721 | 主 EC bank 1 |
| 2 | `0x20000` | `e9cda65663bc091c0afaf25a95ba33fe932d809afe711be61a829a27189c1570` | 52,580 | PD bank 0 |
| 3 | `0x30000` | `be291f2b42cb086d20b796195a815162259cc2fa27ddcac1b392c5ba5a8e375a` | 52,971 | PD bank 1 |

先前单独读取的 64 KiB `ec-block0.bin` 也与完整 dump 的 block 0 和官方 block 0
完全一致。这交叉验证了地址、64 KiB 边界和 Fast Read 数据路径。

### 3.2 后 768 KiB

`0x40000–0xFFFFF` 全部为擦除态 `0xFF`。未发现：

- 地址回绕；
- 官方镜像重复副本；
- 设备专属配置或校准区；
- 额外代码或数据 block。

整片最后一个非 `FF` 字节位于 `0x3EC37`。从 `0x3EC38` 到 `0xFFFFF`
是长度 791,496 bytes 的连续 `FF` 区间。因此当前机器的有效内容完全包含在官方
256 KiB 镜像覆盖范围内；物理 Flash 的其余空间未使用。

## 4. Follow Mode 与复位行为

真机行为表明存在两层不同状态：

1. SPI 子事务由 ID 路径的 direct `0x05` 或 Fast Read 的状态握手结束；
2. 顶层 `0xFC`/KBC `0xAE` 不会让 EC 应用固件重新从复位向量启动。

不发送 `0xFE` 时，主机仍可借助外接键盘操作，但 EC 功能没有恢复。普通 Linux
`poweroff` 只关闭主机，EC 仍由电池/待机电源供电；必须长按电源键触发硬件级 EC
复位。另一次实验中，完成 1 MiB dump 后保持该状态约三分钟，平台自动强制断电。
这很可能来自 EC watchdog、平台失联超时或电源保护，但目前无法仅凭现象确定具体
计时器。

因此“不发送 reset”不是可长期运行状态。当前工具默认不自动发送 `0xFE`，目的是先
保证 dump 持久化；操作者必须预期随后执行硬件复位。显式 `--reset` 才采用 EFI
的 `0xFE → 0xFC → drain KBC → 0xAE` 路径。

## 5. 文件持久化教训

早期版本在 `fwrite`/`fflush` 后立即发送 `0xFE`。平台直接复位时，Btrfs 上的文件
重启后变成 0 bytes，证明 stdio flush 不等于持久化完成。

当前 dump 保存流程已经改为：

```text
同目录临时文件
→ 完整 write
→ fdatasync
→ atomic rename
→ 目录 fsync
→ syncfs
→ 可选显式 0xFE reset
```

即使采用该流程，采集阶段仍建议不加 `--reset`，在确认文件存在、大小和哈希正确后
再通过长按电源执行硬件 EC 复位。

## 6. 已提升置信度的结论

| 结论 | 状态 |
|---|---|
| Follow Mode ACK 为 `0x33` | 真机确认 |
| JEDEC ID 为 `20 40 14 20` | 真机确认 |
| Fast Read (`0x0B`) 数据路径可用 | 真机确认 |
| 物理可读地址空间至少为 1 MiB | 真机确认 |
| 官方 256 KiB 等于设备前 256 KiB | 真机逐字节确认 |
| `0x40000–0xFFFFF` 全为 `FF` | 真机确认 |
| 读取过程未改变前 256 KiB | dump 与官方镜像一致 |
| `0xFC + 0xAE` 可恢复 EC 应用运行 | 否，真机反证 |
| `0xFE` 会导致平台直接 reset | 真机确认 |
