# 移植与组件接入踩坑清单

按"症状 → 原因 → 解法"组织。这些都是 RP2350 → RP2040 反向移植、
或把第三方组件接进 pico-sdk 工程时实际遇到的。

## 平台差异：IRQ 名在 RP2040 是 enum，不是宏

**症状**：RP2040 构建报 `TIMER_IRQ_0 undeclared` 之类。
**原因**：RP2350 上这些是宏，RP2040 上是 enum 成员。用 `#ifndef TIMER_IRQ_0` 判断
**恒为真**（预处理器看不到 enum），于是错误地走进 RP2350 分支。
**解法**：统一用 `#if PICO_RP2350` / `#else` 分支，不要用 `#ifndef` 探测 enum。

## 隐式声明：`hardware/sync.h`

**症状**：`implicit declaration of function 'save_and_disable_interrupts'` / `'__wfi'`。
**解法**：补 `#include "hardware/sync.h"`。

## 符号冲突：`__wrap___aeabi_*`

**症状**：链接报 `duplicate symbol __wrap___aeabi_uldivmod` 之类（与 SDK / libc 抢）。
**解法**：把所有 `__wrap___aeabi_*` 实现标 `__attribute__((weak))`。

## FreeRTOS port 选择

**症状**：RP2350 上跑不起来 / 中断优先级异常。
**解法**：按平台选 port 目录：RP2040 用 `ThirdParty/GCC/RP2040`，
RP2350 用 `ThirdParty/GCC/RP2350_ARM_NTZ`（非 TrustZone）。
另外 `FreeRTOSConfig.h` 里的 `configASSERT` 不要实现成会阻塞的形式（改用
`portDISABLE_INTERRUPTS()` 或直接空实现）。

## LVGL 配置被整份跳过

**症状**：LVGL 内存行为异常，改 `lv_conf.h` 无效，RAM 占用明显偏大。
**原因**：`lv_conf.h` 里出现了**两层同名的 `#ifndef LV_CONF_H`**，
外层把整份配置挡掉，LVGL 一直用默认值。
**解法**：只保留一层守卫。（这个问题两个平台都会中招。）

## flash 容量宏只在编译期生效

**症状**：4MB 板开大资源时链接报 region overflow，但明明传了容量宏。
**原因**：链接脚本读的是 CMake 变量，且 `pico_sdk_init()` 之前 SDK 已填默认值，
`if (NOT PICO_FLASH_SIZE_BYTES)` 永远进不去。
**解法**：见 `build-cmake.md`——无条件下发 CMake 变量，并用 `math(EXPR)` 归一。

## 资源占位导致长度错误

**症状**：`ENABLE_WEB_ASSETS=0` 时 `GET /` 返回 1 字节垃圾。
**原因**：占位块写成 `.byte 0`，资源长度算成 1。
**解法**：占位也写成带哨兵的正常条目；无前端时让 `GET /` 返回一个 API 索引页，
而不是吐空资源。同时把别名表用 `#if WA_EMBED` 一起关掉。

## C 库函数隐式声明

**症状**：`strdup` 返回 int、或 `implicit declaration`。
**解法**：补 `<string.h>` / `<stdlib.h>`。注意某些工具链的 newlib 配置下
`strdup` 需要特性宏才能声明。

## 字符串常量折叠影响对拍

不是构建错误，但会让对拍脚本误判。见 `re-baseline.md`。

## 链接脚本里重复声明 memory region（warning）

```
ld.exe: cc_udata.ld:22: warning: memory region `RAM' not declared
ld.exe: memory_ram.incl:3: warning: redeclaration of memory region `RAM'
```

自定义 linker script 片段与 SDK 的 `memory_ram.incl` 冲突。通常是无害 warning，
但如果自定义段确实没落进 RAM 就要处理（确认片段被 include 的顺序）。

## 通用排查顺序

1. 先分清是**配置问题**还是**代码问题**：换一个平台构建试试，两个平台都挂 → 多半是代码
2. 编译期错误：先查头文件与平台宏分支（enum vs macro 那一类）
3. 链接期错误：先查符号归属（是否该 weak）与段容量
4. 运行期异常：先查配置文件是否真的生效（`lv_conf.h`、容量变量那一类）
5. 对拍报缺失：先怀疑脚本（`re-baseline.md`）
