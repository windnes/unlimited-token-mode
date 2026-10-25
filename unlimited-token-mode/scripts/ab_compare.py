"""对照实验：把同一组对抗用例同时喂给 v1 与 v2 的 lint。

为什么要做这个：如果只有 v2 跑过这批用例，那"23/23 通过"只证明 v2 自洽，
不证明它比 v1 强。真正的证据是**同一批输入、两个版本、结果的差**。
v1 的 lint 从 git tag v1-backup-e9f2687 里取出，未做任何修改。

用法：
    python ab_compare.py <v1_scripts_dir>
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import adversarial_suite as adv  # noqa: E402


def run_lint(lint_path: str, path: str, kind: str, ledger: str | None, strict: bool) -> tuple[int, str]:
    cmd = [sys.executable, lint_path, path, "--kind", kind]
    if ledger:
        cmd += ["--ledger", ledger]
    if strict:
        cmd.append("--strict")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    detail = (proc.stdout or "") + (proc.stderr or "")
    if "Traceback (most recent call last)" in detail:
        return -1, detail
    return proc.returncode, detail


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python ab_compare.py <v1_scripts_dir>")
        return 2
    v1_dir = os.path.abspath(sys.argv[1])
    v1_lint = os.path.join(v1_dir, "unlimited_lint.py")
    v2_lint = os.path.join(HERE, "unlimited_lint.py")
    if not os.path.exists(v1_lint):
        print(f"找不到 v1 lint: {v1_lint}")
        return 2

    workdir = tempfile.mkdtemp(prefix="utc-ab-")
    cases = adv.ATTACKS + adv.HONEST
    rows = []
    try:
        for case in cases:
            path = os.path.join(workdir, case["id"] + ".md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(case["text"])
            ledger = None
            if case["with_ledger"]:
                ledger = os.path.join(workdir, case["id"] + ".ledger.jsonl")
                with open(ledger, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(adv.LEDGER_CALL, ensure_ascii=False) + "\n")
                    fh.write(json.dumps(adv.LEDGER_FIXTURE, ensure_ascii=False) + "\n")

            kind = case["kind"]
            # v1 没有 program 类型，PTC 用例用 v1 的 ptc_lint 跑
            if kind == "program":
                v1_ptc = os.path.join(os.path.dirname(v1_dir), "unlimited-token-ptc", "scripts", "ptc_lint.py")
                v1_lint_for_case = v1_ptc if os.path.exists(v1_ptc) else v1_lint
            else:
                v1_lint_for_case = v1_lint

            # 每个版本都跑默认与 --strict：v1 的误杀发生在 --strict 下，
            # 只比默认模式会把这个差异抹掉。
            v1_rc, v1_detail = run_lint(v1_lint_for_case, path, kind, None, False)
            v1s_rc, _ = run_lint(v1_lint_for_case, path, kind, None, True)
            v2_rc, v2_detail = run_lint(v2_lint, path, kind, ledger, False)
            v2s_rc, _ = run_lint(v2_lint, path, kind, ledger, True)

            def verdict(rc: int, expect: int) -> str:
                if rc == -1:
                    return "崩溃"
                got_fail = rc == 1
                want_fail = expect == 1
                if got_fail == want_fail:
                    return "正确"
                return "漏放" if want_fail else "误杀"

            def verdict2(rc_default: int, rc_strict: int, expect: int) -> str:
                """两档都要判对才算正确；只对一档算半对，这里按最差计。"""
                a = verdict(rc_default, expect)
                b = verdict(rc_strict, expect)
                if a == "正确" and b == "正确":
                    return "正确"
                if a == "崩溃" or b == "崩溃":
                    return "崩溃"
                # 优先报误杀，因为它比漏放更伤信任
                for v in (b, a):
                    if v == "误杀":
                        return "误杀"
                return "漏放"

            rows.append({
                "id": case["id"],
                "expect": "FAIL" if case["expect"] == 1 else "PASS",
                "v1": verdict2(v1_rc, v1s_rc, case["expect"]),
                "v2": verdict2(v2_rc, v2s_rc, case["expect"]),
                "v1_default": verdict(v1_rc, case["expect"]),
                "v1_strict": verdict(v1s_rc, case["expect"]),
                "v2_default": verdict(v2_rc, case["expect"]),
                "v2_strict": verdict(v2s_rc, case["expect"]),
            })
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # ---- 输出 ----
    print(f"{'用例':<8} {'期望':<6} {'v1':<6} {'v2':<6}")
    print("-" * 32)
    for r in rows:
        print(f"{r['id']:<8} {r['expect']:<6} {r['v1']:<6} {r['v2']:<6}")

    v1_ok = sum(1 for r in rows if r["v1"] == "正确")
    v2_ok = sum(1 for r in rows if r["v2"] == "正确")
    v1_leak = sum(1 for r in rows if r["v1"] == "漏放")
    v1_kill = sum(1 for r in rows if r["v1"] == "误杀")
    v1_crash = sum(1 for r in rows if r["v1"] == "崩溃")
    v2_leak = sum(1 for r in rows if r["v2"] == "漏放")
    v2_kill = sum(1 for r in rows if r["v2"] == "误杀")
    v2_crash = sum(1 for r in rows if r["v2"] == "崩溃")

    print()
    print(f"总计 {len(rows)} 例")
    print(f"  v1: 正确 {v1_ok} | 漏放 {v1_leak} | 误杀 {v1_kill} | 崩溃 {v1_crash}")
    print(f"  v2: 正确 {v2_ok} | 漏放 {v2_leak} | 误杀 {v2_kill} | 崩溃 {v2_crash}")
    print()
    if v2_ok > v1_ok:
        print(f"结论: v2 相对 v1 多判对 {v2_ok - v1_ok} 例。")
    if v1_leak:
        print(f"v1 漏放（把攻击当合法）: {', '.join(r['id'] for r in rows if r['v1'] == '漏放')}")
    if v1_kill:
        print(f"v1 误杀（把诚实当攻击）: {', '.join(r['id'] for r in rows if r['v1'] == '误杀')}")

    # 写成 json 便于其他脚本消费
    out = os.path.join(HERE, "..", "ab_compare_result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print(f"明细已写入 {os.path.normpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
