# IT5571 EC 固件插桩教程：从二进制核对到只读 SMBus 探针

> 日期：2026-07-27  
> 适用项目：`GXxHXxx_21.200` / IT5571 主 EC 固件  
> 示例版本：`cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727`  
> 范围：讲解如何在现有 EC 二进制上加入**可请求、可观测、可撤回的只读插桩**。本文不是刷写教程，也不授权自动执行 `ec-flash`。

## 1. 先给安全结论

当前 v13 探针从已恢复的事务类型看，**直接触发电池包化学熔丝、永久失效 PF、永久封锁或改写 Fuel Gauge 配置的风险很低，但不能承诺理论上的绝对零风险**。

原因是 v13 只构造 SMBus word-read：

- 扫描标准命令 `0x00–0x1C`；
- 读取原厂固件本来就在使用的 OEM 命令 `0x42/0x43/0x47`；
- 使用原厂已验证的参数 `R3=0xA1, R5=0x0C, R7=0x00`；
- 没有 Smart Battery write-word 或 block-write；
- 没有向 `ManufacturerAccess (0x00)` **写入**子命令；
- 没有 unseal、seal、reset、PF clear、FET control、ship、shutdown、认证写入或 Gauge flash/data-memory 写入；
- 主机端唯一主动写入是 EC XRAM doorbell `XRAM[0x0A79]=0xA5`，不是向电池地址写数据。

v13 中对 `0x00` 的操作也是 word-read。命令号为 `0x00` 不等于执行 ManufacturerAccess 写子命令；真正危险的是向它写入特定子命令或数据。

### 1.1 风险分层

| 风险 | 当前 v13 判断 | 说明 |
|---|---|---|
| 只读命令直接导致熔断/PF/永久锁死 | 低，但非理论零 | 没有发现写事务；三个 OEM 命令也是原厂已有读取项 |
| SMBus 拥塞、超时、短时遥测陈旧 | 低到中 | 全部扫描需要附加 32 条事务，但每个真实原厂窗口只附加一条 |
| EC 状态机被插桩缺陷破坏 | 比命令本身更值得警惕 | 错误的控制流、栈、寄存器、XRAM scratch 保存可能挂 EC |
| 长时间 EC 异常间接影响充放电控制 | 不能忽略 | 电池保护仍在，但不应依赖保护芯片替错误固件兜底 |
| 未知 ManufacturerAccess/OEM 写命令 | 高风险，禁止盲试 | 可能涉及 PF、FET、认证、封印、寿命数据或 flash |

### 1.2 为什么不能说“绝对安全”

1. 未知厂商读命令通常不应产生永久副作用，但仅靠主 EC 镜像不能证明电池 Gauge 的全部内部实现。
2. 固件插桩运行在充放电控制器中。即使命令本身只读，保存/恢复错误仍可能破坏 EC 调度。
3. Fuel Gauge/保护芯片可因真实过压、过流、短路、过温或单体欠压进入 PF；这与只读扫描不是同一件事，但 EC 长时间失控可能间接增加风险。
4. 静态测试能证明“生成的机器码符合预期”，不能代替所有真机时序组合。

因此，后续工作应继续坚持：**观察原厂已有事务优先于主动发起事务；只读优先于写入；单步调度优先于连续爆发扫描。**

---

## 2. 项目规则与蓝本

### 2.1 二进制是唯一蓝本

本项目中：

- `.bin` 固件才是处理器实际执行的内容；
- `.d52`、伪 C、函数名和标签只用于提高可读性；
- 文档中的历史结论也可能需要回到二进制重新验证；
- `LCALL/LJMP` 目标必须从调用点的原始 opcode 解码，不能从 `jump_xxxx`、猜测的函数边界或伪 C 符号反推。

修改补丁前至少固定以下三项：

```text
输入文件路径
输入文件长度
输入文件 SHA-256
```

v13 使用的输入基线为：

