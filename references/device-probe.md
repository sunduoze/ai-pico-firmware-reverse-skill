# 真机探测：接口定位与安全边界

## 典型 USB 拓扑

一块跑着 Web 类固件的 Pico 板通常枚举成 **VID_2E8A** 的复合设备，同时暴露四个接口：

| 接口 | 表现 | 用途 |
|---|---|---|
| RNDIS | Windows 里多出一张网卡（如「以太网 2」） | HTTP 访问：`192.168.33.2/24` → 设备 `192.168.33.1:80` |
| MSC | 可移动磁盘（如 `E:`，FAT） | 只读访问固件内 FAT 卷 |
| CMSIS-DAP v2 | HID/WinUSB 调试接口 | 只能对外调试别的芯片，不能给自己刷机 |
| CDC | 串口（如 COM4，115200） | shell 交互 |

## 定位命令（Windows / PowerShell）

```powershell
# 1) 确认设备在线
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_2E8A' } |
    Select-Object FriendlyName, InstanceId, Status

# 2) RNDIS 网卡与 IP（找新出现的那张 Up 网卡）
Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | Select-Object Name, InterfaceDescription, LinkSpeed
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } |
    Select-Object InterfaceAlias, IPAddress, PrefixLength

# 3) MSC 盘符
Get-Volume | Where-Object { $_.DriveType -eq 'Removable' } |
    Select-Object DriveLetter, FileSystemLabel, FileSystem, Size
```

拿到网卡 IP 后，设备地址一般是同网段的 `.1`。确认链路：`ping -n 2 192.168.33.1`，
再测 TCP 80 是否开放。

## 探测顺序（严格按此顺序，逐步放宽）

1. `GET /` —— 确认 Web 服务活着，比对 HTML 字节数
2. `GET /assets/*.js|css|png`，带 `Accept-Encoding: gzip` —— 比对 sha256
3. 只读 REST 端点（`/api/stats/get`、`/api/settings/get`、`/api/events/get` …）
4. POST 类端点：先挑**无副作用**的（如 `/api/swd_port` 只读回），再考虑有状态的
5. WebSocket：只做握手，不持续压测

## 挂死黑名单（实测，绝不能发）

| 操作 | 后果 |
|---|---|
| `GET /api/reset` | 设备重启；后续可能不再枚举 |
| `/api/login`、`/api/logout`、`/api/flash` | 连接挂死 |
| `POST /api/setup?type=uart&oper=2` | **该 TCP 连接挂死**（曾表现为"设备掉线"，实为固件卡死） |
| `GET /assets/*.js` 不带 `Accept-Encoding: gzip` | 设备尝试吐 1MB+ 明文，卡死 |

设备"掉线"时**先分辨是物理断连还是固件挂死**：
`Get-PnpDevice -PresentOnly` 里没有 `VID_2E8A`、RNDIS 网卡消失、盘符不存在 →
多半是 USB 物理断开或固件真卡死，需要重新插拔，而不是继续重试请求。

## 请求节流与状态依赖

- 真机上的 mongoose 对**连续快速请求**会偶发超时。断言脚本：每条请求后 `sleep 0.25`，
  超时重试 1 次，超时设 6–8 秒。
- 有些端点是**状态依赖**的，不是按 HTTP 方法分派。例如 i2c 扫描：未配置 i2c 时
  GET/POST 都返回 500 `{"result":-2,"message":"device not configed"}`；
  配置过之后 GET 和 POST 行为一致。断言这类端点前必须先设置前置状态。
- 静态量（如 SWD 的 `act`）在设备上是持久的，测试开头要先复位再断言。

## MSC 盘只读对拍

FAT 卷可以直接读，适合核对"出厂文件种子"：

```bash
cd /e && find . -type f | grep -v 'System Volume|fseventsd|Trashes|/\._' | sort
```

注意区分：FAT 卷常常在**外部 SPI flash** 上，不在 RP2350 内部 flash。
所以刷内部 flash 不会清空盘内容——盘上残留旧文件是正常的，不要误判为"刷机失败"。

## CDC 串口

115200 8N1。设备可能在打开端口时输出 banner，也可能只在收到回车后响应。
**串口输出是判断当前跑哪份固件的关键证据**（见 `uf2-flash.md`）。

## 断言脚本的组织方式

把每条实测响应固化成一个 case（方法、路径、期望状态码、期望 body 前缀或正则、
期望 Content-Type），跑完统计 PASS/FAIL。设备不在线时整体 SKIP，
这样脚本可以直接接进 CI/回归，不会因设备缺席而红。
