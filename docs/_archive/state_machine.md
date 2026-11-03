# IT5571 Follow Mode 状态机

## 主流程

```mermaid
stateDiagram-v2
    Idle --> FlushKBC: start
    FlushKBC --> DisableKBC: OBF=0, IBF=0
    DisableKBC --> EnterFollow: out 0x64, 0xAD
    EnterFollow --> CheckACK: out 0x66, 0xDC
    CheckACK --> ReadID: ACK == 0x33
    CheckACK --> ErrorExit: ACK != 0x33

    ReadID --> StartTransaction: direct 0x01
    StartTransaction --> FinishTransaction: Selector 0x02 + ID opcode + read
    FinishTransaction --> MatchVendor: direct 0x05, direct 0x05
    MatchVendor --> EraseBlock: known vendor
    MatchVendor --> ErrorExit: unknown vendor

    EraseBlock --> WREN: out 0x66, 0x01 → ec_write_idx2(0x06)
    WREN --> CheckStatus: ec_write_idx2(0x05) → read 0x62
    CheckStatus --> DoErase: WIP=0 (status & 0x01 == 0)
    CheckStatus --> CheckStatus: WIP=1 → retry
    DoErase --> VerifyErase: ec_write_idx2(0xD8) + 3 addr bytes

    VerifyErase --> WREN2: read flash, expect FF
    WREN2 --> ProgramPage: ec_write_idx2(0x06)
    ProgramPage --> DataBytes: ec_write_idx2(0x02) + 3 addr bytes
    DataBytes --> DataBytes: ec_write_idx3(data[i]) × 256
    DataBytes --> NextPage: page done
    NextPage --> WREN2: if more pages in this block
    NextPage --> NextBlock: block complete

    NextBlock --> VerifyBlock: read back, compare with buffer
    VerifyBlock --> EraseBlock: if more blocks
    VerifyBlock --> ExitFollow: all done

    ExitFollow --> ResetEC: reset enabled by default
    ResetEC --> DrainKBC: out 0x66, 0xFE then 0xFC
    DrainKBC --> EnableKBC: drain pending 0x60 bytes
    EnableKBC --> Done: out 0x64, 0xAE
    ErrorExit --> Done: print error

    state EraseBlock {
        [*] --> DoErase: vendor/device-specific erase opcode
        DoErase --> WaitDone
        WaitDone --> [*]
    }

    state VerifyErase {
        [*] --> ReadLoop: 64KB
        ReadLoop --> CheckFF: read byte
        CheckFF --> ReadLoop: if FF
        CheckFF --> Fail: if not FF
        ReadLoop --> [*]: if done
    }

    state ProgramPage {
        [*] --> SendAddr: address bytes
        SendAddr --> SendData: 256 data bytes
        SendData --> [*]
    }

    state VerifyBlock {
        [*] --> ReadCompare: 64KB
        ReadCompare --> OK: match
        ReadCompare --> Mismatch: diff
        Mismatch --> Continue: log and continue
        Continue --> ReadCompare
        OK --> [*]
    }
```

## 重试循环

```mermaid
stateDiagram-v2
    state WaitIBF {
        [*] --> ReadStatus: in al, 0x66
        ReadStatus --> CheckBit1: test al, 0x02
        CheckBit1 --> ReadStatus: if IBF=1 → loop
        CheckBit1 --> [*]: IBF=0
    }

    state WaitOBF {
        [*] --> ReadStatus: in al, 0x66
        ReadStatus --> CheckBit0: test al, 0x01
        CheckBit0 --> ReadStatus: if OBF=0 → loop
        CheckBit0 --> [*]: OBF=1
    }

    state RetryOnError {
        [*] --> ReadStatus: in al, 0x66 (after OBF)
        ReadStatus --> CheckDataBit0: read 0x62
        CheckDataBit0 --> [*]: bit 0 clear → success
        CheckDataBit0 --> Restart: bit 0 set → retry
        Restart --> [*]: restart from selector 0x04
    }
```

## 块循环

```c
// 基于 64KB 块循环
for (block = 0; block < num_blocks; block++) {
    // 块地址
    block_addr = block * 0x10000;

    // 擦除
    spi_write_enable();
    spi_erase_64k(block_addr);
    verify_erase(block_addr);  // 读取全 block, 检查 FF

    // 编程
    for (page = 0; page < 256; page++) {  // 256 pages per block
        page_addr = block_addr + page * 256;
        spi_write_enable();
        spi_page_program(page_addr, buf + page_addr, 256);
    }

    // 验证
    verify_block(block_addr, buf + block_addr);  // 读回比较
}
```

这个循环解释了为什么用户观察到 4 次擦除/写入/验证 — 256KB 固件 ÷ 64KB = 4 个块。