```text
firmware_mods/GXxHXxx_21.200/
  cal-direct-mode-locked-apexist-ignore-20260723/
  GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-20260723.bin
```

基线属性：

```text
size:    262144 bytes (0x40000)
sha256:  aa4a52ab7bbeff22e38b0845bc627b735c7b7e6b28ca6868093454a5696dd5d5
```

### 2.2 不要把分析动作和硬件动作混在一起

下列动作不需要 sudo：

- 读取本地 `.bin`；
- 生成补丁；
- 计算哈希；
- 反汇编；
- 运行 pytest；
- 比较二进制差异。

只有 `tools/i2ec_rw.py` 实际访问 `/dev/port` 时才需要 sudo/提权。不得为了方便给整个分析过程提权。

**禁止自动执行 `ec-flash`。** 该命令会影响键盘和看门狗行为，刷写必须由用户明确控制。

---

## 3. 先理解地址模型

`GXxHXxx_21.200` 是 256 KiB 原始镜像：

```text
0x00000–0x1FFFF  IT5571 主 EC
0x20000–0x3FFFF  ITE8850 PD 固件
```

主 EC 使用一个 fixed common 区和三个 32 KiB bank window：

| 逻辑 CODE 地址 | 物理文件偏移 | 说明 |
|---|---:|---|
| `common:0000–7FFF` | `0x00000–0x07FFF` | 固定公共区，不随 bank 切换 |
| `bank0:8000–FFFF` | `0x08000–0x0FFFF` | bank0 窗口 |
| `bank1:8000–FFFF` | `0x10000–0x17FFF` | bank1 窗口 |
| `bank2:8000–FFFF` | `0x18000–0x1FFFF` | bank2 窗口 |

换算示例：

```python
bank0_file_offset = logical_address
bank1_file_offset = 0x10000 + (logical_address - 0x8000)
bank2_file_offset = 0x18000 + (logical_address - 0x8000)
```

因此 `bank1:EE92` 对应：

```text
0x10000 + (0xEE92 - 0x8000) = 0x16E92
```

### 3.1 bank dispatch gate

当前固件 fixed common 区存在三个分派入口：

```text
common:1100 -> bank0
common:1114 -> bank1
common:1128 -> bank2
```

典型 wrapper 是：

```asm
MOV  DPTR,#banked_target
LJMP common:11xx
```

或需要返回时使用相应的 call 语义。目标 bank 不能只由 `DPTR` 地址猜测，必须同时核对 gate。

### 3.2 从 raw opcode 解码控制流

v13 核对的原始 `common:1B22` 字节为：

```text
90 80 D2 02 11 00
```

逐条解码：

```asm
MOV  DPTR,#0x80D2
LJMP 0x1100
```

由 `0x1100` gate 可确认真正目标是 `bank0:80D2`，而不是当前 bank 中任意一个同名 `0x80D2`。

主循环原始 hook 点：

```text
common:0272: 12 15 22
```

即：

```asm
LCALL common:1522
```

这里 `<0x8000`，属于 fixed common。v13 将它替换为：

```text
12 7E 6A    ; LCALL common:7E6A
```

插桩完成后再回到原始 `common:1522`，保留原调用语义。

---

## 4. 选择 hook 点

一个实用插桩通常至少分成两个入口：

1. **请求入口**：主循环中低频、确定会执行的位置，用于识别 doorbell 并初始化记录；
2. **观测/工作入口**：靠近目标事务或目标函数，用于捕获现场或执行一个小步骤。

### 4.1 选择条件

候选 hook 点应满足：

- 原始指令边界明确；
- 能取得足够的前后 raw context；
- 不在不可控的中断高频热路径中；
- 能恢复原始语义；
- 覆盖长度足以放置 `LCALL` 或 `LJMP`；
- 原始目标、返回方式和 active register bank 已核对。

补丁生成器不应只验证待替换的 3 个字节，还应验证周围上下文。例如 v13 同时检查：

