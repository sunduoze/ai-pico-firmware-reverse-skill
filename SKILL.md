---
name: ai-pico-firmware-reverse-skill
description: RP2040/RP2350（Raspberry Pi Pico / Pico 2）固件逆向重建与真机验证工作流。用于把 UF2 固件镜像的反编译结果重建成可编译工程、解析 UF2 与分区表、标定真机 HTTP/REST 契约、双目标（RP2350/RP2040）CMake 交叉编译与排错、进入 BOOTSEL 刷机并验证。触发词：Pico 固件逆向、UF2 解析、RP2350 重建工程、真机对拍、pico-sdk CMake 排错、BOOTSEL 刷机、字符串对拍、符号恢复。
agent_created: true
---

# Pico 固件逆向重建

## 目的

把一块跑着闭源固件的 RP2040/RP2350 板子，逆向重建为**可编译、可对照真机行为**的源码工程，并刷回板子验证。覆盖从 UF2 解析、反编译基准建立、源码重建、双目标构建、真机契约标定到上板刷机的完整链路。

这套流程里最有价值的是**踩过的坑**：哪些操作会让真机挂死、哪些 CMake 变量必须在特定时机设置、如何判断"刷进去的到底是不是我的固件"。这些不可能靠临场推理得出。

## 何时使用

- 拿到一份 Pico / Pico 2 的 UF2 固件，要逆向分析或重建源码
- 重建工程需要同时支持 RP2350 与 RP2040 双目标
- 真机跑着闭源固件，要通过网络/USB 标定它的行为契约
- 构建报 `PICO_PLATFORM incompatible`、flash `OVERFLOW`、符号冲突、SDK 链接脚本不生效
- 需要进 BOOTSEL 刷机，或刷完发现"起来的还是原厂固件"

## 工作流（六个阶段）

### 阶段 1：解析固件原件

用 `scripts/uf2_tool.py` 解析 UF2：段列表、family ID、block 数、拼 flat 镜像。

```bash
python scripts/uf2_tool.py info  official.uf2          # 段 / family / flags
python scripts/uf2_tool.py flat  official.uf2 out.bin  # 从 0x10000000 拼连续镜像
```

**先看 flags 再读 family ID**（`references/uf2-flash.md`）：只有 flags 含 `0x2000` 时
family 才在偏移 0x1C，否则在块尾 0x1F8；同一文件不同段的 family 可以不同
（例如主镜像 `0xe48bff59` RP2350 ARM-S、末尾分区表块 `0xe48bff57` ABSOLUTE）。
`0xe48bff56` = RP2040、`0xe48bff5a` = RP2350 RISC-V。

### 阶段 2：建立反编译基准

把反编译产物（符号表、命令表、字符串、函数清单）固化成 `re/*.json` 这类机器可读基准，
之后所有重建进度都拿它做对拍。**对拍脚本的判定逻辑有两个必踩的坑**（`references/re-baseline.md`）：

- `strings` 默认过滤短字符串 → 2 字节命令（`ls`/`cd`/`rm`）会误报缺失，要直接在 ELF 字节流里搜 `\0name\0`
- GCC `-fmerge-constants` 折叠字符串后缀 → `irq_set_enabled` 藏在 `adc_irq_set_enabled` 尾部，要分"独立串 / 共享后缀"两档统计

### 阶段 3：源码重建

按基准逐个模块重建。**每完成一个模块就对拍一次**，不要全部写完再验。

### 阶段 4：双目标构建

CMake 侧关键约束（`references/build-cmake.md`）：

- **每个平台一个构建目录**（`build/` 给 RP2350，`build-rp2040/` 给 RP2040）——混用必触发 SDK FATAL
- **在 `pico_sdk_init()` 之前**按板型 `FORCE` 设置 `PICO_PLATFORM`（SDK 默认 rp2040，缓存残留会冲突）
- **在 `pico_sdk_init()` 之前**设置 `PICO_FLASH_SIZE_BYTES`（链接脚本读 CMake 变量，只传编译宏无效）
- **`project()` 之前**显式探测交叉编译器并 `set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)`，否则 IDE 里 CMake 会尝试链接主机可执行文件而失败

### 阶段 5：真机契约标定

设备通常暴露多个 USB 接口（RNDIS 网卡 / MSC 盘 / CMSIS-DAP / CDC 串口）。
**先读 `references/device-probe.md` 的黑名单再动手**——探测本身会把设备搞挂。

用 `scripts/probe_device.py` 做只读探测，把实测响应固化成断言脚本并接进回归测试。
逐端点标定表的写法见 `references/http-contract.md`。

### 阶段 6：刷机与上板验证

进 BOOTSEL → 刷入 → 验证。刷机**前**先把原厂固件备份出来。
判定"起来的是不是我的固件"必须用**固件独有字符串**做证据，不能靠行为相似
（本项目据此误判过一次）。详见 `references/uf2-flash.md`。

## 硬性规则（最容易走弯路的地方）

1. **真机探测黑名单**：`/api/reset`、`/api/login`、`/api/logout`、`/api/flash`、
   `POST /api/setup?type=uart&oper=2`、不带 `Accept-Encoding: gzip` 请求 `/assets/*.js`——
   实测都会让设备挂死（表现为 USB 掉线）。**只读探测，绝不碰写操作。**
2. **判定固件归属看字符串，不看行为**。CDC 串口打印、HTTP 响应里的独有字符串才是证据：
   在重建的 ELF/BIN 里搜不到该串 → 跑的一定还是原厂固件。
3. **一个平台一个构建目录**，且 `PICO_PLATFORM` 按板型 FORCE 锁定。
4. **刷机前备份原厂 UF2**（MSC 盘上常有一份），并先验证备份可读。
5. **真机 HTTP 服务对连续快速请求会超时**：断言脚本每条请求后 sleep 0.25s 并重试一次；
   状态依赖的端点（如 i2c 扫描）必须先设置前置状态再断言。
6. **对拍报"缺失"先怀疑脚本**：字符串折叠、短串过滤、库版本重命名造成的假缺失，
   远比真的漏实现常见。

## 资源

### scripts/

| 文件 | 用途 |
|---|---|
| `uf2_tool.py` | UF2 解析 / 拼 flat 镜像 / bin→UF2 打包（picotool 不可用时的替代） |
| `probe_device.py` | 设备只读探测 + 契约断言模板（内置黑名单，拒绝危险端点） |

### references/

| 文件 | 何时读 |
|---|---|
| `device-probe.md` | 阶段 5：探测设备前**必读**，含接口定位方法与挂死黑名单 |
| `http-contract.md` | 阶段 5：要写 REST 契约断言表时 |
| `build-cmake.md` | 阶段 4：搭双目标 CMake / 构建报错排查 |
| `uf2-flash.md` | 阶段 1、6：解析 UF2、进 BOOTSEL、刷机与"刷了没生效"排查 |
| `re-baseline.md` | 阶段 2：写反编译对拍脚本时 |
| `porting-pitfalls.md` | 阶段 4：RP2350→RP2040 反向移植或第三方组件接入报错时 |
