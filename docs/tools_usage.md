# 工具链使用说明

## 概述

本仓库提供一套 EC 固件逆向工具链，涵盖固件分析、拆分、反汇编注释、Ghidra 伪 C 导出、函数符号迁移等功能。

## 环境要求

| 工具 | 依赖 |
|---|---|
| `firmware_tool.py` | Python 3 标准库 |
| `annotate_disassembly.py` | Python 3 |
| `extract_bank_entries.py` | Python 3 |
| `summarize_pseudoc.py` | Python 3 |
| `translate_function_symbols.py` | Python 3 |
| `export_readable_ec.sh` | Ghidra（`/opt/ghidra/support/analyzeHeadless`） |

所有 Python 工具仅需标准库，无需额外安装。

---

## 1. 固件分析 `firmware_tool.py`

多功能工具：分析、拆分、对比固件。

### analyze — 分析固件

```sh
python3 tools/firmware_tool.py analyze <镜像文件> -o <输出.json>
```

输出包含：
- 整体和 64 KiB 块的 SHA-256
- 每块的非 FF 字节数和最后非 FF 偏移
- 入口向量（检查是否以 8051 LJMP 开头）
- 连续 FF 区间（默认 ≥256 字节）
- 可打印 ASCII 字符串（默认 ≥6 字符）
- 文件总字节和校验和

示例：

```sh
python3 tools/firmware_tool.py analyze samples/GXxHXxx_21.200 -o build/manifest.json
```

### split — 拆分固件

将固件按 64 KiB 分割为独立块，同时输出每块的分析清单：

```sh
python3 tools/firmware_tool.py split samples/GXxHXxx_21.200 build/blocks
```

生成 `build/blocks/block0.bin`–`block3.bin` 及 `manifest.json`。

### diff — 对比固件

逐字节比较两个镜像，输出差异簇（含块归属和十六进制预览）：

```sh
python3 tools/firmware_tool.py diff old.bin new.bin -o diff.json
```

---

## 2. Bank 入口提取 `extract_bank_entries.py`

从 common bank（bank0）的 Keil C51 跳板表中恢复各 bank 的函数入口地址。

```sh
# 输出完整 JSON 报告
python3 tools/extract_bank_entries.py samples/disasm/main-bank0.bin -o build/bank-entries.json

# 只输出 bank1 入口地址（逗号分隔），供 export_readable_ec.sh 使用
python3 tools/extract_bank_entries.py samples/disasm/main-bank0.bin --entries bank1

# 输出 bank2 入口地址
python3 tools/extract_bank_entries.py samples/disasm/main-bank0.bin --entries bank2
```

识别模式：`MOV DPTR,#target` + `LJMP 0x11xx`，其中 `xx` 为：
- `0x00` → bank0
- `0x14` → bank1
- `0x28` → bank2

---

## 3. 反汇编注释 `annotate_disassembly.py`

在 disasm51 输出的 `.d52` 文件中标注已知 EC/XRAM 寄存器引用。

```sh
python3 tools/annotate_disassembly.py <输入.d52> -o <输出.d52> \
  --xrefs <交叉引用.md> \
  --registers <寄存器表.tsv> \
  --symbol-bias <偏移>
```

参数：
- `--registers`：默认 `tools/ec_registers.tsv`
- `--symbol-bias`：默认 `1`（修正 disasm51 `org 0-1h` 的符号偏移）
- `--xrefs`：可选，生成 Markdown 交叉引用表

标注内容：
- 识别 `MOV DPTR,#address` 常量加载
- 匹配寄存器表，追加 `EC[0xXXXX] ec_名称; 操作类型; 含义`
- 操作类型自动判定：read / write / read-modify-write / address passed/computed

示例：

```sh
python3 tools/annotate_disassembly.py samples/disasm/main-bank1.d52 \
  -o build/main-bank1.annotated.d52 \
  --xrefs build/main-bank1-xrefs.md
```

bank1 可标注约 691 个常量寄存器引用。

---

## 4. Ghidra 伪 C 导出 `export_readable_ec.sh`

使用 Ghidra 的 8051 反编译器生成伪 C 代码，并安装 EC/XRAM 符号名。

```sh
tools/export_readable_ec.sh <64-KiB-bank.bin> <输出目录> [入口地址,...] [函数符号.tsv]
```

参数：
- `$1`：64 KiB bank 二进制文件
- `$2`：输出目录（自动创建）
- `$3`：逗号分隔的入口地址（可省略，传 `-` 表示无额外入口）
- `$4`：函数符号 TSV 文件（默认 `tools/ec_functions.tsv`）

