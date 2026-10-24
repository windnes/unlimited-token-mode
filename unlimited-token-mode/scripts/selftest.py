#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— unlimited_lint.py 的自检。

独立运行: python scripts/selftest.py
为每个用例生成临时 fixture，断言退出码与关键输出，逐例打印 ok/FAIL，
任一用例失败则以非零码退出。
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINT = HERE / "unlimited_lint.py"

GOOD_DECISION = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 删除 /var/tmp/build-cache 下超过 7 天的缓存目录
- 受影响实体: ci-runner-03 上的 build-cache（唯一使用方：nightly-build 流水线）
- 触发规则: R-07 磁盘水位 > 85% 时清理陈旧缓存（推动 disk_freed_mb 计数）
- 该规则读取的量: du -sb 的 7 天前分片大小
- 我核对过的量: find -mtime +7 -type d | wc -l -> 4，且 lsof 无占用
- 二阶: 4 个目录消失 -> 释放 2.1GB -> 水位从 91% 降到 84%
- 三阶: nightly-build 下次需全量重建约 6 分钟，但 7 天内未引用这些目录，故无额外成本
- 不可逆性: 不可逆（删除），但对象是缓存，撤销成本 = 一次全量构建
- 反事实: 不作为 -> 水位涨到 95%，夜间构建 ENOSPC 失败；只删 2 个 -> 仍高于阈值
- 决定: act —— 核对量与规则读取量一致，撤销成本远低于不作为的失败成本
"""

BAD_DECISION_MISSING = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 删除生产缓存目录
- 受影响实体: 系统
- 触发规则: R-07
- 决定: act
"""

BAD_DECISION_PLACEHOLDER = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: <我即将做什么>
- 受影响实体: {{实体}}
- 触发规则: R-07
- 该规则读取的量: 磁盘水位
- 我核对过的量: 磁盘水位
- 二阶: 释放空间
- 三阶: 构建变慢
- 不可逆性: 不可逆
- 反事实: 不做事
- 决定: act
"""

BAD_DECISION_VERDICT = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 对 production 执行 DROP COLUMN
- 受影响实体: orders 表
- 触发规则: R-11
- 该规则读取的量: deprecated_since
- 我核对过的量: deprecated_since = 114 天
- 二阶: billing-worker 报错
- 三阶: 结算积压
- 不可逆性: 不可逆
- 反事实: 先 SET UNUSED
- 决定: 再看看
"""

GOOD_REPORT = """# 运行报告

## 1. 做了什么
补齐 deploy.sh 的 --dry-run 分支。

## 2. 完成判据 -> 验证命令 -> 真实输出
C1: 三次 /healthz 均返回 200，输出 200/200/200。

## 3. 未验证项与原因
无。

## 4. 残留风险与假设清单
连接池在峰值下可能耗尽。

## 5. 歧义决议回顾
"上线"选定为 staging。

## 6. 预算使用情况
工具调用 42 次，无上限。
"""

BAD_REPORT = """# 运行报告

## 1. 做了什么
写了一些代码。
"""

