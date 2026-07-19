# IT5571 Follow Mode / ISP 协议文档

基于 ifux64.efi v2.0.3 逆向分析 | 适用 IT5571 + 0x66/0x62 接口

---

## 1. I/O Port 定义

| Port | 方向 | 用途         |
| ---- | ---- | ---------- |
| 0x64 | OUT  | KBC 命令      |
| 0x64 | IN   | KBC 状态      |
| 0x60 | IN   | KBC 数据 (drain) |
| 0x66 | OUT  | EC 命令/状态选择 |
| 0x66 | IN   | EC 状态       |
| 0x62 | IN   | EC 数据       |

### 状态寄存器位

```
Bit 0 (0x01): OBF — Output Buffer Full (EC→Host 数据可读)
Bit 1 (0x02): IBF — Input Buffer Full (Host→EC 未完成)
```

---

## 2. 进入/退出协议

### 2.1 进入 Follow Mode

```
flush KBC (drain 0x60)          ; 清理键盘控制器
wait IBF=0                       ; 等待 KBC 可写
out 0x64, 0xAD                   ; Disable KBC
wait IBF=0                       ; 等待 KBC 完成

wait IBF=0                       ; 等待 EC 可写
out 0x66, 0xDC                   ; Enter Follow Mode
wait IBF=0                       ; 等待 EC 接收
wait OBF=1                       ; 等待 EC 响应
in al, 0x62                      ; 读取 ACK
if al != 0x33: error             ; 期望固定 ACK 0x33
```

### 2.2 退出 Follow Mode

```
wait IBF=0
out 0x66, 0xFC                   ; Exit Follow Mode
wait IBF=0

[如果之前发送了 0xAD]
  wait IBF=0
  out 0x64, 0xAE                 ; Enable KBC
```

注意：上述 `0xFC` 是顶层 Follow Mode 退出。在执行它之前，当前 SPI 子事务必须
先结束。`ifux64.efi` 的 JEDEC ID 路径在读取完成后向 `0x66` **直接写入两次
`0x05`**，然后才会在主流程末尾发送 `0xFC`。两层退出不能互相替代。

### 2.3 复位 EC

```
wait IBF=0
out 0x66, 0xFE                   ; EC Reset
```

Linux 工具默认不发送 `0xFE`。它会导致平台立即复位，不能把普通 stdio flush
当作文件已经在 Btrfs 上持久化。只有显式 `--reset` 才启用该路径，且必须发生在
临时文件同步、原子 rename、目录同步和 `syncfs` 全部成功之后。

---

## 3. EC 寄存器接口

进入 Follow Mode 后，通过选择器 (Selector) 访问 EC 内部寄存器：

每个 SPI 子事务开始前，EFI 会先直接执行 `out 0x66, 0x01`。这不是
Selector/Data 二元写入；遗漏它会导致后续 opcode 没有进入正确的 SPI 事务状态，
典型表现是 JEDEC ID 读回全 `0xFF`。

### Selector 协议

每个写操作分两步：
1. 写 Selector 值到 0x66
2. 写 Data 值到 0x66

```
ec_write_idx2(al):               ; Selector=0x02
  wait IBF=0
  out 0x66, 0x02
  wait IBF=0
  wait OBF=0 (extra IBF check)
  out 0x66, al
  wait IBF=0

ec_write_idx3(al):               ; Selector=0x03  
  wait IBF=0
  out 0x66, 0x03
  wait IBF=0
  wait OBF=0 (extra IBF check)
  out 0x66, al
  wait IBF=0
```

### Selector 映射

| Selector | 用途              |
| -------- | --------------- |
| 0x01     | IFACE — 接口控制    |
| 0x02     | CMD — SPI 命令发送  |
| 0x03     | DATA — SPI 数据发送  |
| 0x04     | READ — 读触发 + 读数据 |
| 0x05     | 完成/退出当前操作       |
| 0x06     | 未使用 (保留)        |

### 读操作

```
ec_read():
  wait IBF=0
  out 0x66, 0x01      ; Select IFACE
  wait IBF=0
  ec_write_idx2(0x05) ; 通过 CMD 写 0x05 (疑似 SPI 读触发)
  wait IBF=0
  out 0x66, 0x04      ; Select READ (开始读)
  wait IBF=0

  wait OBF=1           ; 等待 EC 输出数据
  in al, 0x62          ; 读取数据
  if al & 0x01: retry  ; bit 0 = busy

  wait IBF=0
  out 0x66, status     ; 写状态码
  wait IBF=0
  ret
```

---

## 4. SPI 命令协议

### 4.1 JEDEC Read ID (0x9F)

```
ec_write_idx2(0x9F)          ; 发送 JEDEC Read ID opcode
; 上一行之前必须先执行：wait IBF=0; out 0x66, 0x01; wait IBF=0
; 然后连续 Selector=0x04 + 读 0x62 × 4
for i in 0..3:
  wait IBF=0
  out 0x66, 0x04
  wait IBF=0
  wait OBF=1
  ID[i] = in 0x62
; 结束 SPI/EC 子事务（EFI RVA 0x2994、0x29BB）
wait IBF=0; out 0x66, 0x05; wait IBF=0
wait IBF=0; out 0x66, 0x05; wait IBF=0
```

