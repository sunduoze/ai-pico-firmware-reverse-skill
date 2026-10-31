#!/usr/bin/env python3
"""设备只读探测 + 契约断言模板。

设计要点（都是踩过坑换来的）：
  * 内置黑名单：任何会改变设备状态 / 已知会挂死固件的端点一律拒绝发送
  * 请求节流：真机上的 mongoose 对连续快速请求会偶发超时，默认每条间隔 0.25s 并重试 1 次
  * 设备不在线时整体 SKIP（退出码 0），方便直接接进回归测试

用法:
    # 安全探测：打印一组 GET 的状态码 / Content-Type / body 前若干字节
    probe_device.py --host 192.168.33.1

    # 契约断言：按 JSON 用例文件逐条比对
    probe_device.py --host 192.168.33.1 --contract cases.json

cases.json 格式（列表）:
    [
      {"name": "GET /", "method": "GET", "path": "/",
       "status": 200, "body": "<!doctype html>", "ct": "text/html",
       "hdrs": {"Accept-Encoding": "gzip"}},
      {"name": "stats shape", "method": "GET", "path": "/api/stats/get",
       "status": 200, "json_keys": ["temperature", "points"]}
    ]
其中 status/body/ct/json_keys 均可省略；body 只要是被包含即算通过。
"""
import argparse
import http.client
import json
import socket
import sys
import time

# ---- 已知会让真机挂死 / 改变设备状态的端点，一律拒绝 ----
BLACKLIST = (
    "/api/reset",
    "/api/login",
    "/api/logout",
    "/api/flash",
    "type=uart&oper=2",      # 实测挂死该 TCP 连接
)

DEFAULT_PROBES = [
    ("GET", "/", {"Accept-Encoding": "gzip"}),
    ("GET", "/api/stats/get", None),
    ("GET", "/api/settings/get", None),
    ("GET", "/api/events/get", None),
    ("GET", "/api/clear_swd_buff", None),
    ("GET", "/api/debug", None),
    ("GET", "/api/nonexistent", None),
    ("GET", "/www/nonexistent.json", None),
]


def is_dangerous(path):
    low = path.lower()
    return any(b.lower() in low for b in BLACKLIST)


def req(host, port, method, path, body=None, hdrs=None, timeout=8.0, tries=2):
    """返回 (status, headers_dict, body_bytes)；失败返回 (None, None, err_str)"""
    last = "unknown error"
    for _ in range(tries):
        c = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            c.request(method, path, body=body, headers=hdrs or {})
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        except Exception as e:                       # noqa: BLE001
            last = "%s" % e
            time.sleep(0.4)
        finally:
            c.close()
    return None, None, last


def online(host, port, timeout=3.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:                                # noqa: BLE001
        return False


def run_probes(host, port, gap, probes):
    for method, path, hdrs in probes:
        if is_dangerous(path):
            print("[SKIP-BLACKLIST] %s %s" % (method, path))
            continue
        st, h, b = req(host, port, method, path, hdrs=hdrs)
        if st is None:
            print("%-5s %-28s -> ERR %s" % (method, path, b))
        else:
            ct = (h or {}).get("content-type", "-")
            txt = b[:200].decode("utf-8", "replace").replace("\n", " ")
            print("%-5s %-28s -> %s ct=%-22s %s" % (method, path, st, ct, txt))
        time.sleep(gap)


def run_contract(host, port, gap, cases):
    ok = fail = 0
    for c in cases:
        name = c.get("name") or "%s %s" % (c.get("method", "GET"), c.get("path"))
        path = c["path"]
        if is_dangerous(path):
            print("[SKIP-BLACKLIST] %s" % name)
            continue
        st, h, b = req(host, port, c.get("method", "GET"), path,
                       body=c.get("body"), hdrs=c.get("hdrs"))
        time.sleep(gap)
        problems = []
        if st is None:
            problems.append("request failed: %s" % b)
        else:
            if "status" in c and st != c["status"]:
                problems.append("status %s != %s" % (st, c["status"]))
            if "ct" in c and (h or {}).get("content-type") != c["ct"]:
                problems.append("ct %r != %r" % ((h or {}).get("content-type"), c["ct"]))
            if "body" in c and c["body"].encode() not in b:
                problems.append("body mismatch: %r" % b[:120])
            if "json_keys" in c:
                try:
                    d = json.loads(b)
                    missing = [k for k in c["json_keys"] if k not in d]
                    if missing:
                        problems.append("missing keys %s" % missing)
                except Exception as e:               # noqa: BLE001
                    problems.append("not JSON (%s)" % e)
        if problems:
            fail += 1
            print("[FAIL] %-40s %s" % (name, "; ".join(problems)))
        else:
            ok += 1
            print("[ OK ] %s" % name)
    print("=" * 40)
    print(" %d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(description="read-only device probe / contract check")
    ap.add_argument("--host", default="192.168.33.1")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--contract", help="JSON case file")
    ap.add_argument("--gap", type=float, default=0.25, help="seconds between requests")
    args = ap.parse_args()

    if not online(args.host, args.port):
        print("SKIP: device %s:%s not reachable" % (args.host, args.port))
        return 0

    if args.contract:
        cases = json.load(open(args.contract, "rb"))
        return run_contract(args.host, args.port, args.gap, cases)
    run_probes(args.host, args.port, args.gap, DEFAULT_PROBES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