```text
common:026F 附近主循环上下文
common:1B1C 附近 SMBus veneer 上下文
原厂 0x42/0x43/0x47 调用点上下文
bank0:80D2 worker 前缀
common:1100 dispatch gate 前缀
```

这样可以避免把一个针对特定固件的补丁误打到“地址相同但内容不同”的版本上。

### 4.2 `LCALL` 与 `LJMP` 不能互换理解

- 用 `LCALL cave` 替换原始 `LCALL target` 时，cave 一般应在最后调用原始目标并正常 `RET`，或等价恢复原始调用栈。
- 用 `LJMP cave` 替换一个 veneer 的开头时，cave 必须自己决定最终 tail-jump 到原始 veneer 目标，不能多压或少弹一层返回地址。
- 被覆盖的完整原始指令必须在 cave 中重放，不能只恢复“看起来相同”的高级语义。

v13 对 `common:1B22` 使用 `LJMP common:7E95`，普通路径最后重放：

```asm
MOV  DPTR,#0x80D2
LJMP common:0x1100
```

探测路径需要先得到原始事务返回值，因此另建可返回 thunk：

```asm
MOV  DPTR,#0x80D2
LCALL common:0x1100
RET
```

注意：patched veneer 内不能再次 `LCALL common:1B22`，否则会递归回自己的 hook。

---

## 5. 寻找并验证 code cave

### 5.1 不要只看到 `0xFF` 就认为可用

选择 cave 时至少验证：

1. 连续空间足够；
2. 位于正确的 common/bank；
3. 不跨越 `0x8000` fixed/banked 边界；
4. 没有控制流落入；
5. 不是查找表、校验区或延迟加载数据；
6. 基线中整个拟用区间都符合预期字节；
7. 输出只修改 hook 和 cave 允许范围。

v13 使用：

```text
common:7E6A–7FBA
physical: 0x07E6A–0x07FBA
size: 337 bytes
```

补丁生成前要求对应基线区间全部为 `0xFF`，并要求：

```python
CAVE_LOGICAL + len(CAVE) <= 0x8000
```

这样可避免 cave 跨入 banked window。

### 5.2 输出变化白名单

不要只检查输出哈希，还要计算 changed-byte set：

```python
changed = {
    i for i, (before, after) in enumerate(zip(source, output))
    if before != after
}
allowed = hook_ranges | cave_range
assert changed <= allowed
```

v13 允许修改：

```text
main hook:   0x00272–0x00274
SMBus hook:  0x01B22–0x01B24
code cave:   0x07E6A–0x07FBA
```

实际不同字节总数为 341。由于 hook 或 cave 中个别新字节可能恰好与原 `0xFF` 相同，changed-byte 数不一定等于所有允许区间长度之和。

---

## 6. 设计 XRAM mailbox，而不是靠“碰巧读到值”

### 6.1 doorbell 状态机

推荐至少使用四个状态：

```text
0x00  idle，未观察到请求
0xA5  host request pending
0x5A  EC 已接受请求
0xC3  EC 已完成请求
```

这能区分三类故障：

- 一直 `A5`：主机写入可见，但请求 hook 没执行；
- 到 `5A` 不到 `C3`：hook 已执行，工作调度未完成；
- 到 `C3` 但 success mask 为零：EC 执行完成，但目标事务均失败。

v13 mailbox：

```text
XRAM 0x0A79–0x0ABD，共 69 bytes
```

布局：

| 偏移 | 内容 |
|---:|---|
| `+0` | doorbell |
| `+1` | magic `0x53` |
| `+2` | state |
| `+3` | protocol version `0x0D` |
| `+4..+7` | 32-bit success bitmap |
| `+8..+67` | index 0–29 的 30 个 little-endian word |
| `+68` | completed command count |

### 6.2 如何选 XRAM 区域

最低限度检查：

- 基线二进制中是否存在 `MOV DPTR,#address` 的直接引用；
- 相邻地址是否有明确原厂用途；
- mailbox 是否跨越已知结构边界；
- 插桩是否会与原厂共享 scratch 冲突。

