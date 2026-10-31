# ai-pico-firmware-reverse-skill

一套面向 **RP2040 / RP2350（Raspberry Pi Pico / Pico 2）固件逆向重建**的工作流技能包，供 AI Agent 在 WorkBuddy / Claude Code 等环境中加载使用。

它覆盖从 **UF2 解析 → 反编译基准建立 → 源码重建 → 双目标构建 → 真机契约标定 → 上板刷机验证**的完整链路。内容来自一次完整的实战项目（把一块跑着闭源固件 `xTools_uart 3.3.1` 的 RP2350 板子，重建成可编译源码工程并刷回验证），里面沉淀的不是通用知识，而是**踩过的坑**：哪些操作会让真机挂死、哪些 CMake 变量必须在特定时机设置、怎么判断"刷进去的到底是不是我的固件"。

---

## 什么时候用它

- 拿到一份 Pico / Pico 2 的 UF2 固件，要逆向分析或重建源码
- 重建工程需要同时支持 RP2350 与 RP2040 双目标
- 真机跑着闭源固件，要通过网络 / USB 标定它的行为契约
- 构建报 `PICO_PLATFORM incompatible`、flash `OVERFLOW`、符号冲突、SDK 链接脚本不生效
- 需要进 BOOTSEL 刷机，或者刷完发现"起来的还是原厂固件"

触发词：Pico 固件逆向、UF2 解析、RP2350 重建工程、真机对拍、pico-sdk CMake 排错、BOOTSEL 刷机、字符串对拍、符号恢复。

---

## 目录结构

```
ai-pico-firmware-reverse-skill/
├── SKILL.md                    工作流主体：六阶段流程 + 六条硬性规则
├── references/
│   ├── device-probe.md         真机探测：接口定位、挂死黑名单、MSC / CDC 用法
│   ├── http-contract.md        REST 契约标定方法与实测案例表
│   ├── build-cmake.md          双目标 CMake 配置 + VSCode 环境 + 报错对照表
│   ├── uf2-flash.md            UF2 块格式 / flags / family、BOOTSEL、刷机排查
│   ├── re-baseline.md          反编译对拍的三个陷阱、LVGL v8→v9 重命名表
│   └── porting-pitfalls.md     RP2350 ↔ RP2040 移植与第三方组件接入踩坑
└── scripts/
    ├── uf2_tool.py             UF2 解析 / 拼 flat 镜像 / bin→UF2 打包
    └── probe_device.py         设备只读探测 + 契约断言（内置黑名单）
```

`SKILL.md` 是入口，Agent 加载后按阶段逐步查阅对应的 `references/`。两个脚本只依赖 Python 3 标准库，无需安装任何包。

---

## 工作流六阶段

| 阶段 | 做什么 | 关键约束 |
|---|---|---|
| 1. 解析固件原件 | 用 `uf2_tool.py` 看段布局、family、分区表 | **先看 flags 再读 family**：只有 flags 含 `0x2000` 时 family 才在偏移 0x1C，否则在块尾 0x1F8 |
| 2. 建立反编译基准 | 符号表 / 命令表 / 字符串导出成机器可读 json | 对拍脚本有两个必踩的坑，见下 |
| 3. 源码重建 | 按基准逐模块重建 | **每完成一个模块就对拍一次**，不要全部写完再验 |
| 4. 双目标构建 | RP2350 / RP2040 各一个构建目录 | `PICO_PLATFORM`、`PICO_FLASH_SIZE_BYTES` 必须在 `pico_sdk_init()` 前设置 |
| 5. 真机契约标定 | 只读探测，把实测响应固化成断言 | **先读黑名单**——探测本身会把设备搞挂 |
| 6. 刷机与验证 | 备份原厂 → 进 BOOTSEL → 刷入 → 验证 | 判定固件归属**看字符串不看行为** |

---

## 六条硬性规则

这六条是本项目付出代价换来的，优先级高于一切临场推理：

