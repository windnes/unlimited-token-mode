#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无限token标准版 —— 自测（v2）。

三层结构，每层回答一个不同的问题：
    1. 判定引擎对每一种攻击的反应      -> 调 adversarial_suite.py（23 例 × 默认/严格）
    2. 两份 utc_core.py 是否真的相同    -> sha256 比对
    3. CLI 自身的契约（编码、退出码）    -> 单独用例

关键纪律（v1 在这里栽过）：退出码 1 不等于"抓到了"。
v1 的 19/19 "FAIL" 实际是沙箱 ACL 让 fixture 写不进去，测试根本没跑起来。
所以本文件每条用例都检查"是不是真判了"，而不是只看 returncode。
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
LINT = os.path.join(HERE, "unlimited_lint.py")
CORE = os.path.join(HERE, "utc_core.py")
ADV = os.path.join(HERE, "adversarial_suite.py")

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


def main() -> int:
    work = tempfile.mkdtemp(prefix="utc-selftest-")
    try:
        # ---------------- 1. 对抗集 ----------------
        print("--- 1. 对抗集（攻击必须被抓 / 诚实必须放行）---")
        proc = subprocess.run([sys.executable, ADV], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        adv_out = (proc.stdout or "") + (proc.stderr or "")
        check("对抗集全部通过", proc.returncode == 0, adv_out[-1500:])

        # ---------------- 2. 两份 core 一致 ----------------
        print("--- 2. 两份判定引擎必须逐字节相同 ---")
        ptc_core = os.path.normpath(
            os.path.join(HERE, "..", "..", "unlimited-token-ptc", "scripts", "utc_core.py")
        )
        if os.path.exists(ptc_core):
            h1 = hashlib.sha256(open(CORE, "rb").read()).hexdigest()
            h2 = hashlib.sha256(open(ptc_core, "rb").read()).hexdigest()
            check("utc_core.py 两份拷贝一致", h1 == h2,
                  f"标准版 {h1[:16]}…\nPTC 版 {h2[:16]}…")
        else:
            check("找到 PTC 版 utc_core.py", False, f"未找到 {ptc_core}")

        # ---------------- 3. 编码鲁棒性 ----------------
        print("--- 3. 编码：任何输入都不能让校验器崩溃 ---")
        good = """# 运行报告

## 1. 做了什么
- 测试

## 2. 完成判据 → 验证命令 → 真实输出

| # | 判据 | 验证命令 | 真实输出 | 结果 |
| --- | --- | --- | --- | --- |
| C1 | 服务启动 | `curl -s localhost:8080/healthz` | 200 | PASS |

**为什么这条检查能失败**：服务没起来会连接被拒。

## 3. 未验证项与原因

| 项 | 为什么没验证 | 影响 | 谁来补 |
| --- | --- | --- | --- |

## 4. 残留风险与假设
无。

## 7. 结论
达到目标。
"""
        for enc, label in (
            ("utf-8", "utf-8"),
            ("utf-8-sig", "utf-8 BOM"),
            ("gb18030", "GBK/GB18030"),
            ("utf-16", "UTF-16"),
        ):
            p = os.path.join(work, f"enc-{label.replace(' ', '_').replace('/', '_')}.md")
            with open(p, "w", encoding=enc) as fh:
                fh.write(good)
            rc, out = run_lint(p, "--kind", "report")
            check(f"{label} 报告可校验且通过", rc == 0, out)

        p = os.path.join(work, "binary.bin")
        with open(p, "wb") as fh:
            fh.write(b"\x00\x01\x02\xff\xfe\x00\x00binary garbage" * 20)
        rc, out = run_lint(p, "--kind", "report")
        check("二进制输入 -> exit 2 且不崩溃", rc == 2, out)

        p = os.path.join(work, "empty.md")
        open(p, "w").close()
        rc, out = run_lint(p, "--kind", "decision")
        check("空文件 -> 正常判定（不崩溃）", rc in (0, 1), out)

        rc, out = run_lint(os.path.join(work, "nope.md"), "--kind", "brief")
        check("文件不存在 -> exit 2", rc == 2, out)

        p = os.path.join(work, "big.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# 运行报告\n\n" + ("## 填充\n\n" + "内容 " * 200 + "\n\n") * 900)
        rc, out = run_lint(p, "--kind", "report")
        check("1MB 大文件不崩溃", rc in (0, 1), out)

        # ---------------- 4. 围栏隔离 ----------------
        print("--- 4. 围栏里的示例不得被当成真实留档 ---")
        p = os.path.join(work, "fenced.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("""# 决策日志

下面这段是文档，不是我的真实留档：

```markdown
## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 删除 /var/tmp/build-cache
- 受影响实体: ci-runner-03 的 build-cache
- 触发规则: R-07
- 该规则读取的量: 7 天前分片大小
- 我核对过的量: `find /var/tmp -mtime +7 | wc -l` → 4
- 二阶: 4 个目录消失 -> 释放 2.1GB
- 三阶: nightly-build 下次重建约 6 分钟
- 不可逆性: 不可逆，撤销成本 = 一次全量构建
- 反事实: 什么都不做 -> 磁盘涨到 95%
- 决定: act
```
""")
        rc, out = run_lint(p, "--kind", "decision")
        check("只有围栏内示例 -> 判失败（DEC-NONE）", rc == 1 and "DEC-NONE" in out, out)

        # ---------------- 5. 账本优先于词元启发式 ----------------
        print("--- 5. 有账本时不得再用词元启发式判失败 ---")
        ledger = os.path.join(work, "ledger.jsonl")

        def write_ledger(command: str, output: str) -> None:
            with open(ledger, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "kind": "result", "t": "2026-10-25T01:00:00Z", "seq": 1,
                    "session": "s", "callId": "c1", "tool": "pwsh",
                    "command": command, "target": "",
                    "exit": 0, "error": False, "output": output,
                }, ensure_ascii=False) + "\n")

        p = os.path.join(work, "ledger-ok.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("""## 决策 1 @ 2026-10-25T01:00:00Z
- 动作: 停止向 /data/logs 写入新日志
- 受影响实体: api-gateway 的 /data/logs 分区
- 触发规则: R-12 磁盘水位 > 90%
- 该规则读取的量: 磁盘水位百分比
- 我核对过的量: `df -h /data` → 93% /data
- 二阶: 93% 高于 90% → api-gateway 写入失败 → 约 1200 QPS 返回 507
- 三阶: 上游 lb-01 收到 507 → 熔断 → 订单服务延迟升至 8 秒
- 不可逆性: 可逆（停止写入可随时恢复）
- 反事实: 什么都不做 → /data 约 40 分钟写满，API 全面 507
- 决定: act-with-mitigation —— 先转存再停止写入，并已配置告警
""")
        write_ledger("df -h /data", "93%\t/data\n[exit code: 0]")
        rc, out = run_lint(p, "--kind", "decision", "--ledger", ledger, "--strict")
        check("账本印证的真实核对 + --strict 必须通过", rc == 0, out)

        write_ledger("free -m", "Mem: 1000\n")
        rc, out = run_lint(p, "--kind", "decision", "--ledger", ledger)
        check("账本里没有这次调用 -> 必须失败", rc == 1 and "LEDGER-NO-CALL" in out, out)

        # ---------------- 6. 退出码语义 ----------------
        print("--- 6. 退出码 ---")
        rc, out = run_lint(os.path.join(work, "empty.md"), "--kind", "nonsense")
        check("非法 --kind -> exit 2", rc == 2, out)
        rc, out = run_lint(os.path.join(work, "enc-utf-8.md"), "--kind", "report", "--json")
        parsed = None
        try:
            parsed = json.loads(out.strip())
        except Exception:
            pass
        check("--json 输出可被解析", isinstance(parsed, dict) and "evidence_tier" in parsed, out)

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
