#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_docs.py —— 校验「文档里写的数字」与「实际跑出来的结果」是否一致。

这个脚本存在的理由和整个项目一致：文档里的数字如果没人核对，就会漂。
v1 的 README 曾经把 upstream 明确判定为「不足以归因」的结论写成 confirmed，
而没有任何东西会因此报错——因为没人把文档里的数字变成断言。

它检查四类事：
    1. 两份 utc_core.py 逐字节相同（文档声称过）
    2. prompt-prefix*.md 与 cordis-preset*.yml 的 persona.prefix 一致（文档声称过）
    3. 文档里声明的自测项数 == selftest 实际报出的项数
    4. 文档里声明的对抗集用例数 == adversarial_suite 实际报出的数量

用法：
    python check_docs.py            # 全部检查，失败返回 1
    python check_docs.py -v         # 同时打印每项的实际值
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

STD_SCRIPTS = os.path.join(HERE, "unlimited-token-mode", "scripts")
PTC_SCRIPTS = os.path.join(HERE, "unlimited-token-ptc", "scripts")
README = os.path.join(HERE, "README.md")

passed = 0
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"ok   {name}")
    else:
        failed.append(name)
        print(f"FAIL {name}")
        if detail:
            for line in detail.strip().splitlines()[:8]:
                print(f"     {line}")


def run(script: str) -> str:
    proc = subprocess.run([sys.executable, script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    readme = open(README, encoding="utf-8").read()

    # ---------- 1. 两份 core 一致 ----------
    a = os.path.join(STD_SCRIPTS, "utc_core.py")
    b = os.path.join(PTC_SCRIPTS, "utc_core.py")
    h1 = hashlib.sha256(open(a, "rb").read()).hexdigest()
    h2 = hashlib.sha256(open(b, "rb").read()).hexdigest()
    check("两份 utc_core.py 逐字节相同", h1 == h2, f"std {h1[:16]}…\nptc {h2[:16]}…")

    # ---------- 2. prompt-prefix 与 preset 的 persona.prefix 一致 ----------
    proc = subprocess.run([sys.executable, os.path.join(HERE, "sync_prefix.py"), "--check"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("prompt-prefix*.md 与 preset persona.prefix 一致",
          proc.returncode == 0, (proc.stdout or "") + (proc.stderr or ""))

    # ---------- 3. 自测项数与文档一致 ----------
    std_out = run(os.path.join(STD_SCRIPTS, "selftest.py"))
    m = re.search(r"自测:\s*(\d+)/(\d+)\s*通过", std_out)
    std_total = int(m.group(2)) if m else -1
    std_pass = int(m.group(1)) if m else -1
    # README 里英文写 "# 15 checks"，中文写 "（15 项）"
    claims = re.findall(r"selftest\.py\s*#\s*(\d+) checks", readme)
    claim = int(claims[0]) if claims else -1
    ok = std_pass == std_total and std_total > 0 and (claim == -1 or claim == std_total)
    check(f"标准版自测 {std_pass}/{std_total} 全过，且与 README 声称的 {claim} 项一致",
          ok, std_out[-800:])

    ptc_out = run(os.path.join(PTC_SCRIPTS, "selftest.py"))
    m2 = re.search(r"自测:\s*(\d+)/(\d+)\s*通过", ptc_out)
    ptc_total = int(m2.group(2)) if m2 else -1
    ptc_pass = int(m2.group(1)) if m2 else -1
    claims2 = re.findall(r"selftest\.py\s*#\s*(\d+) checks", readme)
    claim2 = int(claims2[1]) if len(claims2) > 1 else -1
    ok2 = ptc_pass == ptc_total and ptc_total > 0 and (claim2 == -1 or claim2 == ptc_total)
    check(f"PTC 版自测 {ptc_pass}/{ptc_total} 全过，且与 README 声称的 {claim2} 项一致",
          ok2, ptc_out[-800:])

    # ---------- 4. 对抗集用例数与文档一致 ----------
    adv_out = run(os.path.join(STD_SCRIPTS, "adversarial_suite.py"))
    m3 = re.search(r"对抗集:\s*(\d+)/(\d+)\s*通过", adv_out)
    adv_pass = int(m3.group(1)) if m3 else -1
    adv_total = int(m3.group(2)) if m3 else -1
    case_claim = re.findall(r"# (\d+) cases \(17 attacks \+ 6 honest\)", readme)
    cases = int(case_claim[0]) if case_claim else -1
    ok3 = (adv_pass == adv_total and adv_total > 0
           and cases > 0 and adv_total == cases * 2)
    check(f"对抗集 {adv_pass}/{adv_total} 全过，README 声称 {cases} 例 × 2 档",
          ok3, adv_out[-600:])

    if args.verbose:
        print()
        print(f"标准版自测 {std_pass}/{std_total}")
        print(f"PTC 版自测 {ptc_pass}/{ptc_total}")
        print(f"对抗集 {adv_pass}/{adv_total}")

    print()
    total = passed + len(failed)
    print(f"文档一致性: {passed}/{total} 通过")
    if failed:
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
