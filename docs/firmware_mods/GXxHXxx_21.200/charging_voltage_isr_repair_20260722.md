# INT1 寄存器重入导致 16.6 V 降压：二进制根因与修复方案（2026-07-22）

## 已闭合的机器码链

老化路径的关键顺序（逻辑 bank1 地址）是：

```text
D1BA/D1BB  检查 0x0497.bit0，成功返回 R7=1
D259       LCALL 0xE1A0，检查 0x0490.bit2
D261       LCALL 0x1D08
D267       MOV  A,R7
D268       MOVX [0x0A54],A
D26C       MOVX [0x039D],A
```

`R7` 在 `D261` 前代表“会话/辅助路径成功”的返回值，正常值为 1。`0x1D08` 是跨 bank
wrapper 调用，执行期间可以被 INT1 中断。

主 EC 的 INT1 向量为 `0x0013 -> 0x0559`。其 ISR 原始序列是：

```text
0559: PUSH ACC
      PUSH B
      PUSH DPH
      PUSH DPL
      PUSH PSW
      MOV  PSW,#0x10      ; register bank 2
      ... 主机邮箱分派/间接调用 ...
05B2: POP  PSW
      POP  DPL/DPH/B/ACC
      RETI
```

它没有保存 `R0..R7`。主线现场 `PSW=0x16` 同样是 register bank 2，所以 ISR 的 `R7` 与
被中断的 `D1D1` 主线 `R7` 是同一组物理 RAM。邮箱分派返回或使用 `R7` 后，ISR 恢复 PSW
并返回；`D264` 随即把被污染的值写进 `0x0A54`。现场同时观察到：

```text
正常 D1BB 返回值       R7 = 1
D264 写入窗口           0x0A54 = 0x1B，0x039D = 0x1B
```

`0x1B` 不是电池遥测值，也不是 stress word；它符合 ISR 与主线共享 bank2 后的寄存器残留。
不同邮箱命令/间断时序会产生 `0x1B/0x2E/0x31/...` 等不同瞬时值，因此会出现“甚至更高”。

静态二进制确认了共享 bank2、不保存 R7、调用返回后消费 R7、以及 `0x039D` 的唯一直接写入
点；现场又观察到 `0x0A54=0x039D=0x1B`。这些证据足以把 INT1 R7 重入列为最强且可修复
的生产路径，但没有 INT1 入口计数或硬件单步，仍不能把具体每一次 `0x1B` 动态归因视为已
完全闭环。

## 永久修复（按可靠性排序）

### 本次选择：方案 B 的 R7-only 最小补丁

本次选择只保存/恢复 bank2 的 `R7`，而不是一次性保存全部 `R0..R7`。二进制和现场证据已经
把异常写入收敛到 `R7`，该补丁只增加 1 字节栈深，并保持 ISR 及其下游继续使用原 bank2
ABI。完整 R0..R7 保存会额外增加 8 字节栈深，而当前尚无最深邮箱调用路径的 SP 实测，风险
收益比更差；切换 bank3 则会让 R0..R7 覆盖内部 RAM `0x18..0x1F`，其中 `0x1C` 已有可达
代码正常使用。

离线生成器为版本目录内的 `patch_int1_r7.py`，只接受 SHA-256 为
`34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2` 的原始 256 KiB
镜像，并在修改前校验三个区间的原字节。生成命令：

```sh
python3 firmware_mods/GXxHXxx_21.200/int1-r7-20260722/patch_int1_r7.py \
  samples/GXxHXxx_21.200 \
  firmware_mods/GXxHXxx_21.200/int1-r7-20260722/GXxHXxx_21.200-int1-r7-20260722.bin
```

当前生成镜像 SHA-256 为
`65dcc65b328ebc77af4b2450e954f7ad9b5424211055708a4de0654854f80217`；与原镜像相比仅有
29 个字节变化。该产物只完成离线静态验证，尚未刷写或在真机执行。

### 方案 A：给 INT1 使用专用 register bank

将 ISR 切换到未被主线使用的 bank（建议 bank3，`PSW.RS1:RS0=11b`），并确认 ISR 调用的
所有子函数都按同一 ABI 编译/重入安全。概念上是：

```asm
PUSH PSW
ANL  PSW,#0xE7       ; 保留标志，清 bank 选择
ORL  PSW,#0x18       ; 选择 bank3
...
POP  PSW
```

不能只把 `MOV PSW,#0x10` 单字节盲改为 `#0x18` 而不审计 `0x012F/0x018B/0x029E` 及
间接邮箱处理函数；这些函数可能依赖当前 bank 的调用约定。

### 方案 B：ISR 保存/恢复全部 R0..R7

在 ISR 进入后、改 PSW 前保存被中断 bank 的 R0..R7，在 `POP PSW` 后按原 bank 恢复。由于
8051 的 `PUSH direct` 对 R0..R7 的地址取决于当前 bank，通用实现应使用独立 scratch 或
`MOV A,Rn; PUSH ACC` 的序列，不能简单写成 `PUSH 00h..07h`。这是对现有 ISR ABI 改动最小、
对主线最稳妥的修复。

### 方案 C：在 D1D1 的关键窗口暂时屏蔽 INT1

在 `D261` 调用前保存并清除 `EX1`，完成 `0x1D08` 返回值消费和 `D267` 起的两次写入后恢复
`EX1`。该窗口很短，可避免 R7 在关键区被改写；但会增加主机邮箱延迟，且必须处理已有
INT1 pending 标志，适合作为补丁而不是长期架构修复。

## 不足以根治的办法

- 反复写 `0x0522:0x0523=17600`：只能临时覆盖目标，`D1D1` 下一个周期会再次写回 16600；
- 清 `0x0A54`：它是共享 scratch，下一次 ISR 重入仍会重新污染；
- 只改 `0x039C` 或 `0x0342`：不能阻止错误的 250 mV/cell 分档；
- 只改 `0x09C9:0x09CA`：当前 16600 的直接输入是 `0x0A54`，不是 stress word；
- 单纯 AC 拔插或 reset：不会修复 ISR 的 register-bank ABI。

## 验证标准

修复后的 AC 在线只读序列应满足：

```text
0x0490.bit0 = 1、0x0497.bit0 = 1
0x0A54 在 D1D1 调用附近稳定为 1（或平台 hook 的真实返回值）
0x039D 不再与 0x1B/更高瞬时值同步跳变
Normal + cycle<150 + stress<=0x10E0 时，目标回到 17600 mV
```

应使用原始固件的副本做离线 patch/仿真验证，确认向量、bank wrapper、烧录校验和恢复路径
均未被破坏；在没有可回滚镜像前不应直接向 EC 写入修补字节。
