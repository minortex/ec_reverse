# SPI Opcode ↔ EC 协议映射

## EC 寄存器和 SPI 命令编码

EC 收到 Selector + 命令后，会 brige 到 SPI Flash 控制器。以下映射基于 ifux64.efi 静态分析。

## 命令映射表

| 阶段 | EC 命令编码                    | SPI Opcode | SPI 功能          |
| -- | --------------------------- | ---------- | --------------- |
| 读ID | ec_write_idx2(0x9F)          | 0x9F       | JEDEC Read ID   |
| 读ID | ec_write_idx2(0xAB)          | 0xAB       | Electronic ID   |
| 写使能 | ec_write_idx2(0x06)          | 0x06       | WREN            |
| 读状态 | ec_write_idx2(0x05)          | 0x05       | RDSR            |
| 读数据 | ec_write_idx2(0x03)          | 0x03       | READ            |
| 页编程 | ec_write_idx2(0x02)          | 0x02       | PP (Page Program) |
| 块擦除 | ec_write_idx2(0xD8)          | 0xD8       | BE 64K (Block Erase) |
| MXIC擦除 | ec_write_idx2(0xF8)          | 0xF8       | BE 64K (MXIC)   |
| MXIC写使能 | ec_write_idx2(0x50)          | 0x50       | EWSR (MXIC)     |

## EC 命令结构

### Host → EC

```
out 0x66, selector    ; 选择 EC 内部寄存器 (0x01-0x06)
out 0x66, data        ; 写入数据 (SPI opcode / addr byte / data byte)
```

### EC → Host (读)

```
out 0x66, 0x04        ; 选择 READ 寄存器 → 触发 SPI 读
in al, 0x62           ; 读取 EC 从 SPI Flash 获取的数据
```

## 时序关系

```
HOST                          EC                        SPI Flash
  |                            |                          |
  |-- Selector=0x02, Data=0x9F |                          |
  |                            |-- JEDEC Read ID (0x9F) →|
  |                            |                          |-- MFR ID
  |                            |←-- 4 bytes --------------|
  |← Selector=0x04, Read 0x62  |                          |
  |                            |                          |
  |-- Selector=0x02, Data=0x06 |-- WREN (0x06) →          |
  |                            |                          |-- Write Enable
  |-- Selector=0x02, Data=0xD8 |-- Block Erase (0xD8) →   |
  |-- Selector=0x03, Data=A2   |-- addr[2] →              |
  |-- Selector=0x03, Data=A1   |-- addr[1] →              |
  |-- Selector=0x03, Data=A0   |-- addr[0] →              |
  |                            |                          |-- Erase 64KB
  |                            |                          |-- Busy...
  |-- Selector=0x02, Data=0x05 |-- RDSR (0x05) →          |
  |← Selector=0x04, Read 0x62  |← status byte             |← status
  |                            |                          |
  |-- Selector=0x02, Data=0x02 |-- Page Program (0x02) →  |
  |-- Selector=0x03, Data=A2   |-- addr[2] →              |
  |-- Selector=0x03, Data=A1   |-- addr[1] →              |
  |-- Selector=0x03, Data=A0   |-- addr[0] →              |
  |-- Selector=0x03, Data=D0   |-- data[0] →              |
  |-- Selector=0x03, Data=D1   |-- data[1] →              |
  |-- ...                      |-- ...                    |
  |-- Selector=0x03, Data=D255 |-- data[255] →            |
  |                            |                          |-- Busy...
```

## 关键观察

1. EC 透明桥接 SPI 命令 — SPI opcode 通过 Selector 0x02 原样发送
2. 地址和数据通过 Selector 0x03 发送
3. 读取的数据通过 Selector 0x04 + 0x62 端口获取
4. Selector 0x01 (IFACE) 用于控制操作（写使能/读状态/其他）
5. Selector 0x05 用于标记操作完成

这意味着任何标准 SPI Flash 命令都可以通过这个接口发送，不仅仅是上面列出的这些。

更正：EFI 的 ID 读取路径在数据读取后还会向 `0x66` 直接写两次 `0x05`，用来
结束当前 SPI/EC 子事务。它们不是 Selector/Data 对，也不能用顶层 `0xFC` 代替。
另外，“任何标准 opcode 都可发送”只是接口形态推测；目前只确认 EFI 实际使用的
opcode，不能视为任意命令透传已经验证。
