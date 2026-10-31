#!/usr/bin/env python3
"""UF2 工具：解析 / 拼 flat 镜像 / 打包（picotool 不可用时的替代）。

用法:
    uf2_tool.py info  <file.uf2>
    uf2_tool.py flat  <file.uf2> <out.bin> [--base 0x10000000]
    uf2_tool.py pack  <in.bin|in.elf> <out.uf2> [family] [--base 0x10000000]
                      [--objcopy arm-none-eabi-objcopy]

family: rp2040 | rp2350 | rp2350-riscv | rp2350-abs(=absolute)
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

MAGIC0 = 0x0A324655  # "UF2\n"
MAGIC1 = 0x9E5D5157

# boot/uf2.h（pico-sdk）
UF2_FLAG_NOT_MAIN_FLASH          = 0x00000001
UF2_FLAG_FILE_CONTAINER          = 0x00001000
UF2_FLAG_FAMILY_ID_PRESENT       = 0x00002000
UF2_FLAG_MD5_PRESENT             = 0x00004000
UF2_FLAG_EXTENSION_FLAGS_PRESENT = 0x00008000

FLAG_NAMES = {
    "rp2040": 0xE48BFF56,
    "rp2350": 0xE48BFF59,        # RP2350_ARM_S，SDK 标准封装
    "rp2350-riscv": 0xE48BFF5A,
    "rp2350-arm-ns": 0xE48BFF5B,
    "rp2350-abs": 0xE48BFF57,    # ABSOLUTE：按块内绝对地址烧写
    "absolute": 0xE48BFF57,
    "data": 0xE48BFF58,
}
FAMILY = FLAG_NAMES


def flag_str(flags):
    names = []
    for bit, name in (
        (UF2_FLAG_NOT_MAIN_FLASH, "NOT_MAIN_FLASH"),
        (UF2_FLAG_FILE_CONTAINER, "FILE_CONTAINER"),
        (UF2_FLAG_FAMILY_ID_PRESENT, "FAMILY_ID"),
        (UF2_FLAG_MD5_PRESENT, "MD5"),
        (UF2_FLAG_EXTENSION_FLAGS_PRESENT, "EXT_FLAGS"),
    ):
        if flags & bit:
            names.append(name)
    return "|".join(names) if names else "0"


def block_family(blk, flags):
    """family ID 的位置由 flags 决定：
    FAMILY_ID_PRESENT(0x2000) -> 在 header 偏移 0x1C；否则在块末尾 0x1F8。"""
    if flags & UF2_FLAG_FAMILY_ID_PRESENT:
        return struct.unpack("<I", blk[28:32])[0]
    return struct.unpack("<I", blk[504:508])[0]


def read_blocks(path):
    """返回 [(addr, flags, family, payload_bytes), ...]"""
    data = open(path, "rb").read()
    blocks = []
    for i in range(0, len(data), 512):
        b = data[i:i + 512]
        if len(b) < 32:
            break
        m0, m1 = struct.unpack("<II", b[0:8])
        if m0 != MAGIC0 or m1 != MAGIC1:
            continue
        flags, addr, size = struct.unpack("<III", b[8:20])
        fam = block_family(b, flags)
        blocks.append((addr, flags, fam, b[32:32 + size]))
    return blocks


def segments(blocks):
    """把连续（步长为 payload size）的块合并成段"""
    segs = []
    for addr, flags, fam, payload in sorted(blocks, key=lambda x: x[0]):
        step = len(payload) or 256
        if segs and addr == segs[-1]["end"] + step and flags == segs[-1]["flags"]:
            segs[-1]["end"] = addr
            segs[-1]["n"] += 1
        else:
            segs.append({"start": addr, "end": addr, "flags": flags,
                         "fam": fam, "n": 1, "step": step})
    return segs


def cmd_info(args):
    blocks = read_blocks(args.uf2)
    if not blocks:
        print("no UF2 blocks found")
        return 1
    fams = {}
    for _, _, f, _ in blocks:
        fams[f] = fams.get(f, 0) + 1
    print("file      : %s (%d B, %d blocks)" % (args.uf2, os.path.getsize(args.uf2), len(blocks)))
    print("families  : %s" % ", ".join("%#x x%d" % (f, n) for f, n in sorted(fams.items())))
    print("flags     : %s" % ", ".join(
        "%#x(%s)" % (f, flag_str(f)) for f in sorted({b[1] for b in blocks})))
    print("segments  :")
    for s in segments(blocks):
        print("  %#010x .. %#010x  (%8d B, %5d blocks)  flags=%#06x(%s) fam=%#x"
              % (s["start"], s["end"] + s["step"] - 1,
                 (s["end"] - s["start"]) + s["step"], s["n"],
                 s["flags"], flag_str(s["flags"]), s["fam"]))
    hi = max(b[0] for b in blocks)
    if hi >= 0x10000000:
        print("flash end : %#x  -> >= %d MB" % (hi, (hi - 0x10000000 + 256) // 1048576))
    return 0


def cmd_flat(args):
    blocks = {a: p for a, _, _, p in read_blocks(args.uf2)}
    base = args.base
    out = bytearray()
    cur = base
    while cur in blocks:
        out += blocks[cur]
        cur += 256
    open(args.out, "wb").write(out)
    print("wrote %s  (%d bytes, contiguous from %#x)" % (args.out, len(out), base))
    if len(out) >= 16:
        sp, reset = struct.unpack("<II", out[0:8])
        print("  initial SP   = %#x" % sp)
        print("  reset vector = %#x" % reset)
    return 0


def bin_from_elf(elf, objcopy):
    tmp = os.path.join(tempfile.gettempdir(), os.path.basename(elf) + ".bin")
    subprocess.run([objcopy, "-O", "binary", elf, tmp], check=True)
    data = open(tmp, "rb").read()
    os.remove(tmp)
    return data


def cmd_pack(args):
    if args.input.lower().endswith(".elf"):
        payload = bin_from_elf(args.input, args.objcopy)
    else:
        payload = open(args.input, "rb").read()
    key = (args.family or "").lower()
    if key in FAMILY:
        fam = FAMILY[key]
    else:
        fam = int(args.family, 0)
    n = (len(payload) + 476 - 1) // 476
    with open(args.out, "wb") as f:
        for i in range(n):
            chunk = payload[i * 476:(i + 1) * 476]
            chunk = chunk + b"\0" * (476 - len(chunk))
            blk = bytearray(b"\0" * 512)
            struct.pack_into("<IIIIIIII", blk, 0,
                             MAGIC0, MAGIC1, UF2_FLAG_FAMILY_ID_PRESENT,
                             args.base + i * 476, len(chunk), i, n, fam)
            blk[32:32 + 476] = chunk
            struct.pack_into("<I", blk, 508, 0x0AB16F30)
            f.write(blk)
    print("wrote %s  (%d bytes payload -> %d blocks, family %#x, base %#x)"
          % (args.out, len(payload), n, fam, args.base))
    return 0


def main():
    ap = argparse.ArgumentParser(description="UF2 tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="show segments / family / flags")
    p.add_argument("uf2")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("flat", help="assemble contiguous flat image from base")
    p.add_argument("uf2")
    p.add_argument("out")
    p.add_argument("--base", type=lambda x: int(x, 0), default=0x10000000)
    p.set_defaults(func=cmd_flat)

    p = sub.add_parser("pack", help="pack bin/elf into UF2")
    p.add_argument("input")
    p.add_argument("out")
    p.add_argument("family", nargs="?", default="rp2350")
    p.add_argument("--base", type=lambda x: int(x, 0), default=0x10000000)
    p.add_argument("--objcopy", default="arm-none-eabi-objcopy")
    p.set_defaults(func=cmd_pack)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