工作流程：
1. 用 Ghidra headless 导入二进制（8051:BE:16:default）
2. 安装 `ec_registers.tsv` 中的 XRAM 符号
3. 安装入口向量 / 指定入口
4. 递归发现被调用函数
5. 安装语义函数名
6. 导出伪 C 和寄存器索引

### bank0（有复位向量，使用专用符号表）

```sh
tools/export_readable_ec.sh samples/disasm/main-bank0.bin \
  build/readable-main0 - tools/ec_functions-main0.tsv
```

### bank1（需要入口地址）

```sh
entries=$(python3 tools/extract_bank_entries.py \
  samples/disasm/main-bank0.bin --entries bank1)
tools/export_readable_ec.sh samples/disasm/main-bank1.bin \
  build/readable-main1 "$entries" tools/ec_functions.tsv
```

输出：
- `<bank>.c`：伪 C 代码（所有 `ec_` 前缀符号指向 XRAM，非 CODE 地址）
- `ec-register-index.md`：寄存器索引，含每个寄存器的静态代码引用位置

---

## 5. 语义索引生成 `summarize_pseudoc.py`

从 Ghidra 伪 C 中提取直接引用已命名 EC/XRAM 寄存器的函数，按领域分类。

```sh
python3 tools/summarize_pseudoc.py <伪C文件.c> -o <输出.md>
```

分类领域：
- `fan` — 包含 fan
- `battery` — 包含 battery/charge
- `power` — 包含 power/tcc/vrm
- `lighting/input` — 包含 backlight/rgb/lightbar/command_trigger
- `platform` — 包含 bios/support/project/system_id/ap_oem
- `other` — 以上均不匹配

示例：

```sh
python3 tools/summarize_pseudoc.py build/readable-main1/main-bank1.bin.c \
  -o build/readable-main1/semantic-index.md
```

---

## 6. 函数符号翻译 `translate_function_symbols.py`

通过精确机器码匹配，将已知函数符号从一个固件版本迁移到另一个。

```sh
python3 tools/translate_function_symbols.py <参考镜像> <目标镜像> \
  -s <符号表.tsv> -o <输出.tsv>
```

匹配策略：
1. 从参考镜像中取函数地址 → `RET`（`0x22`）间的字节作为签名
2. 在目标镜像中查找签名唯一匹配
3. 0/2+ 匹配则拒绝，1 匹配则翻译

示例（bank1→bank0 翻译通常失败，因为同地址对应不同函数）：

```sh
python3 tools/translate_function_symbols.py \
  samples/disasm/main-bank1.bin samples/disasm/main-bank0.bin \
  -o build/translated-functions-b1-to-b0.tsv
```

---

## 7. 完整工作流示例

以下是从原始固件到伪 C 的完整流程：

```sh
# 0. 准备
mkdir -p build

# 1. 分析固件
python3 tools/firmware_tool.py analyze samples/GXxHXxx_21.200 -o build/manifest.json

# 2. 拆分固件
python3 tools/firmware_tool.py split samples/GXxHXxx_21.200 build/blocks

# 3. 提取 bank 入口
python3 tools/extract_bank_entries.py samples/disasm/main-bank0.bin -o build/bank-entries.json

# 4. 注释反汇编
python3 tools/annotate_disassembly.py samples/disasm/main-bank0.d52 \
  -o build/main-bank0.annotated.d52 --xrefs build/main-bank0-xrefs.md
python3 tools/annotate_disassembly.py samples/disasm/main-bank1.d52 \
  -o build/main-bank1.annotated.d52 --xrefs build/main-bank1-xrefs.md

# 5. Ghidra 伪 C（需要 Ghidra）
tools/export_readable_ec.sh samples/disasm/main-bank0.bin \
  build/readable-main0 - tools/ec_functions-main0.tsv
entries=$(python3 tools/extract_bank_entries.py \
  samples/disasm/main-bank0.bin --entries bank1)
tools/export_readable_ec.sh samples/disasm/main-bank1.bin \
  build/readable-main1 "$entries" tools/ec_functions.tsv

# 6. 语义索引
python3 tools/summarize_pseudoc.py build/readable-main0/main-bank0.bin.c \
  -o build/readable-main0/semantic-index.md
python3 tools/summarize_pseudoc.py build/readable-main1/main-bank1.bin.c \
  -o build/readable-main1/semantic-index.md
```

---

## 参考文件

| 文件 | 用途 |
|---|---|
| `tools/ec_registers.tsv` | 官方 GCU Service 恢复的 EC/XRAM 寄存器词汇表 |
| `tools/ec_functions.tsv` | bank1 语义函数名（高置信度） |
| `tools/ec_functions-main0.tsv` | bank0/common 特有函数名 |
| `tools/ghidra/ExportReadableEc.java` | Ghidra 导出脚本（安装符号、反编译、导出） |