### 4.2 Electronic ID Read (0xAB) — 回退方式

```
ec_write_idx2(0xAB)          ; 发送 Electronic ID opcode
ec_write_idx3(0x00) × 3     ; 3 字节虚拟地址
; 然后连续 Selector=0x04 + 读 0x62 × 16
```

### 4.3 Write Enable (WREN, 0x06)

```
ec_write_idx2(0x06)          ; SPI WREN
```

### 4.4 Read Status Register (RDSR, 0x05)

```
ec_write_idx2(0x05)          ; 发送 RDSR
; 然后 Selector=0x04 + 读 0x62 获取状态值
; 检查 WIP (Busy) 位
```

### 4.5 Sector Erase 64KB (0xD8)

```
ec_write_idx2(0xD8)          ; SPI 64KB Block Erase
ec_write_idx3(addr[2])       ; 地址高字节
ec_write_idx3(addr[1])       ; 地址中字节
ec_write_idx3(addr[0])       ; 地址低字节
```

本机 XM25QH80 的实际擦除命令应以芯片数据手册和总线抓取为准；现有只读实验
没有验证擦除/编程，因此不能把旧文档中的 MXIC `0xF8`/`0x50` 分支套用于本芯片。

### 4.6 Page Program (0x02)

```
ec_write_idx2(0x02)          ; SPI Page Program
ec_write_idx3(addr[2])       ; 地址高字节
ec_write_idx3(addr[1])       ; 地址中字节
ec_write_idx3(addr[0])       ; 地址低字节
for each data byte:
  ec_write_idx3(data[i])     ; 数据字节
; 页面大小: 256 字节
```

### 4.7 Read Data (0x03)

```

### 4.8 EFI Verify Fast Read (0x0B)

实际 dump 路径应复用 EFI 已使用的 Verify 序列，而不是假设 `0x03` 分块读取：

```text
direct 0x01
Selector 0x02, opcode 0x0B
Selector 0x03, addr[2], addr[1], addr[0], dummy 0x00
Selector 0x04 + read 0x62，连续读取至 64 KiB 边界
执行 RVA 0x27CC 的状态收尾握手
```
ec_write_idx2(0x03)          ; SPI Read Data
ec_write_idx3(addr[2])       ; 地址高字节
ec_write_idx3(addr[1])       ; 地址中字节
ec_write_idx3(addr[0])       ; 地址低字节
; 然后 Selector=0x04 + 读 0x62 × N
```

---

## 5. 厂商 ID 表

| ID[0] | ID[1] | ID[2] | 厂商         |
| ----- | ----- | ----- | ---------- |
| 0xBF  | —     | —     | MXIC/SST（刷写器兼容分支，非本机 XM25QH80） |
| 0xEF  | 0x40  | 0x19  | Winbond    |
| 0xC8  | —     | —     | GigaDevice |
| 0x1C  | —     | —     | EON        |
| 0x1F  | —     | —     | Atmel      |
| 0x20  | —     | —     | ST/Micron  |
| 0x01  | —     | —     | Spansion   |
| 0x37  | —     | —     | AMIC       |
| 0x9D  | —     | —     | ISSI       |
| 0xE0  | —     | —     | ESMT       |
| 0xD5  | —     | —     | ITE        |

---

## 6. 完整流程

```
┌─────────────────────────────────────────┐
│ 1. flush KBC                            │
│ 2. Disable KBC (0xAD)                   │
│ 3. Enter Follow Mode (0xDC)             │
│ 4. Verify ACK == 0x33                   │
├─────────────────────────────────────────┤
│ 5. Read SPI ID (JEDEC 0x9F / 0xAB)     │
│ 6. Match manufacturer & select handler  │
├─────────────────────────────────────────┤
│ for each 64KB block:                    │
│   7. Write Enable (0x06)                │
│   8. Read Status (0x05) check busy      │
│   9. Erase 64KB (0xD8)                  │
│   10. Verify Erase (read 0x03 + FF check)│
│   11. for each 256B page:               │
│     12. Write Enable (0x06)             │
│     13. Program (0x02 + addr + data)    │
│   14. Verify (0x03 read + compare)      │
├─────────────────────────────────────────┤
│ 15. Exit Follow Mode (0xFC)             │
│ 16. Enable KBC (0xAE)                   │
└─────────────────────────────────────────┘
```

---

## 7. 异常处理

| 条件                       | 行为         |
| ------------------------ | ---------- |
| ACK != 0x33              | 打印错误，退出    |
| Erase Verify 发现非 FF    | 打印偏移 + 失败  |
| Verify 数据不匹配           | 打印 [addr]=val |
| SPI 状态寄存器 bit 0 (WIP) | 等待重试       |
| 读 0x62 返回 bit 1 set    | 重试          |
