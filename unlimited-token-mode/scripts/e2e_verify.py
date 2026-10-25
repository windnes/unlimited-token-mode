#!/usr/bin/env python3
"""端到端验收：把真实 DSH 会话产出的账本与报告喂给 lint，验证两层证据。

这不是合成用例。账本 `.utc/ledger.jsonl` 是 `plugins/utc-ledger.mjs`
在真实 dsh 会话里旁听 tool/call 与 tool/result 落下的原始记录，
`run-report.md` 是那个会话里模型自己写的。

用法：
    python e2e_verify.py <session_dir>            # 只跑正向（诚实产物必须 PASS）
    python e2e_verify.py <session_dir> --negative # 另跑负向（篡改后必须被抓）

session_dir 里应有 run-report.md 与 .utc/ledger.jsonl。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(HERE, "unlimited_lint.py")


def run(report: str, kind: str, ledger: str | None) -> tuple[int, str]:
    cmd = [sys.executable, LINT, report, "--kind", kind]
    if ledger:
        cmd += ["--ledger", ledger]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def make_forged(report_text: str, ledger_path: str) -> tuple[str, list[str]]:
    """把报告里的「真实输出」篡改成编造的数值，记录改了哪些。"""
    records = []
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    real_outputs = [r.get("output", "") for r in records if r.get("kind") == "result"]

    # 挑一个「报告里写了、账本里也真有」的数字，把它改成 +1000。
    # 不能只从账本里挑最常见的数字：账本里最多的是路径里的 10304，
    # 它在报告里根本不出现，改了也没用。要从报告 §2 表格的输出格里找。
    ledger_numbers: set[str] = set()
    for out in real_outputs:
        ledger_numbers.update(re.findall(r"(?<![\d.])\d{1,6}(?![\d.])", out))

    # 只在 §2 表格的「真实输出」格里改。改正文里第一次出现的那个数字没用——
    # 校验器只比对表格行，改了正文等于什么都没改，测试会假装失败。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import utc_core
    except Exception:
        utc_core = None

    forged = report_text
    changed: list[str] = []
    cells: list[str] = []
    if utc_core is not None:
        try:
            body = utc_core.strip_fences(report_text)
            sec = utc_core.section_body(body, r"(完成判据|验证命令|真实输出)")
            for row in utc_core.parse_table_rows(sec):
                if len(row) < 4 or utc_core.is_table_header(row):
                    continue
                cells.append(row[3])
        except Exception:
            cells = []

    for cell in cells:
        for raw in re.findall(r"(?<![\d.])(\d{2,6})(?![\d.])", cell):
            if raw not in ledger_numbers:
                continue
            fake = str(int(raw) + 1000)
            # 在整篇报告里定位这个单元格原文，只替换它
            if cell in forged:
                new_cell = re.sub(rf"(?<![\d.]){raw}(?![\d.])", fake, cell, count=1)
                if new_cell != cell:
                    forged = forged.replace(cell, new_cell, 1)
                    changed.append(f"{raw} -> {fake}")
                    return forged, changed
    return forged, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session_dir")
    ap.add_argument("--negative", action="store_true")
    args = ap.parse_args()

    sdir = os.path.abspath(args.session_dir)
    report = os.path.join(sdir, "run-report.md")
    ledger = os.path.join(sdir, ".utc", "ledger.jsonl")
    for p in (report, ledger):
        if not os.path.exists(p):
            print(f"缺少文件: {p}")
            return 2

    results: list[tuple[str, bool, str]] = []
    boundaries: list[tuple[bool, str]] = []

    # ---- 正向 1：真实报告 + 真实账本 -> 必须 PASS ----
    rc, detail = run(report, "report", ledger)
    ok = rc == 0 and "Traceback" not in detail
    results.append(("真实报告 + 真实账本（必须 PASS）", ok, detail))

    # ---- 正向 2：真实报告 + 不给账本 -> 也必须 PASS（只是降档） ----
    rc2, detail2 = run(report, "report", None)
    ok2 = rc2 == 0 and "Traceback" not in detail2
    results.append(("真实报告 + 无账本（降档，仍须 PASS）", ok2, detail2))

    if args.negative:
        work = tempfile.mkdtemp(prefix="utc-e2e-")
        try:
            with open(report, encoding="utf-8") as fh:
                text = fh.read()

            # ---- 负向 1：篡改输出数值 -> 账本比对必须抓出 ----
            forged, changed = make_forged(text, ledger)
            fpath = os.path.join(work, "forged.md")
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(forged)
            rc3, detail3 = run(fpath, "report", ledger)
            ok3 = rc3 == 1 and ("LEDGER-OUTPUT-MISMATCH" in detail3 or "LEDGER-NO-CALL" in detail3)
            results.append((f"篡改真实输出的数字（{', '.join(changed) or '无可用数字'}）-> 必须被抓",
                            ok3, detail3))

            # ---- 负向 2：把验证命令换成一条没跑过的 -> 账本必须没有记录 ----
            # 不能硬编码 "python .\\sum_data.py"：真实报告里的命令写法随会话而变
            # （`python sum_data.py` / `.\sum_data.py` / 带引号 / 换行续行）。
            # 这里换掉脚本名本身——它是这条命令的身份锚点，换掉就是另一条命令。
            fake_cmd = re.sub(r"\bsum_data\.py\b", "does_not_exist_script.py", text)
            if fake_cmd == text:
                print("（跳过负向 2：报告里找不到可替换的脚本名）")
            else:
                fpath2 = os.path.join(work, "fakecmd.md")
                with open(fpath2, "w", encoding="utf-8") as fh:
                    fh.write(fake_cmd)
                rc4, detail4 = run(fpath2, "report", ledger)
                ok4 = rc4 == 1 and ("LEDGER-NO-CALL" in detail4 or "LEDGER-OUTPUT-MISMATCH" in detail4)
                results.append(("换掉验证命令为从未执行过的命令 -> 必须被抓", ok4, detail4))

                # ---- 边界记录（不计入通过数）：抽掉账本后，形态检查识别不了这条伪造。
                #      这不是缺陷隐瞒，而是 shape-only 档位的固有上限：
                #      没有运行时记录，任何校验器都只能看"长得像不像"，不能证明"跑没跑过"。
                rc5, detail5 = run(fpath2, "report", None)
                boundaries.append((rc5 != 0, detail5))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print("=" * 72)
    passed = 0
    for label, ok, detail in results:
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {label}")
        if not ok:
            print(detail.rstrip()[:1200])
            print("-" * 72)
        else:
            passed += 1
    print("=" * 72)
    print(f"{passed}/{len(results)} 通过")
    if boundaries:
        caught = sum(1 for ok, _ in boundaries if ok)
        print(f"边界记录: 无账本时降档检查抓到了 {caught}/{len(boundaries)} 条伪造"
              f"（该档位的固有上限，预期抓不到）")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
