# 双目标（RP2350 / RP2040）CMake 构建

## 目录约定

```
build/          RP2350 专用（PICO_BOARD=pico2）
build-rp2040/   RP2040 专用（PICO_BOARD=pico）
```

**绝对不能混用。** SDK 会在缓存里保存平台与工具链，混用直接 FATAL。

## 平台锁定（必做，否则迟早踩坑）

SDK 的 `PICO_DEFAULT_PLATFORM` 是 **rp2040**。只要有一次 configure 没读到
`-DPICO_BOARD=pico2`（IDE 首次打开、换 kit、settings 未生效），缓存里就留下
`PICO_PLATFORM=rp2040` + cortex-M0+ 工具链，之后再按 pico2 配置：

```
CMake Error at pico-sdk/cmake/pico_pre_load_platform.cmake:117
  PICO_PLATFORM is specified to be 'rp2040', but PICO_BOARD='pico2' uses 'rp2350'
  ... You need to delete the CMake cache or build directory
```

在 `pico_sdk_init()` 之前按板型 FORCE 锁定（SDK 第 14 行 `PICO_SAVED_PLATFORM = ${PICO_PLATFORM}`，
FORCE 后两者一致即不再冲突）：

```cmake
if (PICO_BOARD MATCHES "pico2|rp2350|2350")
    set(PXT_PLATFORM_WANT rp2350)
else ()
    set(PXT_PLATFORM_WANT rp2040)
endif ()
if (NOT PICO_PLATFORM)
    set(PICO_PLATFORM ${PXT_PLATFORM_WANT})
else ()
    string(REGEX REPLACE "-.*" "" _plat_prefix "${PICO_PLATFORM}")
    if (NOT _plat_prefix STREQUAL PXT_PLATFORM_WANT)
        message(WARNING "PICO_PLATFORM 与 PICO_BOARD 不匹配，自动纠正为 ${PXT_PLATFORM_WANT}")
        set(PICO_PLATFORM ${PXT_PLATFORM_WANT})
    endif ()
endif ()
set(PICO_PLATFORM ${PICO_PLATFORM} CACHE STRING "PICO platform (locked from PICO_BOARD)" FORCE)
```

已经污染时的解药：`rm -f build/CMakeCache.txt && rm -rf build/CMakeFiles` 后重新 configure。
（PowerShell 没有 `rm -rf`，用 Git Bash 或 `Remove-Item -Recurse -Force`。）

## flash 容量：**必须在 pico_sdk_init() 之前**设 CMake 变量

SDK 链接脚本读的是 **CMake 变量** `PICO_FLASH_SIZE_BYTES`，且 `pico_sdk_init()` 阶段就生成好了。
只把它当编译宏传是无效的（4MB 板开大资源时会撞 2MB 限制而 `OVERFLOW`）。

另一个坑：`pico_sdk_init()` 之前 SDK 已经填过默认值，所以**不能**用 `if (NOT PICO_FLASH_SIZE_BYTES)` 包住，
那样永远进不去，用户传的值被静默忽略：

```cmake
if (PXT_FLASH_SIZE_BYTES)
    set(PICO_FLASH_SIZE_BYTES ${PXT_FLASH_SIZE_BYTES})   # 无条件下发
elseif (PICO_BOARD MATCHES "pico2|rp2350|2350")
    set(PICO_FLASH_SIZE_BYTES 4194304)
endif ()
math(EXPR _n "${PICO_FLASH_SIZE_BYTES}")   # 表达式字符串归一成数字
```

把探测结果写到文件供构建脚本读取（比按板名猜可靠）：

```cmake
file(WRITE ${CMAKE_BINARY_DIR}/pxt_platform.txt "${PXT_PLATFORM}\n${_n}\n")
```

## 交叉编译器：在 project() 之前显式探测

IDE（CMake Tools）不一定传编译器。让 CMakeLists 自己兜底：

```cmake
if (NOT CMAKE_C_COMPILER)
    find_program(PXT_ARM_GCC NAMES arm-none-eabi-gcc HINTS ${PICO_TOOLCHAIN_PATH})
    if (NOT PXT_ARM_GCC)
        message(FATAL_ERROR "arm-none-eabi-gcc not found. 设置 PICO_TOOLCHAIN_PATH 或加入 PATH")
    endif ()
    get_filename_component(_tc_bin "${PXT_ARM_GCC}" DIRECTORY)
    set(CMAKE_C_COMPILER   "${_tc_bin}/arm-none-eabi-gcc${CMAKE_EXECUTABLE_SUFFIX}")
    set(CMAKE_CXX_COMPILER "${_tc_bin}/arm-none-eabi-g++${CMAKE_EXECUTABLE_SUFFIX}")
    set(CMAKE_ASM_COMPILER "${_tc_bin}/arm-none-eabi-gcc${CMAKE_EXECUTABLE_SUFFIX}")
endif ()
# 关键：否则 CMake 编译器探测会尝试链接主机可执行文件并失败
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
```

