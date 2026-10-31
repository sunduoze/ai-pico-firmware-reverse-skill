# UF2 解析、进 BOOTSEL 与刷机验证

## UF2 块格式（512 字节）

| 偏移 | 内容 |
|---|---|
| 0x00 | magic0 `0x0A324655` ("UF2\n") |
| 0x04 | magic1 `0x9E5D5157` |
| 0x08 | flags（见下） |
| 0x0C | target addr |
| 0x10 | payload size（通常 256） |
| 0x14 | block no |
| 0x18 | num blocks |
| 0x1C | family ID（**仅当 flags 含 `0x2000` 时有效**，否则在块尾 0x1F8） |
| 0x20 | payload |
| 末尾 | trailing magic `0x0AB16F30` |

### flags（`boot/uf2.h`，pico-sdk）

| 值 | 含义 |
|---|---|
| `0x0001` | NOT_MAIN_FLASH |
| `0x1000` | **FILE_CONTAINER**（块内容是封装/压缩形态，不是展开镜像） |
| `0x2000` | **FAMILY_ID_PRESENT**（family ID 在 0x1C） |
| `0x4000` | MD5_PRESENT |
| `0x8000` | EXTENSION_FLAGS_PRESENT |

**解析 family 必须先看 flags**：没有 `0x2000` 时 family 在块末尾偏移 0x1F8，
直接读 0x1C 会读到垃圾。同一个文件里不同段可以有不同 flags 和 family。

## family ID 表

| ID | 含义 |
|---|---|
| `0xE48BFF56` | RP2040 |
| `0xE48BFF57` | ABSOLUTE（按块内绝对地址烧写） |
| `0xE48BFF58` | DATA |
| `0xE48BFF59` | RP2350 ARM-S（SDK 标准封装） |
| `0xE48BFF5A` | RP2350 RISC-V |
| `0xE48BFF5B` | RP2350 ARM-NS |

**不要凭文件头第一个块猜整个文件的 family。** 实例（xTools 3.3.1 官方 UF2）：

```
0x10000000 .. 0x1003CE00  (249,600 B, 975 blocks)  flags=0x2000(FAMILY_ID)          fam=0xe48bff59
0x10FFFF00 .. 0x10FFFFFF  (    256 B,   1 block )  flags=0xa000(FAMILY_ID|EXT_FLAGS) fam=0xe48bff57
```

主镜像用的是 SDK 标准的 RP2350 ARM-S，**只有末尾那个 256 字节的分区表块用 ABSOLUTE**（内容全 `0xEF` 空槽）。
只读文件头容易得出"官方用 ABSOLUTE"的错误结论——本项目确实这样误判过一次，
还据此把"刷机没生效"归因到 family，后来证明是错的。

## 解析步骤

```bash
python scripts/uf2_tool.py info official.uf2      # 段列表 + flags + family + block 数
python scripts/uf2_tool.py flat official.uf2 out.bin   # 从 0x10000000 起拼连续镜像
```

看段结构能读出很多信息。例如某份 RP2350 出货镜像只有两段：

```
0x10000000 .. 0x1003CE00  (249,600 B)  flags=0x2000   <- 主镜像（file container）
0x10FFFF00 .. 0x10FFFFFF  (256 B)      flags=0xa000   <- 分区表（内容全 0xEF 空槽）
```

注意主镜像只有 249,600 B —— 它是 **file container**（压缩/封装形态），
不是展开后的线性镜像；而自己编译出来的 `.bin` 是展开的 2.9 MB。二者不能直接按大小比较。

## 进 BOOTSEL 的几种方式

| 方式 | 可靠性 | 说明 |
|---|---|---|
| **按住 BOOTSEL 上电**（或按住 BOOTSEL 点 RUN） | ✅ 可靠 | 最终手段，需要人动手 |
| CDC 串口设 **1200 波特率**再打开 | ⚠️ 部分固件有效 | 依赖固件实现 "reset via baudrate"；实测时灵时不灵 |
| HTTP `/api/reset?type=uf2` | ❌ 多数只做普通重启 | 先试无害，别指望 |
| shell `reboot` 命令 | ❌ 普通重启 | 同上 |

1200 波特率技巧（PowerShell）：

```powershell
$p = New-Object System.IO.Ports.SerialPort "COM4",1200,None,8,one
$p.DtrEnable = $false; $p.Open(); Start-Sleep -Milliseconds 600; $p.Close()
```

成功标志：卷标变成 `RP2350` / `RPI-RP2`，出现新的可移动盘。

## 刷机前必做

1. **备份原厂固件**。MSC 盘上常常就躺着一份（如 `xTools_rp2350.uf2_3.3.1.uf2`），
   复制到工程目录（如 `dl/official_xxx.uf2`）并核对 sha256 与大小。
2. 确认备份可读（能解析出 UF2 头、block 数合理）。
3. 想清楚盘上的 FAT 卷在哪：若在**外部 SPI flash**，刷内部 flash 不会清空它；
   若在内部 flash，刷机即清空——备份就是唯一的后悔药。

## 刷机流程

1. 进 BOOTSEL（盘符出现）
2. 把 UF2 复制到该盘（用 `cp`，不要用带缓存的写方式；写完后等盘自动消失）
3. 等设备重启、重新枚举（轮询 USB/盘符/HTTP，最多等 60–90 秒）
4. 跑契约断言

## 判定"起来的是不是我的固件" —— 必须靠字符串证据

**不要靠行为相似性判断。** 例如"某个端点从 200 变 500"、"事件时间戳归零"
看起来像新固件，实际可能只是重启导致的状态复位。

正确做法：找一个**只可能存在于其中一份固件**的字符串，然后：

```python
print(b'Web Busy' in open('my_firmware.bin','rb').read())        # 重建的
print(b'Web Busy' in open('official_flat.bin','rb').read())      # 原厂的
```

可以用 CDC 串口输出里的提示串、HTTP 响应里的特征串、shell 帮助文本。
**在重建 ELF/BIN 里搜不到、而原厂镜像里有的串，一旦设备上打印出来 → 跑的还是原厂固件。**

注意：如果原厂 UF2 是 file container，直接搜 UF2 文件可能搜不到（未展开）。
要先 `flat` 出线性镜像再搜。

## 刷机失效排查清单

按概率排序（本项目实际踩到的顺序）：

1. **先确认到底有没有生效**——用固件独有字符串判定，别靠行为相似（见下节）
2. 镜像形态：官方主镜像可能只有几百 KB（file container / 分段），
   自己编译出的是展开后的几 MB；两者大小不可比，但**不代表格式错**
3. 分区表 / OTP 启动配置：`0x10FFFF00` 那个 256 字节块决定 ROM 从哪加载；
   线性镜像不带分区表时，ROM 可能仍按旧配置启动 → 需要 `picotool load` 处理
4. family ID（按 flags 正确解析后比对；主镜像通常是 `0xE48BFF59`）
5. 基址是否正确（应用镜像 `0x10000000`）
6. 启动头：前两个字应为初始 SP 与 Reset 向量（与原厂镜像对比；
   例如两者 SP 同为 `0x20082000`、Reset 向量差几字节属正常）

## 回退

按住 BOOTSEL 上电，把备份的原厂 UF2 拖进盘里即可。
