#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— ptc_lint.py 的自检。

独立运行: python scripts/selftest.py
为每个用例生成临时 fixture，断言退出码与关键输出片段，逐例打印 ok/FAIL，
任一用例失败则以非零码退出，末行打印实际用例数汇总。

覆盖四类 kind 的正反例、`--strict` 三条 program 规则与 decision 计数矛盾、
以及 detect_kind 的自动探测。

fixture 组织方式:
    GOOD_* 常量是能通过的完整产物；
    程序块与决策块的负例由 drop_fields / replace_once 从 GOOD_* 派生，
    每个派生只改一处，失败原因因此唯一。
"""

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINT = HERE / "ptc_lint.py"

# ---- program 正例 ----------------------------------------------------------

GOOD_PROGRAM = """# 程序账本

## 程序 1 @ 2026-10-25T13:05:00Z
- 程序 id: P1
- description: 校验部署脚本的干跑输出与退出码
- 意图: 确认 deploy.sh 的 --dry-run 分支不产生副作用
- 不可逆动作: 无
- 状态读写: 无
- 证据:
  ```text
  $ bash scripts/deploy.sh --dry-run
  exit=0
  dry-run: 3 changes planned, 0 applied
  ```
- 判决: pass
- 可失败性: 若 --dry-run 分支未生效，输出里的 applied 计数会变成 3 而不是 0
"""

# 有不可逆动作时，同文件必须存在一个决策块（规则 5 的正例）
DECISION_TAIL = """
## 决策 1 @ 2026-10-25T13:04:00Z
- 动作: 写入 state/run.json 的 _rev
- 受影响实体: state/run.json
- 触发规则: R-02 每轮开始写 _rev
- 该规则读取的量: _rev 当前值
- 我核对过的量: cat state/run.json 读到 _rev 7
- 二阶: _rev 变成 8，下一轮读到 8
- 三阶: 旧的 _rev 7 持有者重读，不会合并
- 不可逆性: 可逆（改写回 7 即可）
- 反事实: 不写则两轮共用 _rev 7，并发无法检测
- 决定: act —— 核对值与文件一致
"""

GOOD_PROGRAM_IRREVERSIBLE = (
    GOOD_PROGRAM.replace(
        "- 不可逆动作: 无", "- 不可逆动作: state/run.json 写入，_rev 7 → 8"
    )
    + DECISION_TAIL
)

# ---- decision 正例（十一栏 + PTC 新增三栏） --------------------------------

GOOD_DECISION = """# 决策日志

## 决策 1 @ 2026-10-24T14:20:11Z
- 动作: 删除 /var/tmp/build-cache 下超过 7 天的缓存目录
- 受影响实体: ci-runner-03 上的 build-cache（唯一使用方：nightly-build 流水线）
- 触发规则: R-07 磁盘水位高于 85% 时清理陈旧缓存（推动 disk_freed_mb 计数）
- 该规则读取的量: du -sb 的 7 天前分片大小
- 我核对过的量: find -mtime +7 -type d 得到 4 个目录，且 lsof 无占用
- 二阶: 4 个目录消失，ci-runner-03 释放 2.1GB，水位从 91% 降到 84%
- 三阶: nightly-build 下次全量重建约 6 分钟，但 7 天内未引用这些目录
- 不可逆性: 不可逆（删除），撤销成本等于一次全量构建
- 反事实: 不作为则水位涨到 95%，夜间构建因 ENOSPC 失败
- 决定: act —— 核对量与规则读取量一致
- 程序 id: P1
- description: 删除过期构建缓存并释放磁盘水位
- 程序内不可逆动作数: 1
"""

# ---- brief 正例 ------------------------------------------------------------

GOOD_BRIEF = """# 任务契约书

## 1. 目标
让 staging 的 /healthz 连续 3 次返回 200。

## 2. 完成判据
| C1 | 3 次 200 | curl | 200 | agent |

## 3. 歧义决议表
"上线" 选定 staging。

## 5. 状态来源
state/run.json

## 6. 程序预算与并发
timeoutMs 600000；只读调用可 Promise.all 并发，上限 maxPendingCalls 128；写调用串行。
"""

# ---- report 正例 -----------------------------------------------------------

GOOD_REPORT = """# 运行报告

## 1. 做了什么
补齐 deploy.sh 的 --dry-run 分支。

