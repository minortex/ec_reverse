# 02 刷写协议（Follow Mode / ISP）

来源：`ifux64.efi`（ITE Flash Utility 2.0.3）静态逆向 + 真机 ID/读验证。
实现以 `ec-flash` 为准，协议细节见 `_archive/protocol.md`、`_archive/io_trace.md`。

## 1. 端口与状态位

| 端口 | 方向 | 含义 |
|---|---|---|
| `0x64` | OUT | KBC 命令（`0xAD` 禁用 / `0xAE` 启用） |
| `0x64` | IN | KBC 状态 |
| `0x60` | IN | KBC 数据（进入前 drain） |
| `0x66` | OUT | EC 命令、Selector、Data |
| `0x66` | IN | EC 状态 |
| `0x62` | IN | EC 数据 |

| 位 | 名称 | 含义 |
|---|---|---|
| bit0 | OBF | EC 有数据可读 |
| bit1 | IBF | EC 输入缓冲未取走 |

所有等待都必须带超时；无超时的轮询在 EC 卡死时会锁死主机。

## 2. 进入

```text
drain 0x60                      ← 清残留 KBC 数据
out 0x64, 0xAD                  ← Disable KBC
轮询 0x64 直到 IBF=0
out 0x66, 0xDC                  ← Enter Follow
轮询 0x66 直到 IBF=0
轮询 0x66 直到 OBF=1
in 0x62 == 0x33                 ← ACK，否则中止
```

## 3. 退出与复位

| 序列 | 用途 |
|---|---|
| `0x66←0xFC` → drain `0x60` → `0x64←0xAE` | 退出 Follow，KBC 恢复；**EC 应用不恢复** |
| `0x66←0xFE` | EC Reset，平台直接复位 |

`0xFE` 之后不需要再补 `0xFC`；EFI 的 `--reset` 路径是 `0xFE → 0xFC → drain → 0xAE`。

## 4. Selector 协议

写操作固定两字节一组：`out 0x66, selector` 然后 `out 0x66, data`；
读操作 `out 0x66, 0x04` 然后 `in 0x62`。

| Selector | 用途 |
|---:|---|
| `0x01` | IFACE / 子事务开始 |
| `0x02` | CMD（SPI opcode） |
| `0x03` | DATA（地址字节、数据字节） |
| `0x04` | READ（触发一次读） |
| `0x05` | 结束当前子事务 |
| `0x06` | 保留 |

两条容易踩空的规则：

1. **每个 SPI 子事务开始前必须直接 `out 0x66, 0x01`**（不是 Selector/Data 对）。
   漏掉时 JEDEC ID 读回 `FF FF FF FF`。
2. **JEDEC ID 路径结束后必须直接向 `0x66` 写两次 `0x05`**（EFI RVA `0x2994`、`0x29BB`），
   不能用顶层 `0xFC` 代替。数据读路径（Fast Read）的收尾方式与 ID 路径不同。

## 5. SPI opcode

| Opcode | 命令 | 用法 |
|---:|---|---|
| `0x9F` | JEDEC ID | 读 4 字节 |
| `0xAB` | Electronic ID | 回退路径：3 字节 `0x00` 地址 + 读 16 字节 |
| `0x06` | WREN | 擦/写前必发 |
| `0x05` | RDSR | 查 WIP 与写使能 |
| `0x20` | Sector Erase 4 KiB | 每 64 KiB 发 16 次 |
| `0x02` | Page Program | 3 字节地址 + ≤256 字节 |
| `0x03` | Read Data | 普通读 |
| `0x0B` | Fast Read | 3 字节地址 + 1 dummy + 连读；dump 实际使用 |

地址字节按高→中→低发送。

## 6. 擦除 / 编程 / 校验

```text
每个变化的 64 KiB block：
  16 × ( WREN(0x06) → RDSR 检查 (sr & 0x03) == 0x02 → Sector Erase(0x20)+addr )
  轮询 RDSR.WIP(bit0) 直到清零
  读回全块，必须全 0xFF
  每 256 B：( WREN → Page Program(0x02)+addr+256B )
  读回全块，与源逐字节比较
```

256 KiB ÷ 64 KiB = **4 个 block**；观察到的“4 次擦/写/校验”是块数，不是重试次数。
未变化的块跳过。

## 7. 厂商分支

EFI 内置厂商表（Winbond `0xEF`、GigaDevice `0xC8`、EON `0x1C`、ISSI `0x9D`、ITE `0xD5`、
SST/旧 MXIC 等），并含 `0xD7`/`0x50` 等旧路径。本机是 XMC（JEDEC 首字节 `0x20`），
EFI 表里把 `0x20` 标为 ST/Micron 属表项语义问题；标准 4 KiB 擦 + 页编程路径适用，
厂商特化分支不适用，也不应猜测使用。

## 8. 错误处理

| 现象 | 处理 |
|---|---|
| `in 0x62` 返回 bit0=1 | 操作仍忙，重试 |
| RDSR.WIP=1 | 继续轮询 |
| 写使能未建立（`sr & 0x03 != 0x02`） | 重发 WREN，仍失败则中止 |
| SMBus/总线状态 `0x18` 或超时 | `bank0:0x8016` 复位控制器 |
| 任意阶段失败 | 等飞行中的 SPI 操作、WREN 关闭、退出 Follow、不创建复位标记 |

## 9. `ec-flash` 使用约束

```sh
sudo build/ec-flash --id
sudo build/ec-flash --dump dumps/machine-ec.bin
sudo build/ec-flash --verify samples/GXxHXxx_21.200
sudo build/ec-flash --write firmware.bin          # 破坏性，显式指定
sudo build/ec-flash --write block1.bin --address 0x10000
```

- dump/verify 默认两次读取并逐字节比较，不一致即失败。
- `--write` 要求输入是按 `0x10000` 对齐的完整块，只写变化的块。
- 写入成功后只**布置**一次关机复位标记 `/run/ec-flash-reset-on-shutdown`，
  由 `/usr/lib/systemd/system-shutdown/ec-flash-reset` 在关机末段消费；不会立即 `0xFE`。
- 该写入路径静态对齐 EFI 实现，但**未在真机执行过**。

## 10. 铁律

- 擦/写期间不得断电。
- 禁止在无恢复手段时执行写入。
- 仓库内不得私自运行 `ec-flash`：会禁用键盘，失联约 3 分钟后可能强制断电。