v13 的 mailbox 结束在 `0x0ABD`，基线中紧邻的第一个直接引用地址是 `0x0ABE`。

但要注意：**“没有直接 DPTR 引用”并不能数学证明完全未使用。** 固件仍可能通过间接指针、基址加索引或 DMA 访问。选址还需结合真机稳定性和执行窗口验证。

### 6.3 mailbox 先写标识，再发布完成状态

建议顺序：

1. 接受请求后把 doorbell 改为 `0x5A`；
2. 写 magic、state、version；
3. 清理数据区；
4. 执行探测并逐步写数据；
5. 最后把 state 写为 complete；
6. 最后一步才写 doorbell `0xC3`。

这样主机不会把半初始化记录误判为完成数据。

---

## 7. 保存和恢复 CPU/XRAM 现场

### 7.1 最低 CPU 上下文

8051 插桩常见需要保护：

```text
ACC
B
DPTR (DPL/DPH)
PSW
R0–R7
```

仅保存 `ACC/DPTR` 通常不够。被调用的原厂 worker、算术 helper 和 descriptor builder 都可能使用 `B` 或通用寄存器。

### 7.2 register bank 问题

`R0–R7` 的物理位置由 `PSW.RS1:RS0` 决定。不能在不知道 active bank 的情况下简单切换 PSW 后再假设原寄存器仍安全。

v13 在进入附加扫描前检查：

```asm
MOV  A,PSW
ANL  A,#0x18
JNZ  bypass
```

即只在 active register bank 为 bank0 时附加扫描。随后先把原事务执行完，再保存原事务的 post-call：

```text
ACC/B/DPTR/PSW/R0–R7
```

保存 PSW 后清除 `RS1:RS0`，让插桩明确使用 register bank0，结束后逆序恢复。

### 7.3 栈必须严格平衡

检查方法：

- 对每条路径统计 `PUSH/POP`；
- 检查成功、失败、bypass、提前完成路径；
- 检查 `LCALL/RET` 与 `LJMP` tail 是否匹配；
- 保证任何 `POP PSW` 都发生在恢复依赖 active bank 的寄存器之后或按设计顺序进行；
- 留意原厂中断可能使用同一个内部 RAM 栈。

### 7.4 XRAM scratch 也属于现场

只保存 CPU 寄存器仍可能破坏原始调用者。v13 发现原厂 `0x42/0x47` word-read 使用：

```text
XRAM 0x00C0–0x00C1
```

作为临时目的地址。触发 hook 的原始事务返回后，其调用者可能继续消费这两个字节。如果附加探针立即覆盖它们，原事务就会得到错误结果。

因此 v13 在附加事务前把 `0x00C0/0x00C1` 压栈，附加事务后原样恢复，并单独保留附加事务的布尔返回值。这个修正说明：

> 插桩需要保存的不只是寄存器，还包括原调用约定隐式依赖的共享 XRAM scratch。

---

## 8. 从原厂调用点恢复 SMBus 协议

### 8.1 不要只看 command number

相同命令号在不同传输模式下可能表示：

- byte read；
- word read；
- block read；
- write；
- 厂商协议；
- 完全不同的 descriptor 结构。

因此必须从原厂 raw call site 同时恢复：

```text
目标设备/地址参数
command
R3
R5
R7
descriptor 内容
调用的 worker/gate
返回值与状态字段
```

例如原厂 105 项 telemetry 表用：

```text
R3=A1
R5=08
LCALL common:1B22
```

其中包含 byte 地址 `0x17`，但这**不能证明**它读取标准 SBS word `CycleCount(0x17)`。

v12 真机已证明 OEM `0x43` 使用 word-read 并返回循环次数。其原厂调用点恢复出：

```text
R3=A1
R5=0C
R7=00
```

v13 据此用相同 word-read 模式测试 `0x00–0x1C`。真机结果随后证明这些编号是连续的 OEM byte-register 窗口，不能解释成标准 SBS word；只有原厂已使用的 OEM `0x43` 保持 CycleCount 语义。