1. **真机探测有黑名单**。`/api/reset`、`/api/login`、`/api/logout`、`/api/flash`、`POST /api/setup?type=uart&oper=2`、不带 `Accept-Encoding: gzip` 请求 `/assets/*.js`——实测全部会让设备挂死（表现为 USB 掉线）。**只读探测，绝不碰写操作。**
2. **判定固件归属看字符串，不看行为**。CDC 串口打印、HTTP 响应里的独有字符串才是证据：在重建的 ELF/BIN 里搜不到该串，跑的一定还是原厂固件。（本项目曾因"行为看起来变了"误判成功刷机，实际没有。）
3. **一个平台一个构建目录**（`build/` 与 `build-rp2040/`），且 `PICO_PLATFORM` 按板型 `FORCE` 锁定——SDK 默认是 rp2040，缓存残留会直接 FATAL。
4. **刷机前先备份原厂 UF2**，并验证备份可读（MSC 盘上通常就有一份）。刷机会清空整片 flash。
5. **真机 HTTP 对连续快速请求会超时**。断言脚本每条请求后 sleep 0.25s 并重试一次；状态依赖的端点（如 I2C 扫描）必须先设置前置状态再断言。
6. **对拍报"缺失"先怀疑脚本**。字符串折叠（GCC `-fmerge-constants`）、短串过滤（`strings` 默认丢弃 2 字节串）、库版本重命名造成的假缺失，远比真的漏实现常见。

---

## 脚本用法

### uf2_tool.py（picotool 不可用时的替代）

```bash
# 看段布局 / family / flags / flash 容量
python scripts/uf2_tool.py info official.uf2

# 从基址 0x10000000 拼出连续 flat 镜像（可指定 --base）
python scripts/uf2_tool.py flat official.uf2 out.bin

# 把 bin 打成 UF2（family 可用 rp2040 / rp2350 / rp2350-riscv / rp2350-abs）
python scripts/uf2_tool.py pack app.bin app.uf2 rp2350
```

`info` 的输出示例（真实项目中的官方固件）：

```
file      : official.uf2 (6109696 B, 976 blocks)
families  : 0xe48bff57 x1, 0xe48bff59 x975
segments  :
  0x10000000 .. 0x1003ceff  (  249600 B,  975 blocks)  flags=0x2000(FAMILY_ID) fam=0xe48bff59
  0x10ffff00 .. 0x10ffffff  (     256 B,    1 blocks)  flags=0xa000(FAMILY_ID|EXT_FLAGS) fam=0xe48bff57
flash end : 0x10ffff00  -> >= 16 MB
```

family 对照：`0xe48bff56` RP2040、`0xe48bff59` RP2350 ARM-S、`0xe48bff5a` RP2350 RISC-V、`0xe48bff57` ABSOLUTE（常见于分区表块）。

### probe_device.py（只读探测）

```bash
# 基础探测：枚举 HTTP 端点、读版本信息
python scripts/probe_device.py --host 192.168.33.1

# 用契约文件跑断言（JSON case 文件）
python scripts/probe_device.py --host 192.168.33.1 --contract contract.json --gap 0.25
```

脚本内置黑名单，遇到 `/api/reset` 这类危险端点会直接拒绝发送。

---

## 参考战绩

这套流程在 RP2350 项目 `xTools_uart 3.3.1` 上完整跑通：

- 双目标编译通过：RP2350 固件 2,899,572 B（4 MB flash 的 69.1%，静态 RAM 163,628 B）；RP2040 1,344,112 B（2 MB 的 64.1%，RAM 130,012 B）
- 反编译基准对拍 100%：shell 命令 75/75、HTTP URI 34/34、externs 340/340、LVGL 类 49/49、JS 成员 106/106
- 真机 HTTP 契约标定 29 条断言全通过，据此修正了 5 处重建偏差
- 内嵌前端资源与真机 gzip 响应 sha256 逐字节一致；FAT 出厂文件与 MSC 盘 20/20 逐字节一致

---

## 环境要求

- Python 3（仅标准库，无第三方依赖）
- 目标侧：pico-sdk 2.x、arm-none-eabi-gcc、CMake + Ninja
- 可选：真机通过 USB 接入（RNDIS / MSC / CMSIS-DAP / CDC）

---

## 安装

把本仓库放到用户级 skills 目录即可，目录名必须与 `SKILL.md` 里的 `name` 字段一致：

```
C:\Users\<用户名>\.workbuddy\skills\ai-pico-firmware-reverse-skill\
```

macOS / Linux 对应 `~/.workbuddy/skills/ai-pico-firmware-reverse-skill/`。

---

## License

MIT
