# 反编译基准与对拍

## 基准的组织

把反编译产物固化成机器可读的 JSON，放在 `re/` 下，按类别分文件：

```
re/
├── symbols.json      函数名/符号表
├── cmdmap.json       shell 命令表
├── externs.json      C 解释器可见的 externs 表
├── strings.json      关键字符串
├── lvgl_classes.json LVGL 控件类
└── js_members.json   JS 类成员
```

每重建一个模块，就拿对应基准对拍一次，输出 `命中 / 总数 (百分比)`。

## 对拍脚本的三个必踩陷阱

### 1. `strings` 过滤短字符串 → 假缺失

`strings` 默认最小长度 4，2 字节命令（`ls`、`cd`、`rm`）会被滤掉，
脚本误报"未实现"。**正确做法：直接在 ELF 字节流里搜 `\0name\0`**：

```python
data = open(elf, 'rb').read()
found = (b'\0' + name.encode() + b'\0') in data
```

### 2. GCC `-fmerge-constants` 折叠字符串后缀 → 假缺失

`irq_set_enabled` 可能不存在独立副本，而是作为 `adc_irq_set_enabled` 的尾部共享存储。
分两档统计，别把"共享后缀命中"当缺失：

```python
exact   = (b'\0' + name + b'\0') in data      # 独立串
suffix  = name in data                        # 共享后缀 / 子串
```

输出时标注"（N 个与更长串共享存储）"，既准确又不虚报。

### 3. 库版本重命名 → 假缺失

第三方库升级会改名。对拍前先建映射表，否则永远对不齐。
LVGL v8 → v9 实际遇到的重命名：

| v8 | v9 |
|---|---|
| `lv_span` | `lv_spangroup` |
| `lv_tile` | `lv_tileview_tile` |
| `lv_btn` / `lv_btnmatrix` | `lv_button` / `lv_buttonmatrix` |
| `lv_img` | `lv_image` |
| `lv_imgbtn` | `lv_imagebutton` |
| `lv_cont` | `lv_obj`（v9 已移除） |

## 对拍输出格式

```
  [OK  ] commands              75/ 75 (100.0%)  (3 个短命令用字节流匹配)
  [OK  ] externs table        340/340 (100.0%)  (5 个与更长串共享存储)
  [OK  ] lvgl widget classes   49/ 49 (100.0%)  (6 个 v8->v9 重命名后命中)
  [OK  ] js class members     106/106 (100.0%)  (10 个与更长串共享存储)
```

阈值不要强行要求 100%（保留"共享后缀"这类标注），但**独立串命中率**要盯紧。

## 对拍脚本要进回归

把 `verify_re.py` 这类脚本接进 `tests/run_all.sh`。它跑得快、纯离线，
是防止重建过程中悄悄丢符号的第一道防线。

## 判定"缺失"时的排查顺序

1. 是不是短字符串（<4 字符）被过滤？ → 用字节流搜
2. 是不是被更长串折叠？ → 查子串命中
3. 是不是库版本改名？ → 查重命名映射表
4. 是不是该模块被 `ENABLE_*` 开关关掉了？ → 查构建配置
5. 是不是被 `--gc-sections` 裁掉了？ → 查是否真的被调用
6. 以上都排除，才去改实现

## 特别注意：`--gc-sections` 会静默吞掉代码

一个模块**编译通过但从未被主流程调用**时，链接器会把它整个裁掉，
构建依然成功、没有任何警告。典型症状：符号表里找不到该模块的任何函数。

排查：确认 `main()` 里真的调用了初始化与轮询函数（如 `web_init()` / `web_poll()`）。