### 8.2 当前 descriptor

v13 构造的 descriptor 位于：

```text
XRAM 0x0A6E–0x0A71
```

四字节含义：

```text
+0 command
+1 destination high
+2 destination low
+3 flags
```

然后设置：

```text
R3 = 0xA1
R5 = 0x0C
R7 = 0x00
```

并调用原始 bank0 worker：

```asm
MOV   DPTR,#0x80D2
LCALL common:0x1100
```

### 8.3 “读取 ManufacturerAccess 0x00”与“写子命令”

这两者必须严格区分：

```text
word-read command 0x00
```

只是读取设备在该命令下提供的 word。危险操作通常需要：

```text
write-word command 0x00 + 特定子命令
```

或后续配套读写序列。插桩审核时应检查事务模式和数据方向，不能仅凭命令号判断风险。

---

## 9. 调度：一次只做一小步

### 9.1 为什么不在 doorbell hook 中一次执行 32 条

一次连续发出大量 SMBus 事务会增加：

- 单次主循环阻塞时间；
- SMBus host busy/timeout；
- 与 charger/battery 原厂事务争用；
- 看门狗与其他周期任务延迟；
- 出错后难以定位是哪一条命令造成。

v13 采用“真实窗口搭车”：

1. doorbell hook 只把请求置为 accepted；
2. 等待下一次真实的 bank0 `common:1B22` 事务；
3. 先让原始事务完整执行；
4. 保存原始 post-call 现场；
5. 附加一条只读探测；
6. 恢复原事务 scratch 和 CPU 现场；
7. 未完成时回到 waiting；
8. 32 个真实窗口后写 `C3`。

这并不等于没有时序影响，但比在一个 hook 中爆发执行 32 条事务更温和。

### 9.2 必须防重入

v13 state：

```text
1 = waiting for a stock SMBus window
2 = one injected probe is running
3 = complete
```

进入 worker 前先把 state 改成 2。若执行过程中再次经过 hook，看到 state 2 就 bypass，避免插桩递归或重入。

### 9.3 completion count 比固定时间更可靠

`CACHE_COUNT` 同时作为下一个命令索引和完成数。每次窗口只递增一次，到 `0x20` 才发布完成。

读取工具仍需要 timeout，因为：

- 系统可能暂时没有足够多真实 SMBus 窗口；
- 电池断开或状态机被 gate；
- 原厂调度频率可能随电源状态变化。

超时不应自动解释成“电池不响应”，应先看 doorbell、state 和 count。

---

## 10. 用 Python 生成机器码，而不是手工拼补丁

v13 的 `Code` emitter 提供：

```text
emit()
label()
rel8()
abs_jump()
lcall()
finish()
```

其中 `finish()` 负责：

- 计算 signed rel8 displacement；
- 验证 `-128..127`；
- 填充绝对 16-bit 目标；
- 拒绝重复 label 或越界跳转。

### 10.1 一个安全的 patch emitter 应具备

1. 固定 `EXPECTED_SIZE`；
2. 固定 `EXPECTED_SHA256`；
3. 拒绝 source 与 output 为同一路径；
4. 每个 hook 使用 `require_bytes()`；
5. 关键调用点使用更长 raw context；
6. cave 整段验证预期填充值；
7. 验证 cave 不跨 logical boundary；
8. 生成后打印 source/output SHA；
9. 测试输出只修改白名单区间；
10. 输出写到独立版本目录，不覆盖基线。

示意：

```python
source = source_path.read_bytes()
assert len(source) == EXPECTED_SIZE
assert sha256(source).hexdigest() == EXPECTED_SHA256
require_bytes(source, HOOK_OFFSET, HOOK_ORIGINAL, "hook")
require_bytes(source, CAVE_OFFSET, b"\xFF" * len(cave), "cave")

patched = bytearray(source)
patched[HOOK_OFFSET:HOOK_OFFSET + len(hook)] = hook
patched[CAVE_OFFSET:CAVE_OFFSET + len(cave)] = cave
output_path.write_bytes(patched)
```