GOOD_BRIEF = """# 任务契约书

## 1. 目标
让 staging 的 /healthz 连续 3 次返回 200。

## 2. 完成判据
| C1 | 3 次 200 | curl | 200 | agent |

## 3. 歧义决议表
"上线" 选定 staging。

## 5. 状态来源
state/run.json
"""

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def run_lint(path, *extra):
    proc = subprocess.run(
        [sys.executable, str(LINT), str(path)] + list(extra),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_fixture(tmpdir, name, content):
    path = Path(tmpdir) / name
    path.write_text(content, encoding="utf-8")
    return path


@case("good decision -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "good-decision.md", GOOD_DECISION)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert out.strip().splitlines()[-1] == "PASS", "末行应为 PASS，实际：%s" % out


@case("bad decision (missing keys) -> exit 1")
def _(tmpdir):
    p = write_fixture(tmpdir, "bad-missing.md", BAD_DECISION_MISSING)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 1, "期望 exit 1，实际 %d" % code
    assert "缺少栏位" in out, "应报缺失栏位，实际：%s" % out
    assert "FAIL" in out.splitlines()[-1], "末行应含 FAIL，实际：%s" % out


@case("bad decision (placeholder) -> exit 1")
def _(tmpdir):
    p = write_fixture(tmpdir, "bad-placeholder.md", BAD_DECISION_PLACEHOLDER)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 1, "期望 exit 1，实际 %d" % code
    assert "占位符" in out, "应报占位符，实际：%s" % out


@case("bad decision (verdict not in enum) -> exit 1")
def _(tmpdir):
    p = write_fixture(tmpdir, "bad-verdict.md", BAD_DECISION_VERDICT)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 1, "期望 exit 1，实际 %d" % code
    assert "act-with-mitigation" in out, "应提示合法取值，实际：%s" % out


@case("good report -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "good-report.md", GOOD_REPORT)
    code, out, _ = run_lint(p, "--kind", "report")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert out.strip().splitlines()[-1] == "PASS", "末行应为 PASS"


@case("bad report (missing sections) -> exit 1")
def _(tmpdir):
    p = write_fixture(tmpdir, "bad-report.md", BAD_REPORT)
    code, out, _ = run_lint(p, "--kind", "report")
    assert code == 1, "期望 exit 1，实际 %d" % code
    assert "缺少小节" in out, "应报缺失小节，实际：%s" % out


@case("good brief -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "good-brief.md", GOOD_BRIEF)
    code, out, _ = run_lint(p, "--kind", "brief")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)


@case("auto-detect report without --kind")
def _(tmpdir):
    p = write_fixture(tmpdir, "auto-report.md", GOOD_REPORT)
    code, out, _ = run_lint(p)
    assert code == 0, "自动识别应为 report 且通过，实际 %d；输出：%s" % (code, out)


@case("auto-detect decision without --kind")
def _(tmpdir):
    p = write_fixture(tmpdir, "auto-decision.md", GOOD_DECISION)
    code, out, _ = run_lint(p)
    assert code == 0, "自动识别应为 decision 且通过，实际 %d；输出：%s" % (code, out)


@case("fenced template block is ignored")
def _(tmpdir):
    body = GOOD_DECISION + "\n```\n## 决策 <n> @ <time>\n- 动作: <what>\n```\n"
    p = write_fixture(tmpdir, "fenced.md", body)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 0, "围栏内的占位块不应参与校验，实际 %d；输出：%s" % (code, out)


@case("--json output is valid JSON")
def _(tmpdir):
    p = write_fixture(tmpdir, "json-decision.md", GOOD_DECISION)
    code, out, _ = run_lint(p, "--kind", "decision", "--json")
    assert code == 0, "期望 exit 0，实际 %d" % code
    data = json.loads(out)
    assert data["ok"] is True, "ok 应为 true，实际：%r" % data
    assert data["kind"] == "decision", "kind 应为 decision，实际：%r" % data
    assert data["summary"] == "PASS", "summary 应为 PASS，实际：%r" % data


@case("missing file -> exit 2")
def _(tmpdir):
    code, _, err = run_lint(Path(tmpdir) / "nope.md")
    assert code == 2, "期望 exit 2，实际 %d" % code
    assert "不存在" in err, "stderr 应说明文件不存在，实际：%s" % err


@case("bad --kind -> exit 2")
def _(tmpdir):
    p = write_fixture(tmpdir, "any.md", GOOD_DECISION)
    code, _, _ = run_lint(p, "--kind", "nonsense")
    assert code == 2, "期望 exit 2，实际 %d" % code


# ---- 语义检查：假核对 / 实体不具体 / 推演过简 --------------------------------

FAKE_CHECK = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 触发封存
- 受影响实体: 即将开出的盲盒 #14
- 触发规则: 每退 3 次货，下一个盒子被封存
- 该规则读取的量: 退货次数 % 3
- 我核对过的量: 钱包余额 23 元，充足
- 二阶: 盒子 #14 强制留下，它是生活用品，永久占掉一个名额
- 三阶: 只剩 4 个名额可选高热量，总分上限下降约 600
- 不可逆性: 不可逆（封存不可退）
- 反事实: 不退这次货 -> 保留名额，代价是多花 1 次抽盒机会
- 决定: act
"""

VAGUE_ENTITY = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 重启
- 受影响实体: 系统
- 触发规则: R-01 健康检查失败
- 该规则读取的量: 健康检查失败次数
- 我核对过的量: 健康检查失败次数 = 3
- 二阶: 服务短暂不可用，约 5 秒
- 三阶: 上游重试，无永久影响
- 不可逆性: 可逆
- 反事实: 不重启 -> 持续 500
- 决定: act
"""

SHALLOW_DERIVATION = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 删除缓存
- 受影响实体: ci-runner-03 的 build-cache
- 触发规则: R-07 磁盘水位
- 该规则读取的量: 缓存目录大小
- 我核对过的量: 缓存目录大小 2.1GB
- 二阶: 没事
- 三阶: 无
- 不可逆性: 不可逆
- 反事实: 不删 -> 水位继续涨
- 决定: act
"""


@case("fake check -> warning by default, still exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "fake-check.md", FAKE_CHECK)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 0, "软警告默认不应改变退出码，实际 %d；输出：%s" % (code, out)
    assert "假核对" in out, "应提示假核对，实际：%s" % out


@case("fake check --strict -> exit 1")
def _(tmpdir):
    p = write_fixture(tmpdir, "fake-check-strict.md", FAKE_CHECK)
    code, out, _ = run_lint(p, "--kind", "decision", "--strict")
    assert code == 1, "严格模式下假核对应失败，实际 %d；输出：%s" % (code, out)
    assert "假核对" in out, "应提示假核对，实际：%s" % out


@case("vague entity --strict -> exit 1")
def _(tmpdir):
    p = write_fixture(tmpdir, "vague-entity.md", VAGUE_ENTITY)
    code, out, _ = run_lint(p, "--kind", "decision", "--strict")
    assert code == 1, "严格模式下笼统实体应失败，实际 %d；输出：%s" % (code, out)
    assert "没有点名" in out, "应提示未点名实体，实际：%s" % out


@case("shallow derivation --strict -> exit 1")
def _(tmpdir):
    p = write_fixture(tmpdir, "shallow.md", SHALLOW_DERIVATION)
    code, out, _ = run_lint(p, "--kind", "decision", "--strict")
    assert code == 1, "严格模式下推演过简应失败，实际 %d；输出：%s" % (code, out)
    assert "过于简略" in out, "应提示推演过简，实际：%s" % out


@case("good decision passes --strict too")
def _(tmpdir):
    p = write_fixture(tmpdir, "good-strict.md", GOOD_DECISION)
    code, out, _ = run_lint(p, "--kind", "decision", "--strict")
    assert code == 0, "正确的决策块在严格模式下也应通过，实际 %d；输出：%s" % (code, out)


@case("--json exposes warnings separately")
def _(tmpdir):
    p = write_fixture(tmpdir, "json-warn.md", FAKE_CHECK)
    code, out, _ = run_lint(p, "--kind", "decision", "--json")
    data = json.loads(out)
    assert code == 0, "软警告下 --json 仍应 exit 0，实际 %d" % code
    assert data["ok"] is True, "ok 应为 true，实际：%r" % data
    assert data["problems"] == [], "problems 应为空，实际：%r" % data["problems"]
    assert data["warnings"], "warnings 不应为空，实际：%r" % data["warnings"]


def main():
    if not LINT.exists():
        print("FAIL 找不到 unlimited_lint.py：%s" % LINT)
        return 1

    failures = 0
    with tempfile.TemporaryDirectory(prefix="contract-lint-selftest-") as tmpdir:
        for name, fn in CASES:
            try:
                fn(tmpdir)
            except AssertionError as exc:
                failures += 1
                print("FAIL %s" % name)
                print("     %s" % exc)
            except Exception as exc:  # noqa: BLE001 - 自检要报告任何异常
                failures += 1
                print("FAIL %s" % name)
                print("     %s: %s" % (type(exc).__name__, exc))
            else:
                print("ok   %s" % name)

    total = len(CASES)
    if failures:
        print("\n%d/%d 用例失败" % (failures, total))
        return 1
    print("\n全部 %d 个用例通过" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
