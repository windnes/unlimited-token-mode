#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair_session.py —— 修复被「注入消息缺 id」写坏的会话历史。

背景
----
utc-ledger 旧版每步注入一条 user/message 却不带 `id`。框架写入时不校验，
**读取时**才校验：

    dsh-session/lib/index.js:1200
      throw new Error(`${subject} lacks an identified message`)

于是重启/重开历史时整份会话被拒，界面报：

    stored session "..." is corrupt: ... session event at seq 14
    lacks an identified message

报错只提第一条（seq 14），实际通常有几百条同样的问题。

这个脚本做什么
--------------
只给 `user/message` 事件补 UUID，不碰任何其它字段、不重排、不删行。
补完再跑一遍校验，确认能被框架接受。

它**不修代码**。代码侧修复见 plugins/utc-ledger.mjs 里的 `id: randomUUID()`；
本脚本只处理「已经写坏的历史文件」。

用法
----
    python repair_session.py --dry-run <会话目录或 .zstd 文件>
    python repair_session.py <会话目录或 .zstd 文件>          # 实际修复（先备份）
    python repair_session.py --all                            # 扫描并修复整个 sessions 目录

退出码：0 无需修复或修复成功 / 1 有无法修复的文件 / 2 输入错误
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import uuid

try:
    import zstandard
except ImportError:
    print("错误: 需要 zstandard 模块（pip install zstandard）", file=sys.stderr)
    raise SystemExit(2)

DEFAULT_SESSIONS = os.path.join(
    os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"),
    "sessions",
)

MESSAGE_TYPES = {"user/message", "assistant/message", "system/message",
                 "developer/message", "tool/result"}


def read_zstd(path: str) -> bytes:
    d = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh:
        return d.stream_reader(fh).read()


def write_zstd(path: str, raw: bytes) -> None:
    c = zstandard.ZstdCompressor(level=3)
    tmp = path + ".repair-tmp"
    with open(tmp, "wb") as fh:
        fh.write(c.compress(raw))
    os.replace(tmp, path)


def payload(event: dict) -> tuple[dict | None, str]:
    """返回 (message 对象, 它所在的容器:'data' 或 'data.message')。"""
    data = event.get("data")
    if not isinstance(data, dict):
        return None, ""
    if event.get("type") == "user/message":
        return data, "data"
    inner = data.get("message")
    if isinstance(inner, dict):
        return inner, "data.message"
    return None, ""


def needs_id(event: dict) -> bool:
    if event.get("type") not in MESSAGE_TYPES:
        return False
    msg, _where = payload(event)
    if msg is None:
        return True  # 连容器都没有，属于另一种损坏，一并报告
    return not (isinstance(msg.get("id"), str) and msg.get("id") != "")


def repair_bytes(raw: bytes, dry_run: bool) -> tuple[bytes, int, int, list[str]]:
    """返回 (新内容, 补了几条, 总事件数, 无法修复的问题列表)。"""
    out_lines: list[bytes] = []
    fixed = 0
    total = 0
    problems: list[str] = []
    for line in raw.split(b"\n"):
        if not line.strip():
            out_lines.append(line)
            continue
        try:
            obj = json.loads(line)
        except Exception:
            out_lines.append(line)
            continue
        total += 1
        if not needs_id(obj):
            out_lines.append(line)
            continue
        msg, where = payload(obj)
        if msg is None:
            problems.append("seq %s: %s 缺 data，无法自动修复"
                            % (obj.get("seq"), obj.get("type")))
            out_lines.append(line)
            continue
        if not dry_run:
            if where == "data":
                obj["data"]["id"] = str(uuid.uuid4())
            else:
                obj["data"]["message"]["id"] = str(uuid.uuid4())
        fixed += 1
        out_lines.append(json.dumps(obj, ensure_ascii=False).encode("utf-8")
                          if not dry_run else line)
    return b"\n".join(out_lines), fixed, total, problems


def find_targets(target: str) -> list[str]:
    if os.path.isfile(target):
        return [target] if target.endswith(".zstd") else []
    out: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(target):
        for name in filenames:
            if name.endswith(".zstd") and ".bak" not in name:
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="会话目录、session.v4.jsonl.zstd，或 sessions 根目录")
    ap.add_argument("--all", action="store_true", help="扫描默认 sessions 根目录下的全部会话")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写盘")
    args = ap.parse_args()

    target = args.target or (DEFAULT_SESSIONS if args.all else None)
    if not target:
        print("错误: 需要 <target> 或 --all", file=sys.stderr)
        return 2
    if not os.path.exists(target):
        print(f"错误: 不存在 {target}", file=sys.stderr)
        return 2

    files = find_targets(target)
    if not files:
        print(f"没有找到 .zstd 会话文件: {target}")
        return 0

    print(f"扫描 {len(files)} 个会话文件{'（dry-run）' if args.dry_run else ''}")
    dirty = 0
    broken = 0
    for p in files:
        try:
            raw = read_zstd(p)
        except Exception as exc:
            print(f"  ✗ 解压失败 {p}: {exc}")
            broken += 1
            continue
        new_raw, fixed, total, problems = repair_bytes(raw, args.dry_run)
        if fixed == 0 and not problems:
            continue
        dirty += 1
        rel = os.path.basename(os.path.dirname(p)) or p
        print(f"  {'需修复' if fixed else '需人工'} {rel}: "
              f"缺 id {fixed} 条 / 共 {total} 事件")
        for msg in problems[:5]:
            print(f"      {msg}")
        if problems:
            broken += 1
        if args.dry_run or fixed == 0:
            continue
        backup = p + f".bak-repair-{dt.datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(p, backup)
        write_zstd(p, new_raw)
        # 写回后复核：必须一条不缺
        _raw2, still, _t, _pr = repair_bytes(read_zstd(p), True)
        status = "通过" if still == 0 else f"仍有 {still} 条"
        print(f"      已备份 {os.path.basename(backup)}，复核{status}")

    print()
    if dirty == 0:
        print("结论: 全部会话健康，无需修复")
    elif args.dry_run:
        print(f"结论: {dirty} 个文件需要修复（dry-run 未写盘）；{broken} 个需人工")
    else:
        print(f"结论: 处理了 {dirty} 个文件；{broken} 个需人工")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
