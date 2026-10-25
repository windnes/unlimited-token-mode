#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_prefix.py —— 把 preset/prompt-prefix*.md 同步进 preset/cordis-preset*.yml 的 persona.prefix。

为什么要这个脚本：同一段 persona 前缀在仓库里存在两份（可读的 .md 和实际生效的 .yml 内联块），
手改一处忘另一处就会出现「文档说一套、运行时跑另一套」。v1 就靠人眼 diff 维持同步。
这里把 .md 当作唯一真源，.yml 是它的投影，并提供 --check 供自测调用。

用法：
    python sync_prefix.py           # 写入
    python sync_prefix.py --check   # 只校验是否一致（不一致返回 1）
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = [
    ("preset/prompt-prefix.md", "preset/cordis-preset.yml"),
    ("preset/prompt-prefix-ptc.md", "preset/cordis-preset-ptc.yml"),
]

PREFIX_KEY = "prefix: |"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def extract_yml_prefix(text: str) -> tuple[int, int, str]:
    """返回 (起始行号, 结束行号, 内容)。块内容为 prefix: | 之后所有更深缩进的行。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == PREFIX_KEY:
            base = indent_of(line)
            j = i + 1
            body: list[str] = []
            while j < len(lines):
                cur = lines[j]
                if cur.strip() == "":
                    body.append("")
                    j += 1
                    continue
                if indent_of(cur) <= base:
                    break
                body.append(cur)
                j += 1
            # 去掉尾部空行
            while body and body[-1].strip() == "":
                body.pop()
            content_indent = indent_of(body[0]) if body else base + 2
            stripped = "\n".join(
                (line[content_indent:] if line.strip() else "") for line in body
            )
            return i, j, stripped
    raise SystemExit(f"在 {PREFIX_KEY} 处没找到 persona 前缀块")


def render(text: str, md_content: str) -> str:
    lines = text.splitlines()
    i, j, _ = extract_yml_prefix(text)
    base = indent_of(lines[i])
    content_indent = " " * (base + 2)
    md_content = md_content.rstrip("\n")
    block = [lines[i]]
    for line in md_content.split("\n"):
        block.append(content_indent + line if line.strip() else "")
    return "\n".join(lines[:i] + block + lines[j:]) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    ap.add_argument("--repo", default=HERE, help="仓库根目录")
    args = ap.parse_args()

    bad = 0
    for md_rel, yml_rel in PAIRS:
        md_path = os.path.join(args.repo, md_rel)
        yml_path = os.path.join(args.repo, yml_rel)
        for p in (md_path, yml_path):
            if not os.path.exists(p):
                print(f"缺少文件: {p}", file=sys.stderr)
                return 2
        md_content = read(md_path)
        yml_text = read(yml_path)
        _, _, current = extract_yml_prefix(yml_text)
        want = md_content.rstrip("\n")
        if current == want:
            print(f"ok   {yml_rel} 的 persona 前缀与 {md_rel} 一致")
            continue
        bad += 1
        if args.check:
            print(f"FAIL {yml_rel} 的 persona 前缀与 {md_rel} 不一致")
            cur_lines = current.split("\n")
            want_lines = want.split("\n")
            for k in range(max(len(cur_lines), len(want_lines))):
                a = cur_lines[k] if k < len(cur_lines) else "<缺行>"
                b = want_lines[k] if k < len(want_lines) else "<缺行>"
                if a != b:
                    print(f"     第 {k+1} 行不同：")
                    print(f"       yml: {a[:100]}")
                    print(f"       md : {b[:100]}")
                    break
        else:
            new_text = render(yml_text, md_content)
            with open(yml_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new_text)
            print(f"同步 {md_rel} -> {yml_rel}")

    if args.check:
        return 0 if bad == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
