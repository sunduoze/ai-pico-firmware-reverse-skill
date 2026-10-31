# 真机 REST 契约标定

## 方法

不要假设端点语义，**逐端点实测**。对同一个 POST 端点，用不同参数组合扫出取值规则：

1. 先用 GET 打一遍 —— 很多固件对错误方法返回 `500` 且**不带 Content-Type**，
   这是重要特征，必须原样复现（重建实现里写成 404 就是对不上）。
2. 再对 POST 扫参数：枚举 type、oper 0..3、越界值（如 255）看截断规则。
3. 观察**字段数量**：失败分支常常只回部分字段（成功 6 字段，失败 3 字段）。
4. 观察**字节级细节**：比如 JSON 结尾是 `], "totalCount":N}`（逗号后有空格）。
   前端可能不在意，但对拍必须一致。

**扫参数时一次只改一个变量，且避开已知挂死组合。**

## 实测案例（xTools 3.3.1，作为标定的参考形态）

| 端点 | 方法 | 真机实测响应 | 容易写错的地方 |
|---|---|---|---|
| `/` | GET | 200，606 B HTML | 资源未嵌入时会返回 1 字节垃圾 |
| `/assets/index-*.js` | GET + gzip | 200，`application/javascript` | 不带 gzip 会挂死设备 |
| `/api/stats/get` | GET | 200 JSON，含 temperature/humidity/points(13) | 点数写死成别的值 |
| `/api/settings/get` | GET | 200 JSON | — |
| `/api/events/get` | GET | 200 JSON，结尾 `], "totalCount":N}`（逗号后**有空格**） | 漏掉空格 |
| `/api/setup` | GET | **500 `{"result":-1}`**，无 Content-Type | 写成 404 |
| `/api/setup?type=i2c&oper=0` | POST | 200 `{"result":400000}`（回的是当前 I2C 速率） | 以为 result 是成败标志 |
| `/api/setup?type=xxx` | POST | 500 `{"result":-2}` | 未知 type 的分支漏掉 |
| `/api/setup?type=gpio...` | POST | 恒 `{"result":-1}` | — |
| `/api/setup?type=uart&oper=2` | POST | **挂死**（黑名单） | — |
| `/api/scan_i2c` | GET/POST | 状态依赖：未配置 i2c → 500 `{"result":-2,"message":"device not configed"}` | 按方法分派是错的 |
| `/api/swd_port` | POST | `oper≠3` 时 `result` 恒 **0**；`oper==1` 时 `act = value & 3`；`ex_p` 请求传 1 也**恒回 0** | 直接赋值 act、回显 ex_p |
| `/api/swd_port` `oper=3` 未接目标 | POST | `result=-1`，**只回 3 个字段** | 回 6 字段 |
| `/api/clear_swd_buff` | GET | 200 `{"result":0}`，无 Content-Type | 加 Content-Type |
| `/api/debug` | GET | 200 `Debug level set to 3` | — |
| `/www/*` | GET | 200 静态文件 | — |
| `/upload/*` | GET | **404**（HTTP 只暴露 `/www/`） | 误放行 `/upload/` |
| 根文件如 `/start.js` | GET | 404 | 以为根路径也能取 |
| 未知路径 | GET | 404 | — |

## 静态资源：必须带 gzip 协商

前端 bundle 在固件里通常是 gzip 预压缩的。请求必须带 `Accept-Encoding: gzip`，
否则设备吐明文会卡死。对拍时用 sha256 逐字节比对，不要只比大小：

```python
c.request("GET", "/assets/index-9a02e455.js", headers={"Accept-Encoding": "gzip"})
```

## 标定流程建议

1. 写一个最小安全探测脚本（只 GET、只无副作用端点），跑通并打印全部响应
2. 把每条响应整理成契约表（如上）
3. 照表逐条修正重建实现
4. 把契约表固化成断言脚本，接进回归测试（设备缺席则 SKIP）
5. 重新构建后**再跑一遍断言**，确认没有回归

## 契约表要记录的东西

- 状态码（错误方法常是 500 而非 404/405）
- Content-Type（很多固件错误响应不带）
- body 精确字节（含空格、字段顺序、字段数量）
- 状态依赖的前置条件
- 已知会挂死的组合（写进黑名单）