不要写成“搜索到类似字节就自动替换所有匹配项”。固件中相同短序列很多，必须使用精确地址、版本哈希和上下文共同约束。

---

## 11. 主机读取工具的安全边界

当前读取工具：

```text
tools/read_sbs_identity_cache.py
```

普通读取只观察 mailbox。请求模式要求显式提供：

```text
--request
--allow-write
--confirm I_UNDERSTAND_I2EC_WRITES
```

这里的 “write” 是通过 I2EC 写 EC XRAM doorbell，不是构造 Smart Battery 写事务。固件是否进一步读写电池，仍由刷入的具体 probe 版本决定，所以工具必须验证 magic/version，不能假设任意固件都实现同一 mailbox。

硬件访问命令需要 sudo，因为底层 `tools/i2ec_rw.py` 访问 `/dev/port`。分析、生成补丁和测试不需要 sudo。

### 11.1 读取工具应输出什么

至少包括：

- raw bytes；
- doorbell 解释；
- magic/version/state；
- success mask；
- completion count；
- 每条命令是否成功；
- 返回 word；
- 控制命令是否成功；
- timeout 时当前处于 A5 还是 5A。

不要只输出最终 value，否则无法区分：

```text
真实值为 0
事务失败留下 0
记录尚未初始化
版本不匹配
```

---

## 12. 验证清单

### 12.1 生成前

- [ ] 输入 `.bin` 长度正确；
- [ ] SHA-256 与指定基线一致；
- [ ] hook 原始 opcode 与完整上下文一致；
- [ ] `LCALL/LJMP` 目标由 raw opcode 解码；
- [ ] fixed common 与 banked 地址已区分；
- [ ] 原厂 SMBus 模式从真实调用点恢复；
- [ ] 没有未知写事务；
- [ ] mailbox 与 scratch 地址经过引用审计；
- [ ] cave 足够且不跨 bank 边界。

### 12.2 生成后静态检查

```bash
python3 -m py_compile \
  firmware_mods/GXxHXxx_21.200/\
cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727/\
patch_sbs_cyclecount_v13_word_scan.py \
  tools/read_sbs_identity_cache.py

pytest -q tests/test_sbs_cyclecount_v13_word_scan_patch.py \
          tests/test_read_sbs_identity_cache.py

git diff --check

sha256sum \
  firmware_mods/GXxHXxx_21.200/\
cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727/\
GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727.bin
```

还应检查：

- [ ] 重新运行 emitter 生成的文件与保存的 `.bin` 完全一致；
- [ ] changed-byte set 是允许范围的子集；
- [ ] cave 内不存在对 patched veneer 的递归调用；
- [ ] 所有成功/失败路径栈平衡；
- [ ] 保存/恢复顺序正确；
- [ ] 共享 XRAM scratch 已恢复；
- [ ] completion 只在最后发布；
- [ ] build 反汇编放在 `build/`，且只作辅助检查。

### 12.3 真机前检查

- [ ] 明确刷入的文件 SHA-256；
- [ ] 保留已知可恢复的基线镜像；
- [ ] 不在无人值守状态进行首次验证；
- [ ] 电池温度、电压、充电电流处于正常范围；
- [ ] 首次只发一个 doorbell 请求，不循环轰炸；
- [ ] 若 EC/键盘/充电状态异常，停止继续请求；
- [ ] 不把保护芯片当作实验安全机制；
- [ ] 不执行任何未知 ManufacturerAccess/OEM 写命令。

本文不自动执行刷写，也不建议把刷写步骤封装进测试脚本。

---

## 13. 常见失败模式与定位

### 13.1 doorbell 保持 `0xA5`

说明：

```text
host 写入可见，但请求 hook 没有接受
```

优先检查：

