# IT5571 Follow Mode I/O Trace

基于 ifux64.efi (v2.0.3) 逆向分析，提取所有 0x66/0x62/0x64/0x60 I/O 操作。

## 符号

```
IBF = Status Bit 1 (0x02) — Input Buffer Full
OBF = Status Bit 0 (0x01) — Output Buffer Full
```

---

## 1. Enter Follow Mode

| Seq | Port | Dir | Data     | IBF 条件 | OBF 条件 | 注释            |
| --- | ---- | --- | -------- | ------- | ------- | ------------- |
| 1   | 0x64 | IN  | status   | —       | OBF=0   | 刷新 KBC 输出缓冲区   |
| 2   | 0x60 | IN  | drain    | —       | —       | (条件执行) 读取丢弃    |
| 3   | 0x64 | IN  | status   | IBF=0   | —       | 等待主机可写         |
| 4   | 0x64 | OUT | **0xAD** | —       | —       | **Disable KBC** |
| 5   | 0x64 | IN  | status   | IBF=0   | —       | 等待完成            |
| 6   | 0x66 | IN  | status   | IBF=0   | —       | 等待 EC 可写        |
| 7   | 0x66 | OUT | **0xDC** | —       | —       | **Enter Follow** |
| 8   | 0x66 | IN  | status   | IBF=0   | —       | 等待完成            |
| 9   | 0x66 | IN  | status   | —       | OBF=1   | 等待 EC 输出        |
| 10  | 0x62 | IN  | **ACK**  | —       | —       | 读取响应            |
| 10a | —    | —   | —        | —       | —       | 期望: **0x33**    |
| 11  | 0x66 | IN  | status   | IBF=0   | —       | 等待完成            |
| 12  | 0x66 | OUT | 0x01     | —       | —       | Selector 01       |
| 13  | 0x66 | IN  | status   | IBF=0   | —       |                   |
| 14  | 0x66 | OUT | 0x02     | —       | —       | Selector 02       |
| 15  | 0x66 | IN  | status   | IBF=0   | —       |                   |
| 16  | 0x66 | IN  | status   | IBF=0   | —       | 额外一次            |
| 17  | 0x66 | OUT | **0x9F** | —       | —       | **JEDEC Read ID** |
| 18  | 0x66 | IN  | status   | IBF=0   | —       |                   |

*注：Seq 12-18 实际通过函数 `ec_write_idx2(0x9F)` 实现。*

---

## 2. Read SPI ID (4 bytes JEDEC + 16 bytes Electronic ID)

开始 ID 事务时必须先向 `0x66` 直接写 `0x01`，再发送 Selector `0x02` 和
opcode `0x9F`。旧实现遗漏 direct `0x01` 时，真机读回了 `FF FF FF FF`。

| Seq | Port | Dir | Data     | IBF 条件 | OBF 条件 | 注释                |
| --- | ---- | --- | -------- | ------- | ------- | ----------------- |
| 19  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 20  | 0x66 | OUT | **0x04** | —       | —       | Selector 04 (读触发)  |
| 21  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 22  | 0x66 | IN  | status   | —       | OBF=1   |                    |
| 23  | **0x62** | **IN** | **ID[0]** | —    | —       | **JEDEC Byte 0**  |
| 24  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 25  | 0x66 | OUT | 0x04     | —       | —       |                    |
| 26  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 27  | 0x66 | IN  | status   | —       | OBF=1   |                    |
| 28  | 0x62 | IN  | ID[1]    | —       | —       | JEDEC Byte 1      |
| 29  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 30  | 0x66 | OUT | 0x04     | —       | —       |                    |
| 31  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 32  | 0x66 | IN  | status   | —       | OBF=1   |                    |
| 33  | 0x62 | IN  | ID[2]    | —       | —       | JEDEC Byte 2      |
| 34  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 35  | 0x66 | OUT | 0x04     | —       | —       |                    |
| 36  | 0x66 | IN  | status   | IBF=0   | —       |                    |
| 37  | 0x66 | IN  | status   | —       | OBF=1   |                    |
| 38  | 0x62 | IN  | ID[3]    | —       | —       | JEDEC Byte 3      |