## UF2 打包：picotool 不可用时

`pico_add_extra_outputs()` 只出 elf/bin/hex/map。用 `-DPICO_NO_PICOTOOL=1` 关掉 picotool，
再用脚本打包（见 `scripts/uf2_tool.py pack`）。用 POST_BUILD 自动执行，IDE 点"生成"就能拿到：

```cmake
add_custom_command(TARGET ${TARGET_NAME} POST_BUILD
    COMMAND ${Python3_EXECUTABLE} "${CMAKE_CURRENT_LIST_DIR}/scripts/mkuf2.py"
            "${CMAKE_CURRENT_BINARY_DIR}/${TARGET_NAME}.elf"
            "${CMAKE_CURRENT_BINARY_DIR}/${TARGET_NAME}-rp2350.uf2" "rp2350" 0x10000000
    BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${TARGET_NAME}-rp2350.uf2"
    VERBATIM)
```

**坑**：`add_custom_command` 里用 `$<TARGET_FILE_DIR:${TARGET_NAME}>` 会在生成阶段报
`Error evaluating generator expression: No target "xxx"`（目标其实存在）。
改用普通路径 `${CMAKE_CURRENT_BINARY_DIR}/${TARGET_NAME}.elf`。

## VSCode / CMake Tools 配置要点

`.vscode/settings.json`：

```jsonc
"cmake.cmakePath": "D:/xxx/cmake/bin/cmake.exe",
"cmake.generator": "Ninja",
"cmake.buildDirectory": "${workspaceFolder}/build",
"cmake.configureOnOpen": false,        // 避免用错 kit 自动跑一遍
"cmake.configureArgs": ["-DPICO_BOARD=pico2", "-DPICO_NO_PICOTOOL=1"],
"cmake.environment": { "PATH": "<toolchain/bin>;<cmake/bin>;<ninja>;<git/usr/bin>;...;${env:PATH}",
                       "PICO_SDK_PATH": "C:/xxx/pico-sdk" },
"cmake.copyCompileCommands": "${workspaceFolder}/compile_commands.json"
```

`.vscode/cmake-kits.json` 里预置 arm-none-eabi kit（含 `environmentVariables.PATH`），
`c_cpp_properties.json` 用 `"configurationProvider": "ms-vscode.cmake-tools"`。

切平台时改两处：`cmake.configureArgs` 的板名 + `cmake.buildDirectory`。

## 报错对照表

| 报错 | 原因 | 解法 |
|---|---|---|
| `PICO_PLATFORM ... incompatible` | 缓存残留另一平台 | 删 `CMakeCache.txt` + `CMakeFiles` |
| `arm-none-eabi-gcc not found` / 编译器探测失败 | 缺 `CMAKE_TRY_COMPILE_TARGET_TYPE` 或 PATH 不全 | 见上文两段 |
| `region 'FLASH' overflowed` | flash 变量没生效（只在编译宏里） | 在 `pico_sdk_init()` 前 set 变量 |
| `Error evaluating generator expression: No target` | POST_BUILD 里用了 `$<TARGET_FILE_DIR>` | 换普通路径 |
| `duplicate symbol __wrap___aeabi_*` | 与 SDK/库抢符号 | 全部改 `__attribute__((weak))` |
| LVGL 内存/表现不对 | `lv_conf.h` 被跳过（双重 `#ifndef LV_CONF_H`） | 删多余的外层守卫 |
| `implicit declaration of function` (`__wfi`、`save_and_disable_interrupts`、 `strdup`) | 缺 `#include "hardware/sync.h"` / `<string.h>` | 补头文件 |
| `TIMER_IRQ_0` 之类未定义（RP2040） | IRQ 名在 RP2040 是 **enum** 不是宏 | 用 `#if PICO_RP2350` 分支，不要 `#ifndef` |

## 容量体检

构建末尾打印 flash / 静态 RAM 占用，超限时给出可执行的建议（关哪个开关）：

```
flash usage: 2899572 / 4194304 B (69.1%)
static RAM  : 163628 / 532480 B (30.7%) + 8KB stacks + heap
```

RAM 用 `arm-none-eabi-size -A` 取 `.data + .bss + ...` 求和。