- 是否刷入了预期 SHA 的固件；
- hook 点是否真实执行；
- main hook 是否被别的状态路径绕开；
- mailbox 地址是否被原厂代码清零；
- hook 的 `LCALL` 是否落在 fixed common 正确地址。

不要立刻换一批随机 XRAM 地址继续猜。

### 13.2 到 `0x5A`，一直不到 `0xC3`

说明请求已接受，但没有完成足够的工作窗口。检查：

- state 是 waiting 还是 running；
- completion count 是否增长；
- active register bank filter 是否一直 bypass；
- 系统是否产生目标 bank0 SMBus 事务；
- 是否出现重入或 worker 卡死；
- timeout 是否短于真实调度周期。

### 13.3 到 `0xC3`，success mask 为零

说明 EC 插桩确实执行完成，但目标事务失败。此时应记录：

- SMBus host status；
- completion/result；
- 原厂控制命令是否成功；
- descriptor、R3/R5/R7；
- 电池 gate 和电源状态；
- 同一命令在原厂路径下是否能成功。

“完成”不等于“读到了有效值”。

### 13.4 原事务行为异常

优先怀疑：

- 没保存 B 或 PSW；
- R0–R7 保存了错误 register bank；
- 栈不平衡；
- `LJMP/LCALL` 语义恢复错误；
- 共享 scratch 被覆盖；
- descriptor 或 worker 正在被其他状态机使用；
- 附加事务太密集。

---

## 14. v13 完整实例

### 14.1 文件

补丁生成器：

```text
firmware_mods/GXxHXxx_21.200/
  cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727/
  patch_sbs_cyclecount_v13_word_scan.py
```

输出固件：

```text
firmware_mods/GXxHXxx_21.200/
  cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727/
  GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-sbs-cyclecount-v13-word-scan-20260727.bin
```

输出 SHA-256：

```text
191d15a931f8fdd2101b589881712abc0266b06da44baec7c34943e96d6d25e2
```

build 辅助反汇编：

```text
build/sbs-cyclecount-v13-word-scan-20260727/
```

### 14.2 hook 与 cave

```text
common:0272 -> common:7E6A   main doorbell acceptance
common:1B22 -> common:7E95   SMBus veneer hook
common:7E6A–7FBA             337-byte fixed-common cave
```

原始 SMBus veneer：

```text
common:1B22 = 90 80 D2 02 11 00
```

真实 worker：

```text
bank0:80D2 through common:1100
```

### 14.3 扫描项目

```text
index 0..28  -> 对 OEM byte-register offset 0x00..0x1C 发起 word-read
index 29     -> OEM 0x43，已知成功控制项，保存返回 word
index 30     -> OEM 0x42，只保存成功状态
index 31     -> OEM 0x47，只保存成功状态
```

所有命令使用：

```text
R3=A1, R5=0C, R7=00
```

### 14.4 v13 特别处理

- 每个真实 bank0 SMBus 窗口只附加一条命令；
- state 2 防重入；
- 不递归调用 patched `common:1B22`；
- 保存 `ACC/B/DPTR/PSW/R0–R7`；
- 保存并恢复原厂 `XRAM 0x00C0–0x00C1` scratch；
- 最后才发布 `state=3` 和 `doorbell=0xC3`；
- 不改写 EC 的 cycle cache 或 published cycle 字段；
- 不发送 Smart Battery 写事务。

### 14.5 真机结果的后验修正

v13 真机完成了全部 32 条只读事务，但 `0x00–0x1C` 的返回满足：

```text
D1(command n) == D0(command n + 1)    28/28
```

同时，原厂 `common:0x632F` 的 105 项表使用 `R5=0x08`（Read Byte），并把这些
command 按相邻字节写入 `XRAM 0x0300...`。真机 dump 与 v13 的 D0 序列逐字节一致。
因此 v13 的正确解释是：