## 2. 验证：判据 → 程序 → 真实输出
| # | 程序 id | 判据 | 验证命令 | 真实输出 | 结果 |
| --- | --- | --- | --- | --- | --- |
| C1 | P1 | 3 次 200 | curl | 200/200/200 | PASS |

## 3. 未验证
无。

## 4. 残留风险
连接池在峰值下可能耗尽。

## 5. 假设
orders 表本轮不会被并发写入。
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


def drop_fields(text, keys):
    """删掉以 `- <key>:` 开头的栏位行，用于构造缺栏位负例。"""
    out = []
    for line in text.splitlines():
        if any(line.startswith("- %s:" % key) for key in keys):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def replace_once(text, old, new):
    """只替换一处；old 不存在直接断言失败，避免 fixture 悄悄失效。"""
    assert old in text, "fixture 里找不到要替换的片段：%r" % old
    return text.replace(old, new, 1)


def last_line(out):
    return out.strip().splitlines()[-1]


# ---- program: 正例 ---------------------------------------------------------

@case("program 九字段齐全 -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "program-good.md", GOOD_PROGRAM)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert last_line(out) == "11 passed, 0 failed, 0 warnings", "末行应为 11 passed, 0 failed, 0 warnings，实际：%s" % last_line(out)


@case("program 正例在 --strict 下也通过")
def _(tmpdir):
    p = write_fixture(tmpdir, "program-good-strict.md", GOOD_PROGRAM)
    code, out, _ = run_lint(p, "--kind", "program", "--strict")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert "0 failed, 0 warnings" in last_line(out), "末行应无 failed 与 warnings，实际：%s" % last_line(out)


@case("program 有不可逆动作且有决策块 -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "program-irrev-ok.md", GOOD_PROGRAM_IRREVERSIBLE)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert "同文件有 1 个决策块" in out, "应确认决策块存在，实际：%s" % out


# ---- program: 多程序块 -----------------------------------------------------

@case("program 两个程序块各自校验、各自计行 -> exit 0")
def _(tmpdir):
    second = GOOD_PROGRAM.split("## 程序 1 @", 1)[1]
    second = "## 程序 2 @" + second.replace("P1", "P2").replace("deploy.sh", "smoke.sh")
    body = GOOD_PROGRAM + "\n" + second
    p = write_fixture(tmpdir, "program-two-blocks.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 0, "两个合法程序块应通过，实际 %d；输出：%s" % (code, out)
    assert "共 2 个程序块" in out, "应识别 2 个程序块，实际：%s" % out
    assert "程序块 2 @L" in out, "应对第 2 块单独出检查行，实际：%s" % out


@case("program 两块中第二块有缺陷 -> exit 1 且定位到第 2 块")
def _(tmpdir):
    second = GOOD_PROGRAM.split("## 程序 1 @", 1)[1]
    second = "## 程序 2 @" + second.replace("P1", "P2")
    second = second.replace("  exit=0\n", "")
    p = write_fixture(tmpdir, "program-second-bad.md", GOOD_PROGRAM + "\n" + second)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "程序块 2 @L" in out and "缺 `exit=` 行" in out, "应定位到第 2 块，实际：%s" % out


# ---- program: 规则 1（九字段） ---------------------------------------------

@case("program 缺栏位（九字段不全）-> exit 1")
def _(tmpdir):
    body = drop_fields(GOOD_PROGRAM, ["状态读写"])
    p = write_fixture(tmpdir, "program-missing-field.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "九字段齐全" in out and "`状态读写`" in out, "应报缺「状态读写」，实际：%s" % out


@case("program 栏位为空（九字段不全）-> exit 1")
def _(tmpdir):
    body = replace_once(GOOD_PROGRAM, "- 程序 id: P1", "- 程序 id:")
    p = write_fixture(tmpdir, "program-empty-field.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "九字段齐全" in out, "应报九字段不全，实际：%s" % out


# ---- program: 规则 2（证据块格式） -----------------------------------------

@case("program 证据块缺 exit= 行 -> exit 1")
def _(tmpdir):
    body = replace_once(GOOD_PROGRAM, "  exit=0\n", "")
    p = write_fixture(tmpdir, "program-no-exit.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "缺 `exit=` 行" in out, "应报缺 exit= 行，实际：%s" % out


@case("program 证据块缺 $ 调用行 -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM, "  $ bash scripts/deploy.sh --dry-run\n", "  bash scripts/deploy.sh --dry-run\n"
    )
    p = write_fixture(tmpdir, "program-no-call.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "缺以 `$ ` 开头的调用行" in out, "应报缺 $ 调用行，实际：%s" % out


# ---- program: 规则 3（pass 的可失败性） ------------------------------------

@case("program pass 但缺「可失败性」-> exit 1")
def _(tmpdir):
    body = drop_fields(GOOD_PROGRAM, ["可失败性"])
    p = write_fixture(tmpdir, "program-no-failability.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "「可失败性」为空" in out, "应报可失败性为空，实际：%s" % out


@case("program pass 的可失败性写成「无」-> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM,
        "- 可失败性: 若 --dry-run 分支未生效，输出里的 applied 计数会变成 3 而不是 0",
        "- 可失败性: 无",
    )
    p = write_fixture(tmpdir, "program-failability-none.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "pass 必须给出会变红的具体条件" in out, "应报可失败性无效，实际：%s" % out


# ---- program: 规则 4（无证据的通过） ---------------------------------------

@case("program pass 但证据块无 exit=0 也无数字 -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM,
        "  exit=0\n  dry-run: 3 changes planned, 0 applied\n",
        "  exit=nonzero\n  dry-run: no changes applied\n",
    )
    p = write_fixture(tmpdir, "program-no-evidence.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "无证据的通过" in out, "应报无证据的通过，实际：%s" % out


@case("program 只有 $ 调用行含数字、输出行无数字 -> exit 1")
def _(tmpdir):
    # 规则 4 的「任何数字」只认证据输出行：`$ ` 调用行里的端口号不算证据。
    body = replace_once(
        GOOD_PROGRAM,
        "  $ bash scripts/deploy.sh --dry-run\n  exit=0\n  dry-run: 3 changes planned, 0 applied\n",
        "  $ curl -s -o /dev/null -w '%{http_code}' localhost:8080/healthz\n"
        "  exit=ok\n  success\n",
    )
    p = write_fixture(tmpdir, "program-callline-digits-only.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "无证据的通过" in out, "调用行的数字不算证据，实际：%s" % out


@case("program pass 且证据块有数字但非 exit=0 -> exit 0")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM, "  exit=0\n", "  exit=2\n"
    )
    p = write_fixture(tmpdir, "program-exit2-with-digits.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 0, "有数字输出应满足规则 4，实际 %d；输出：%s" % (code, out)


# ---- program: 规则 5（不可逆动作需决策块） ---------------------------------

@case("program 有不可逆动作但无决策块 -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM, "- 不可逆动作: 无", "- 不可逆动作: state/run.json 写入，_rev 7 → 8"
    )
    p = write_fixture(tmpdir, "program-irrev-no-decision.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "没有 `## 决策` 块" in out, "应报缺决策块，实际：%s" % out


# ---- program: 规则 6（禁用措辞） -------------------------------------------

@case("program 出现禁用措辞 -> exit 1")
def _(tmpdir):
    body = GOOD_PROGRAM + "\n备注：基本完成，后续再补证据。\n"
    p = write_fixture(tmpdir, "program-forbidden.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "禁用措辞" in out and "基本完成" in out, "应报禁用措辞，实际：%s" % out


# ---- program: 规则 7（--strict 三条） --------------------------------------

@case("program description 过短：默认 WARN 且 exit 0")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM, "- description: 校验部署脚本的干跑输出与退出码", "- description: 校验"
    )
    p = write_fixture(tmpdir, "program-desc-short.md", body)
    code, out, _ = run_lint(p, "--kind", "program")
    assert code == 0, "WARN 不应改变退出码，实际 %d；输出：%s" % (code, out)
    assert "WARN  程序块 1" in out and "description 长度" in out, "应报 description 长度 WARN，实际：%s" % out


@case("program description 过短 + --strict -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM, "- description: 校验部署脚本的干跑输出与退出码", "- description: 校验"
    )
    p = write_fixture(tmpdir, "program-desc-short-strict.md", body)
    code, out, _ = run_lint(p, "--kind", "program", "--strict")
    assert code == 1, "严格模式下应失败，实际 %d；输出：%s" % (code, out)
    assert "description 长度" in out and "超出" in out, "应报长度越界，实际：%s" % out


@case("program 意图含多意图词 + --strict -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM,
        "- 意图: 确认 deploy.sh 的 --dry-run 分支不产生副作用",
        "- 意图: 确认 deploy.sh 的 --dry-run 分支不产生副作用和状态漂移",
    )
    p = write_fixture(tmpdir, "program-multi-intent.md", body)
    code, out, _ = run_lint(p, "--kind", "program", "--strict")
    assert code == 1, "严格模式下应失败，实际 %d；输出：%s" % (code, out)
    assert "多意图词" in out, "应报多意图，实际：%s" % out


@case("program 证据块 catch 无 throw + --strict -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM,
        "  dry-run: 3 changes planned, 0 applied\n",
        "  catch: 1 error suppressed\n",
    )
    p = write_fixture(tmpdir, "program-catch.md", body)
    code, out, _ = run_lint(p, "--kind", "program", "--strict")
    assert code == 1, "严格模式下应失败，实际 %d；输出：%s" % (code, out)
    assert "疑似吞异常" in out, "应报疑似吞异常，实际：%s" % out


@case("program 证据块 catch 带 throw -> exit 0")
def _(tmpdir):
    body = replace_once(
        GOOD_PROGRAM,
        "  dry-run: 3 changes planned, 0 applied\n",
        "  catch: 1 error, rethrow via throw\n",
    )
    p = write_fixture(tmpdir, "program-catch-throw.md", body)
    code, out, _ = run_lint(p, "--kind", "program", "--strict")
    assert code == 0, "catch 后重抛不算吞异常，实际 %d；输出：%s" % (code, out)


# ---- decision --------------------------------------------------------------

@case("decision 十一栏 + 新增三栏齐全 -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "decision-good.md", GOOD_DECISION)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert last_line(out) == "5 passed, 0 failed, 0 warnings", "末行应为 5 passed, 0 failed, 0 warnings，实际：%s" % last_line(out)


@case("decision 缺新增三栏 -> exit 1")
def _(tmpdir):
    body = drop_fields(GOOD_DECISION, ["程序 id", "description", "程序内不可逆动作数"])
    p = write_fixture(tmpdir, "decision-no-ptc.md", body)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "新增三栏" in out, "应报缺新增三栏，实际：%s" % out


@case("decision 缺标准栏位（十一栏不全）-> exit 1")
def _(tmpdir):
    body = drop_fields(GOOD_DECISION, ["二阶", "三阶"])
    p = write_fixture(tmpdir, "decision-missing-keys.md", body)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "十一栏齐全" in out and "`二阶`" in out, "应报缺二阶，实际：%s" % out


@case("decision 计数与动作矛盾：默认 WARN 且 exit 0")
def _(tmpdir):
    body = replace_once(
        GOOD_DECISION,
        "- 动作: 删除 /var/tmp/build-cache 下超过 7 天的缓存目录",
        "- 动作: 删除 /var/tmp/build-cache 并写入 state/run.json 记录 _rev",
    )
    p = write_fixture(tmpdir, "decision-count-mismatch.md", body)
    code, out, _ = run_lint(p, "--kind", "decision")
    assert code == 0, "WARN 不应改变退出码，实际 %d；输出：%s" % (code, out)
    assert "P5 捆绑不可逆" in out, "应报计数矛盾，实际：%s" % out


@case("decision 计数与动作矛盾 + --strict -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_DECISION,
        "- 动作: 删除 /var/tmp/build-cache 下超过 7 天的缓存目录",
        "- 动作: 删除 /var/tmp/build-cache 并写入 state/run.json 记录 _rev",
    )
    p = write_fixture(tmpdir, "decision-count-mismatch-strict.md", body)
    code, out, _ = run_lint(p, "--kind", "decision", "--strict")
    assert code == 1, "严格模式下应失败，实际 %d；输出：%s" % (code, out)
    assert "计数与动作一致" in out, "应报计数矛盾，实际：%s" % out


# ---- brief -----------------------------------------------------------------

@case("brief 必需节齐全 -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "brief-good.md", GOOD_BRIEF)
    code, out, _ = run_lint(p, "--kind", "brief")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert last_line(out) == "2 passed, 0 failed, 0 warnings", "末行应为 2 passed, 0 failed, 0 warnings，实际：%s" % last_line(out)


@case("brief 缺「程序预算与并发」节 -> exit 1")
def _(tmpdir):
    body = GOOD_BRIEF.split("## 6. 程序预算与并发")[0]
    p = write_fixture(tmpdir, "brief-no-budget.md", body)
    code, out, _ = run_lint(p, "--kind", "brief")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "程序预算与并发" in out, "应报缺程序预算与并发，实际：%s" % out


@case("brief 有未填的 {{...}} -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_BRIEF, "让 staging 的 /healthz 连续 3 次返回 200。", "{{目标：动词 + 对象 + 可测量的终态}}"
    )
    p = write_fixture(tmpdir, "brief-placeholder.md", body)
    code, out, _ = run_lint(p, "--kind", "brief")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "无未填占位符" in out, "应报未填占位符，实际：%s" % out


# ---- report ----------------------------------------------------------------

@case("report 必需节 + 程序 id 列齐全 -> exit 0")
def _(tmpdir):
    p = write_fixture(tmpdir, "report-good.md", GOOD_REPORT)
    code, out, _ = run_lint(p, "--kind", "report")
    assert code == 0, "期望 exit 0，实际 %d；输出：%s" % (code, out)
    assert last_line(out) == "3 passed, 0 failed, 0 warnings", "末行应为 3 passed, 0 failed, 0 warnings，实际：%s" % last_line(out)


@case("report §2 表格缺程序 id 列 -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_REPORT,
        "| # | 程序 id | 判据 | 验证命令 | 真实输出 | 结果 |",
        "| # | 判据 | 验证命令 | 真实输出 | 结果 |",
    )
    p = write_fixture(tmpdir, "report-no-program-id.md", body)
    code, out, _ = run_lint(p, "--kind", "report")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "§2 含程序 id 列" in out, "应报缺程序 id 列，实际：%s" % out


@case("report 出现禁用措辞 -> exit 1")
def _(tmpdir):
    body = replace_once(
        GOOD_REPORT, "补齐 deploy.sh 的 --dry-run 分支。", "补齐 deploy.sh 的 --dry-run 分支，基本完成。"
    )
    p = write_fixture(tmpdir, "report-forbidden.md", body)
    code, out, _ = run_lint(p, "--kind", "report")
    assert code == 1, "期望 exit 1，实际 %d；输出：%s" % (code, out)
    assert "禁用措辞" in out, "应报禁用措辞，实际：%s" % out


# ---- detect_kind -----------------------------------------------------------

@case("自动探测 program")
def _(tmpdir):
    p = write_fixture(tmpdir, "auto-program.md", GOOD_PROGRAM)
    code, out, _ = run_lint(p)
    assert code == 0, "自动识别应为 program 且通过，实际 %d；输出：%s" % (code, out)
    assert "kind: program（自动识别）" in out, "应识别为 program，实际：%s" % out


@case("自动探测 decision")
def _(tmpdir):
    p = write_fixture(tmpdir, "auto-decision.md", GOOD_DECISION)
    code, out, _ = run_lint(p)
    assert code == 0, "自动识别应为 decision 且通过，实际 %d；输出：%s" % (code, out)
    assert "kind: decision（自动识别）" in out, "应识别为 decision，实际：%s" % out


@case("自动探测 report")
def _(tmpdir):
    p = write_fixture(tmpdir, "auto-report.md", GOOD_REPORT)
    code, out, _ = run_lint(p)
    assert code == 0, "自动识别应为 report 且通过，实际 %d；输出：%s" % (code, out)
    assert "kind: report（自动识别）" in out, "应识别为 report，实际：%s" % out


@case("自动探测 brief")
def _(tmpdir):
    p = write_fixture(tmpdir, "auto-brief.md", GOOD_BRIEF)
    code, out, _ = run_lint(p)
    assert code == 0, "自动识别应为 brief 且通过，实际 %d；输出：%s" % (code, out)
    assert "kind: brief（自动识别）" in out, "应识别为 brief，实际：%s" % out


# ---- 用法错误 --------------------------------------------------------------

@case("文件不存在 -> exit 2")
def _(tmpdir):
    code, _, err = run_lint(Path(tmpdir) / "nope.md")
    assert code == 2, "期望 exit 2，实际 %d" % code
    assert "不存在" in err, "stderr 应说明文件不存在，实际：%s" % err


@case("--kind 取值非法 -> exit 2")
def _(tmpdir):
    p = write_fixture(tmpdir, "any.md", GOOD_PROGRAM)
    code, _, _ = run_lint(p, "--kind", "nonsense")
    assert code == 2, "期望 exit 2，实际 %d" % code


def main():
    if not LINT.exists():
        print("FAIL 找不到 ptc_lint.py：%s" % LINT)
        return 1

    failures = 0
    with tempfile.TemporaryDirectory(prefix="ptc-lint-selftest-") as tmpdir:
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
    print("\n%d/%d 用例通过" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
