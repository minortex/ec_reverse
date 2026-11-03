# EC 刷写与 I2EC 读取工具（2026-11-03）

本仓库提供两个直接访问 EC 硬件的工具：`tools/ec-flash/`（C，SPI Flash 整片读写）和
`tools/i2ec_rw.py`（Python，IT557x 专用 I2EC 端口）。二者都与 kmod 的
`/dev/mechrevo-ec` 通道并存，能力不同，按场景选用。

## 1. 通道对比

| 工具 | 路径 | 访问方式 | 可访问范围 | 权限 |
|---|---|---|---|---|
| `mfc` + kmod | `/dev/mechrevo-ec` | ioctl | H2RAM 4 KiB 窗口（`0x0000–0x0FFF`） | root 或 wheel |
| `tools/ec_rw.py` | `/dev/mem` 或 ACPI | MMIO/ECRR | 同上，H2RAM 开窗部分 | sudo |
| `tools/i2ec_rw.py` | `/dev/port`，端口 `0x681–0x683` | I2EC 专用通道 | 完整 16 位 EC memory（含 H2RAM 看不见的 `0x0800–0x0BFF`：stress、charger queue、SMBus 结果） | sudo |
| `ec-flash` | `/dev/port` + `iopl` | SPI Flash 协议 | 整片 Flash（本机 1 MiB，官方有效区 256 KiB） | sudo |

I2EC 通道由原厂固件初始化表配置：`SPCTRL1` 最终为 `0xCB`（read-write）或刷入
只读补丁后的 `0xCA`；端口译码已由 `CODE:0xDF47–0xDF59` 设置，无需改 H2RAM 窗口。
I2EC 手册保证读取不触发 read-clear，但动态外设值仍可能在逐字节读取之间变化。

## 2. ec-flash

### 构建

```bash
make -C tools/ec-flash    # 产出 build/ec-flash
build/ec-flash --help
```

只依赖 libc 与 `sys/io.h`，编译告警等级 `-Wall -Wextra -Wpedantic`。
Arch 打包：

```bash
cd tools/ec-flash && makepkg -f && sudo pacman -U ./ec-flash-*.pkg.tar.zst
```

包装 `/usr/bin/ec-flash` 与 `/usr/lib/systemd/system-shutdown/ec-flash-reset`。

### 命令

| 命令 | 作用 | 硬件行为 |
|---|---|---|
| `--id` | 读 JEDEC ID | 进入 follow/ISP 模式后发 SPI 命令 |
| `--dump FILE` | 双读 dump，不一致即失败 | 连续 SPI 读，原子落盘 + `fsync`/`syncfs` |
| `--verify FILE` | 双读并与参考文件逐字节比较 | 同上 |
| `--write FILE` | 编程校验后写入 | 清状态寄存器保护 → 16×`0x20` 4 KiB 擦除 → 校验全 `FF` → `0x02` 256 字节页编程 → 读回比较 |
| `--reset-only` | 仅由 shutdown helper 调用的内部复位 | 发 EC 复位序列 |
| `--address/--length/--timeout/--single-read` | 偏移、长度、超时、关闭第二次读 | — |

默认范围为完整 256 KiB（`0`–`0x3ffff`）。写模式要求 64 KiB 对齐的地址与文件大小，
未改动的块跳过；EFI 的 SST/未知与旧 MXIC 厂商分支被拒绝而不是猜测。每次 I/O 等待
都有超时，SIGINT/SIGTERM 触发清理，退出时重新启用 KBC。

### 关机复位挂钩

写成功只创建一次性标记 `/run/ec-flash-reset-on-shutdown`（重启即失效），不立即复位。
`ec-flash-system-shutdown` 在 `poweroff`/`halt`/`reboot` 时消费该标记并调用内部
`--reset-only`；忽略 `kexec`。可手动解除：`sudo rm -f /run/ec-flash-reset-on-shutdown`。
按 `man systemd-shutdown`，该目录下的程序在服务停止、多数文件系统卸载后、最终电源
动作前并行执行且有超时，因此这是 best-effort 的迟滞复位，不是严格有序的断电步骤。

### 风险

- **刷写有变砖风险**：断电、写错 bank、破坏校验和或 PD 电源策略都可能导致无法开机、
  无法充电或只能外接编程器恢复。刷前保留两份一致整片 dump 与恢复路径。
- 写模式在权限检查通过后才广播默认关机复位行为并等待 3 秒，用于让操作者松开键盘——
  因为随后 KBC 会被禁用。不要在该窗口内按键。
- 本仓库不包含、也不会自动执行任何硬件命令。

## 3. tools/i2ec_rw.py

```bash
sudo uv run python tools/i2ec_rw.py read 0x200d          # 单字节
sudo uv run python tools/i2ec_rw.py read 0x0439 0x0438   # 两字节组 word（高字节地址在前）
sudo uv run python tools/i2ec_rw.py dump 0x09c7 4        # 区间 dump
sudo uv run python tools/i2ec_rw.py write 0x0340 0x14    # 写单字节
```

| 子命令 | 行为 |
|---|---|
| `read ADDR [LOW_ADDR]` | 读一字节；给两个地址时按 `(hi << 8) \| lo` 组 word 并打印十进制 |
| `dump START [COUNT]` | 连续读，默认 16 字节，范围必须在 `0x0000–0xFFFF` 内 |
| `write ADDR VALUE` | 写前后各读一次并打印变化 |

`--base-port`（默认 `0x680`）与 `--device`（默认 `/dev/port`）对所有子命令有效。

> [!WARNING]
> 当前版本的 `write` **不再要求** `--allow-write` / `--confirm I_UNDERSTAND_I2EC_WRITES`
> 确认（这两个参数仍被接受但不校验），并可用 `--force` 跳过 `SPCTRL1` 读写模式检查。
> I2EC 可写不等于安全：`0x0800–0x0BFF`、外设寄存器和 firmware-owned 状态字都可能
> 被写入破坏。只读调查请只用 `read`/`dump`。
>
> 一次只运行一个 I2EC 客户端：高地址、低地址、数据三个周期是一次逻辑事务，工具锁
> 无法协调其他 `outb` 工具。

## 4. 典型流程

1. 用 `tools/i2ec_rw.py dump 0x09c7 4` 等只读命令确认通道与隐藏状态
   （`stress = (EC[0x09C9] << 8) | EC[0x09CA]`）。
2. 用 `tools/patch_ec_flexicharge_v22.py` 生成改版镜像（见
   [`ec-firmware-flexicharge-v2.2-20260729.md`](../ec-firmware-flexicharge-v2.2-20260729.md)）。
3. 用 `build/ec-flash --dump dumps/before.bin` 留两份一致基线。
4. `sudo build/ec-flash --write patched.bin`，成功后按提示关机，由 helper 触发热复位。
5. 重新起来后先用 `tools/i2ec_rw.py read 0x200d` 与 `mfc bat status` 交叉验证。

## 5. 参考

- 生成器：`tools/patch_ec_flexicharge_v22.py`、`tools/ec-flash/Makefile`、`tools/ec-flash/PKGBUILD`
- 寄存器表：`docs/ec-register-map.md`
- 补丁说明：[`ec-firmware-flexicharge-v2.2-20260729.md`](../ec-firmware-flexicharge-v2.2-20260729.md)
- 上游调查（协议、bank 映射、EFI 控制流）：ec_reverse 仓库 `docs/` 与
  `docs/firmware_mods/GXxHXxx_21.200/`