- `0x00–0x1C` 是厂商连续 byte-register offset；
- word-read 只是一次取回当前 byte 和下一个 byte，不能套用标准 SBS 字段名；
- v13 曾输出的 `SBS 0x17=0x4C3C` 是错误解释，应撤销；
- OEM `0x43` 仍返回 `D0=0x3E, D1=0x00`，即 `0x003E=62` 次循环。

原厂 byte 表还给出了交叉验证：

```text
cmd 0x43 -> XRAM 0x0344 = 0x3E
cmd 0x44 -> XRAM 0x0343 = 0x00
```

所以内部缓存 `XRAM 0x0343:0x0344 = 00 3E`，发布时转换为主机字段的
little-endian `3E 00`。循环次数来自电池包内 Gauge/包控制器，EC 负责轮询、缓存和发布。

### 14.6 当前静态验证结果

在 2026-07-27 重新生成并比较：

```text
生成文件与保存的 v13 固件逐字节一致
changed bytes: 341
v13 patch + reader tests: 30 passed, 3 subtests passed
```

这证明 emitter、输出文件和测试约束彼此一致；它不是对所有真机时序的形式化安全证明。

---

## 15. 高风险禁区

在没有 Fuel Gauge 型号、完整协议文档、可恢复手段和独立硬件保护前，不要做以下实验：

- 向 `ManufacturerAccess (0x00)` 写未知子命令；
- 尝试 unseal/full-access/seal 密钥；
- PF clear、FET enable/disable、chemical fuse、lifetime reset；
- ship mode、shutdown、reset；
- Gauge data flash、instruction flash、configuration update；
- 未知 OEM authentication/challenge-response 写序列；
- 对 `0x00–0xFF` 盲目组合 byte/word/block/write 协议扫描；
- 改写 charger 电压/电流命令来“测试电池响应”；
- 在中断服务程序中执行长 SMBus 事务；
- 未保存完整上下文就调用原厂 worker；
- 未确认 bank gate 就跳往 `0x8000–0xFFFF`；
- 让脚本自动刷写或在失败后无限重复 doorbell。

后续如果要发现更多电池字段，优先级应是：

1. 观察原厂 105 项 byte telemetry 表；
2. 追踪原厂 descriptor 的生产者和消费者；
3. 记录已存在命令的返回及用途；
4. 只对已确认的原厂 read 模式做小范围复现；
5. 最后才考虑有数据手册支持的新命令；
6. 未知写事务保持禁止。

---

## 16. 相关材料

- [`battery_aging.md`](battery_aging.md)：循环次数、OEM `0x43`、老化策略和 SMBus worker 调查。
- [`battery_calibration/battery_calibration_investigation_20260721.md`](battery_calibration/battery_calibration_investigation_20260721.md)：容量校正职责边界。
- [`battery_calibration/battery_calibration_trigger_audit_20260721.md`](battery_calibration/battery_calibration_trigger_audit_20260721.md)：有效 bank 控制流与校正触发复核。
- [`../tools/read_sbs_identity_cache.py`](../../tools/read_sbs_identity_cache.py)：mailbox 读取和 doorbell 请求工具。
- `tests/test_sbs_cyclecount_v13_word_scan_patch.py`（已随测试目录删除）：v13 二进制、控制流、上下文和 XRAM 约束测试。
- [`../tools/extract_bank_entries.py`](../../tools/extract_bank_entries.py)：从 common wrapper 恢复 bank dispatch 目标的辅助工具。

## 17. 最终原则

一个可信的 EC 插桩不只是“能跑起来”，而应同时满足：

```text
目标由 raw opcode 证明
bank 映射明确
修改范围最小
输入版本固定
上下文完整恢复
共享 scratch 完整恢复
协议方向明确为只读
工作量分散调度
mailbox 能区分未执行/进行中/已完成/事务失败
输出可复现、可测试、可回退
```

对于电池相关固件，最重要的安全边界是：**先证明数据流，再增加观测；先复用原厂读路径，再考虑主动事务；永远不要把未知写命令当作探测手段。**
