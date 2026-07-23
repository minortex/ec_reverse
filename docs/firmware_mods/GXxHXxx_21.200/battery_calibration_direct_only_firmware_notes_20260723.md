# 电池容量校准直提 Only 实验固件说明（2026-07-23）

## 版本目的

本版本直接以官方 `samples/GXxHXxx_21.200` 为基线，只跳过容量校准状态 5 的单轮
`+/-300 mAh` clamp。它不包含 INT1 bank2-R7 修复，主 EC block0 与官方固件逐字节一致。

## 产物与生成器

```text
目录：firmware_mods/GXxHXxx_21.200/cal-direct-only-exp-20260723/
生成器：patch_battery_calibration_direct_official.py
产物：GXxHXxx_21.200-cal-direct-only-exp-20260723.bin
直接输入 SHA-256：34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2
产物 SHA-256：64f3b37eab0d59f44b6777861f63b78db85ddfb3efc38694c31e952a287da78f
产物 byte sum：0x2124DC9
产物 sum8：0xC9
产物 sum16：0x4DC9
```

生成命令：

```sh
python3 firmware_mods/GXxHXxx_21.200/cal-direct-only-exp-20260723/patch_battery_calibration_direct_official.py \
  samples/GXxHXxx_21.200 \
  firmware_mods/GXxHXxx_21.200/cal-direct-only-exp-20260723/GXxHXxx_21.200-cal-direct-only-exp-20260723.bin
```

## 精确修改

```text
完整镜像物理偏移：0x14974--0x14976（主 EC block1）
原字节：90 03 9B    MOV  DPTR,#039B
新字节：02 C9 8E    LJMP bank1:C98E
```

`02 C9 8E` 从原始 opcode 直接解码为 `LJMP 0xC98E`。调用点和目标均在 bank1 的
`0x8000--0xFFFF` banked window，执行期间 bank 选择不变，因此目标是 `bank1:C98E`，
不是 fixed common 或其他 bank 的同地址代码。

修改后状态 5 保留候选计算、factor 写入、乘 100、pending 发布和状态迁移，只跳过读取
`0x039B/0x039C` 并夹逼候选值的代码。

## 与其他版本的边界

- 相对官方固件应且只应有上述 3 字节差异；
- 主 EC block0 的 SHA-256 必须与官方固件相同；
- 不包含 `0x0566`、`0x05B2`、`0x7E6A` 的 INT1 修改；
- 生成器只接受官方 SHA-256，不接受 INT1-R7 或任何其他改版固件。

该版本尚未实机验证，而且没有 DesignCapacity 绝对范围保护。取消 clamp 后，单次异常校准
可能造成主机可见 FCC 大幅跳变；同时，因为它不含 INT1 修复，原先观察到的充电电压异常
风险也没有被该版本处理。