*当 ID[0] != 0 时（JEDEC 方式成功），跳至读取 4 bytes；否则读取 16 bytes（Electronic ID 方式）。*

---

## 3. SPI Page Program (256 bytes)

| Seq | Port | Dir | Data     | 注释                        |
| --- | ---- | --- | -------- | ------------------------- |
| 39  | 0x66 | IN  | status   | Wait IBF=0                |
| 40  | 0x66 | OUT | 0x01     | Selector 01               |
| 41  | 0x66 | IN  | status   | Wait IBF=0                |
| 42  | 0x66 | OUT | 0x02     | Selector 02               |
| 43  | 0x66 | IN  | status   | Wait IBF=0                |
| 44  | 0x66 | IN  | status   | Wait IBF=0 (extra)        |
| 45  | 0x66 | OUT | **0x06** | **SPI WREN (Write Enable)** |
| 46  | 0x66 | IN  | status   | Wait IBF=0                |
| 47  | 0x66 | OUT | 0x01     | Selector 01               |
| 48  | 0x66 | IN  | status   | Wait IBF=0                |
| 49  | 0x66 | OUT | 0x02     | Selector 02               |
| 50  | 0x66 | IN  | status   | Wait IBF=0                |
| 51  | 0x66 | IN  | status   | Wait IBF=0 (extra)        |
| 52  | 0x66 | OUT | **0x05** | **SPI RDSR (Read Status)** |
| 53  | 0x66 | IN  | status   | Wait IBF=0                |
| 54  | 0x66 | OUT | 0x04     | Selector 04 (读触发)        |
| 55  | 0x66 | IN  | status   | Wait IBF=0                |
| 56  | 0x66 | IN  | status   | Wait OBF=1                |
| 57  | **0x62** | **IN** | status_val | 读取状态寄存器值               |
| 58  | —    | —   | —        | 检查 status & 0x03 == 0x02   |
| 59  | 0x66 | IN  | status   | Wait IBF=0                |
| 60  | 0x66 | OUT | 0x01     | Selector 01               |
| 61  | 0x66 | IN  | status   | Wait IBF=0                |
| 62  | 0x66 | OUT | 0x02     | Selector 02               |
| 63  | 0x66 | IN  | status   | Wait IBF=0                |
| 64  | 0x66 | IN  | status   | Wait IBF=0 (extra)        |
| 65  | 0x66 | OUT | **0x02** | **SPI Page Program opcode** |
| 66  | 0x66 | IN  | status   | Wait IBF=0                |
| 67  | 0x66 | OUT | 0x03     | Selector 03               |
| 68  | 0x66 | IN  | status   | Wait IBF=0                |
| 69  | 0x66 | IN  | status   | Wait IBF=0 (extra)        |
| 70  | 0x66 | OUT | addr[2]  | 地址高字节                    |
| 71  | 0x66 | IN  | status   | Wait IBF=0                |
| 72  | 0x66 | OUT | 0x03     | Selector 03               |
| 73  | 0x66 | IN  | status   | Wait IBF=0                |
| 74  | 0x66 | IN  | status   | Wait IBF=0 (extra)        |
| 75  | 0x66 | OUT | addr[1]  | 地址中字节                    |
| 76  | 0x66 | IN  | status   | Wait IBF=0                |
| 77  | 0x66 | OUT | 0x03     | Selector 03               |
| 78  | 0x66 | IN  | status   | Wait IBF=0                |
| 79  | 0x66 | IN  | status   | Wait IBF=0 (extra)        |
| 80  | 0x66 | OUT | addr[0]  | 地址低字节                    |
| 81  | 0x66 | IN  | status   | Wait IBF=0                |
| 82  | 0x66 | OUT | 0x03     | Selector 03               |
| 83  | 0x66 | IN  | status   | Wait IBF=0                |
| 84  | 0x66 | IN  | status   | Wait IBF=0 (extra)        |
| 85  | 0x66 | OUT | data[i]  | 数据字节 (256次)             |
| 86  | 0x66 | IN  | status   | Wait IBF=0                |
| ... | ...  | ... | ...      | 重复直到 256 字节             |

