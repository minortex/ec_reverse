# Mode-only 充电电压实验固件（2026-07-23）

## 修改范围

该版本只修改官方 BIN 的 `bank1:D303` 分档选择逻辑，保留 D1D1 的所有 gate、基准读取、
电芯数乘法、16 位减法和 `0x0522:0x0523` 写回。原有 cycle、stress、auxiliary 五级比较
被旁路，只保留 `0x07A6[5:4]` 模式最低档：

| 模式 | `0x07A6[5:4]` | 每电芯降额 |
|---|---:|---:|
| Normal | `0x00` | 0 mV |
| Balanced | `0x10` | 100 mV |
| Health | `0x20` | 200 mV |

`D300` 原有的低基准保护跳转未修改。补丁只覆盖 `D303-D31D`，随后跳到原公共算术尾部
`D3EC`；不存在其它固件字节变化。

## 离线身份

- 输入：官方 `samples/GXxHXxx_21.200`
- 输入 SHA-256：`34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2`
- 生成器：`patch_mode_only_voltage.py`
- 产物：`GXxHXxx_21.200-mode-only-voltage-20260723.bin`

该镜像只完成静态构建和算术路径验证，尚未刷写。实机验证标准是：4S、17600 mV 基准下，
Normal 稳定为 17600，Balanced 为 17200，Health 为 16800；`0x0A54=0x1B` 不再改变
Normal 结果。
