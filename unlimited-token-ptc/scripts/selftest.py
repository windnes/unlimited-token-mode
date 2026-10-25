#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无限token PTC 版 —— 自测（v2）。

PTC 版与标准版共用同一套判定引擎（scripts/utc_core.py，两份逐字节相同），
所以这里不重复标准版的全部用例，只覆盖 PTC 特有的部分：

    1. 共享 core 的 sha256 与标准版一致
    2. program 类型（PTC 特有）
    3. 走 ptc_lint.py 入口的对抗集（同一批用例经由 PTC CLI）
    4. 编码 / 围栏 / 账本 / 退出码的 PTC 侧回归

标准版的完整测试在 ../unlimited-token-mode/scripts/selftest.py。

关键纪律（v1 在这里栽过）：退出码 1 不等于"抓到了"。
校验器自己崩溃也会返回非零，所以每个用例都要把 traceback 排除在外。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(HERE, "ptc_lint.py")
CORE = os.path.join(HERE, "utc_core.py")
STD_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "unlimited-token-mode", "scripts"))

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
            for line in detail.strip().splitlines()[:12]:
                print(f"     {line}")


def run_lint(path: str, *extra: str) -> tuple[int, str]:
    cmd = [sys.executable, LINT, path, *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if "Traceback (most recent call last)" in out:
        return -1, out
    return proc.returncode, out


GOOD_PROGRAM = """## 程序 P1 @ 2026-10-25T01:00:00Z
- 程序: 统计 data 目录下 11 个文件的数值总和
- 意图: 验证计数由脚本完成，不在思考里数
- 不可逆动作: 无
- 状态读写: 写 state/aggregate.json
- 证据: `python scripts/sum.py data` exit=0，输出 `files=11 total=530`
- 判决: pass
- 可失败性: 若漏读文件则 total != 530；若把不存在的 f07 计入则 total 变成 582
"""

BAD_PROGRAM_EXIT_ONLY = """## 程序 P1 @ 2026-10-25T01:00:00Z
- 程序: 清理临时目录
- 意图: 释放磁盘空间
- 不可逆动作: 删除 /var/tmp/build-cache
- 状态读写: 读写 state/run.json
- 证据: `rm -rf /var/tmp/build-cache` exit=0
- 判决: pass
- 可失败性: 若目标不存在则 rm 返回 exit=1
"""


def main() -> int:
    work = tempfile.mkdtemp(prefix="utc-ptc-selftest-")
    try:
        # ---------------- 1. 共享 core ----------------
        print("--- 1. 共享判定引擎必须与标准版逐字节相同 ---")
        std_core = os.path.join(STD_DIR, "utc_core.py")
        if os.path.exists(std_core):
            h1 = hashlib.sha256(open(CORE, "rb").read()).hexdigest()
            h2 = hashlib.sha256(open(std_core, "rb").read()).hexdigest()
            check("utc_core.py 与标准版一致", h1 == h2, f"PTC {h1[:16]}…\n标准 {h2[:16]}…")
        else:
            check("找到标准版 utc_core.py", False, f"未找到 {std_core}")

        # ---------------- 2. program 类型 ----------------
        print("--- 2. program 类型（PTC 特有）---")
        p = os.path.join(work, "good-program.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(GOOD_PROGRAM)
        rc, out = run_lint(p, "--kind", "program")
        check("诚实的程序日志通过", rc == 0, out)

        p = os.path.join(work, "bad-program.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(BAD_PROGRAM_EXIT_ONLY)
        rc, out = run_lint(p, "--kind", "program")
        check("只有 exit=0 没有输出 -> 必须失败", rc == 1 and "PRG-EVIDENCE-VALUE" in out, out)

        rc, out = run_lint(os.path.join(work, "good-program.md"))
        check("program 能被自动探测", rc == 0 and "program" in out, out)

        p = os.path.join(work, "no-program.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# 随便写点\n\n没有程序块。\n")
        rc, out = run_lint(p, "--kind", "program")
        check("缺少程序块 -> 判失败", rc == 1 and "PRG-NONE" in out, out)

        # ---------------- 3. 经 PTC CLI 的对抗集 ----------------
        print("--- 3. 同一批对抗用例经由 ptc_lint.py ---")
        std_adv = os.path.join(STD_DIR, "adversarial_suite.py")
        if os.path.exists(std_adv):
            env = dict(os.environ)
            env["UTC_LINT"] = LINT
            proc = subprocess.run(
                [sys.executable, std_adv],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
            )
            adv_out = (proc.stdout or "") + (proc.stderr or "")
            check("对抗集经 PTC CLI 同样全部通过", proc.returncode == 0, adv_out[-1500:])
        else:
            check("找到对抗集脚本", False, f"未找到 {std_adv}")

        # ---------------- 4. 回归 ----------------
        print("--- 4. 编码 / 围栏 / 账本 / 退出码 ---")
        ledger = os.path.join(work, "ledger.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "kind": "result", "t": "2026-10-25T01:00:00Z", "seq": 1,
                "session": "s", "callId": "c1", "tool": "pwsh",
                "command": "python scripts/sum.py data", "target": "",
                "exit": 0, "error": False,
                "output": "files=11 total=530\n[exit code: 0]",
            }, ensure_ascii=False) + "\n")
        rc, out = run_lint(os.path.join(work, "good-program.md"), "--kind", "program",
                           "--ledger", ledger)
        check("账本印证的 program 通过（ledger 档）", rc == 0 and "ledger" in out, out)

        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "kind": "result", "t": "2026-10-25T01:00:00Z", "seq": 1,
                "session": "s", "callId": "c1", "tool": "pwsh",
                "command": "echo hi", "target": "",
                "exit": 0, "error": False, "output": "hi\n",
            }, ensure_ascii=False) + "\n")
        rc, out = run_lint(os.path.join(work, "good-program.md"), "--kind", "program",
                           "--ledger", ledger)
        check("账本里没有这次调用 -> program 也必须失败",
              rc == 1 and "LEDGER-NO-CALL" in out, out)

        p = os.path.join(work, "gbk.md")
        with open(p, "w", encoding="gb18030") as fh:
            fh.write(GOOD_PROGRAM)
        rc, out = run_lint(p, "--kind", "program")
        check("GBK 程序日志可校验", rc == 0, out)

        p = os.path.join(work, "bin.bin")
        with open(p, "wb") as fh:
            fh.write(b"\x00\xff" * 500)
        rc, out = run_lint(p, "--kind", "program")
        check("二进制输入 -> exit 2", rc == 2, out)

        p = os.path.join(work, "fenced.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# 文档\n\n```markdown\n" + GOOD_PROGRAM + "```\n")
        rc, out = run_lint(p, "--kind", "program")
        check("围栏内程序块 -> 判失败", rc == 1 and "PRG-NONE" in out, out)

        rc, out = run_lint(os.path.join(work, "good-program.md"), "--kind", "nonsense")
        check("非法 --kind -> exit 2", rc == 2, out)

        rc, out = run_lint(os.path.join(work, "good-program.md"), "--kind", "program", "--json")
        parsed = None
        try:
            parsed = json.loads(out.strip())
        except Exception:
            pass
        check("--json 可解析", isinstance(parsed, dict) and "evidence_tier" in parsed, out)

    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    total = passed + len(failed)
    print(f"自测: {passed}/{total} 通过")
    if failed:
        print("失败项:")
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