---

## 4. SPI Read (Verify)

| Seq | Port | Dir | Data     | 注释              |
| --- | ---- | --- | -------- | --------------- |
| 87  | 0x66 | IN  | status   | Wait IBF=0      |
| 88  | 0x66 | OUT | 0x01     | Selector 01     |
| 89  | 0x66 | IN  | status   | Wait IBF=0      |
| 90  | 0x66 | OUT | 0x02     | Selector 02     |
| 91  | 0x66 | IN  | status   | Wait IBF=0      |
| 92  | 0x66 | IN  | status   | Wait IBF=0      |
| 93  | 0x66 | OUT | **0x03** | **SPI READ opcode** |
| 94  | 0x66 | IN  | status   | Wait IBF=0      |
| 95  | 0x66 | OUT | 0x03     | Selector 03     |
| 96  | 0x66 | IN  | status   | Wait IBF=0      |
| 97  | 0x66 | IN  | status   | Wait IBF=0      |
| 98  | 0x66 | OUT | addr[2]  | 地址高字节          |
| 99  | 0x66 | IN  | status   | Wait IBF=0      |
| 100 | 0x66 | OUT | 0x03     | Selector 03     |
| 101 | 0x66 | IN  | status   | Wait IBF=0      |
| 102 | 0x66 | IN  | status   | Wait IBF=0      |
| 103 | 0x66 | OUT | addr[1]  | 地址中字节          |
| 104 | 0x66 | IN  | status   | Wait IBF=0      |
| 105 | 0x66 | OUT | 0x03     | Selector 03     |
| 106 | 0x66 | IN  | status   | Wait IBF=0      |
| 107 | 0x66 | IN  | status   | Wait IBF=0      |
| 108 | 0x66 | OUT | addr[0]  | 地址低字节          |
| 109 | 0x66 | IN  | status   | Wait IBF=0      |

然后通过 Selector 04 + 读 0x62 循环读取每个字节。

---

## 5. Exit Follow Mode

进入本节的顶层退出之前，每次 SPI 读事务均须直接向 `0x66` 写两次 `0x05`
完成子事务。旧版文档遗漏了该层，直接发送 `0xFC` 会使部分机器上的 EC 留在
Follow/SPI 状态，表现为风扇全速且键盘不可用，直至 EC 完全断电复位。

| Seq | Port | Dir | Data     | 注释               |
| --- | ---- | --- | -------- | ---------------- |
| 110 | 0x66 | IN  | status   | Wait IBF=0       |
| 111 | 0x66 | OUT | **0xFC** | **Exit Follow**  |
| 112 | 0x66 | IN  | status   | Wait IBF=0       |
| 113 | 0x64 | IN  | status   | Wait IBF=0       |
| 114 | 0x64 | OUT | **0xAE** | **Enable KBC**   |

*注：Seq 113-114 仅当使用了 Disable KBC (Seq 4) 时需要。*

---

## 合计 I/O 次数（按操作估算）

| 操作 | OUT 0x66 | OUT 0x64 | IN 0x66 | IN 0x62 | IN 0x64 | IN 0x60 |
| --- | ------- | ------- | ------- | ------- | ------- | ------- |
| Enter Follow | 2 | 1 | 7 | 1 | 3 | 1 |
| Read ID (4B) | 2 | — | 20 | 4 | — | — |
| Erase 64K | 8 | — | 22 | 2 | — | — |
| Program 256B | 260* | — | 520* | — | — | — |
| Verify 256B | 7+ | — | 14+ | 256+ | — | — |
| Exit | 1 | 1 | 4 | — | 2 | — |
