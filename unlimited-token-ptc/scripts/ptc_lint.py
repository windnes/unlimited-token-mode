#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ptc_lint.py —— 校验 无限tokenPTC版 的四类产物（program 账本 / 决策日志 / 任务契约书 / 运行报告）。

用途
    把 无限tokenPTC版 的四种 markdown 产物压成一组可判定的检查。
    每条检查输出一行 `PASS` / `FAIL` / `WARN` 加原因，最后一行输出
    `N passed, M failed, K warnings`。

四类 kind
    program   —— 程序账本（spec §6）。逐字实现 7 条 lint 硬规则：
                 1. 九字段齐全（标题行 `## 程序 <n> @ <ISO 时间>` + 8 个栏位）
                 2. `证据` 代码块内必须有 `exit=` 行与一行以 `$ ` 开头的调用行
                 3. `判决: pass` 时 `可失败性` 不得为空，且不得是「无」「N/A」「-」「不会失败」
                 4. `判决: pass` 且证据块内既无 `exit=0` 也无任何数字 -> FAIL（无证据的通过）
                 5. `不可逆动作` 不是「无」时，同文件内必须存在至少一个 `## 决策` 块
                 6. 全文件禁用措辞（6 条，见 FORBIDDEN_PHRASES）
                 7. `--strict` 额外三条：`description` 长度越界 / `意图` 含多意图词 /
                    证据块含 `catch` 且同块无 `throw` / `return {`（疑似吞异常 P1）
    decision  —— 决策日志：标准版十一栏（标题行 `## 决策 <n> @ <time>` + 10 栏）
                 加 PTC 新增三栏（`程序 id` / `description` / `程序内不可逆动作数`），
                 并检查「程序内不可逆动作数」与「动作」栏是否矛盾。
    brief     —— 任务契约书：必需节标题（含 `程序预算与并发`）+ `{{...}}` 未填。
    report    —— 运行报告：必需节 + §2 表格含 `程序 id` 列 + 禁用措辞。

用法
    python ptc_lint.py <path> [--kind brief|report|decision|program] [--strict]

    `--kind` 省略时按内容自动探测；探测不出时按 decision 处理。
    `--strict` 把 strict 规则从 WARN 升级为 FAIL。

退出码
    0  全部检查通过（WARN 不影响退出码）
    1  至少一条检查 FAIL
    2  用法错误（文件不存在 / 不是文件 / 无法读取 / `--kind` 取值非法）

两条实现说明（都为了守住规则本意，不是放宽）
    a. 围栏代码块不参与「节标题 / 栏位 / 占位符」类结构校验，模板里的格式示例因此不会误报。
       例外一：program 的 `证据` 块本身就在围栏里，规则 2 与规则 4 直接解析它的内容。
       例外二：规则 6 写的是「全文件禁止出现」，所以禁用措辞扫描整份文件（含围栏内），
       不剥围栏 —— 引用禁用措辞的模板不适合用 --kind program 校验。
    b. 规则 4 的「任何数字」指证据输出行里的数字，不含 `$ ` 调用行与 `exit=` 行本身。
       规则 2 已经强制要求存在 `exit=` 行，而 `exit=<整数>` 自带数字；若不排除这两类行，
       规则 4 永远不可能触发，「无证据的通过」就再也抓不到。
"""

import argparse
import re
import sys
from pathlib import Path

# ---- 规则表 ----------------------------------------------------------------

# program 的九字段：标题行 + 下列 8 个栏位（spec §6 逐字）
PROGRAM_FIELDS = [
    "程序 id",
    "description",
    "意图",
    "不可逆动作",
    "状态读写",
    "证据",
    "判决",
    "可失败性",
]

# 标准版决策块十栏；加上标题行即「十一栏」
DECISION_KEYS = [
    "动作",
    "受影响实体",
    "触发规则",
    "该规则读取的量",
    "我核对过的量",
    "二阶",
    "三阶",
    "不可逆性",
    "反事实",
    "决定",
]

# PTC 新增三栏
DECISION_PTC_KEYS = ["程序 id", "description", "程序内不可逆动作数"]

DECISION_VALUES = {"act", "act-with-mitigation", "don't-act"}
VERDICT_VALUES = {"pass", "fail", "partial"}

# spec §6 规则 3 的占位取值
FAILABILITY_PLACEHOLDERS = {"无", "n/a", "na", "-", "—", "不会失败"}

# spec §6 规则 6 的禁用措辞。这六条是规则字面量，逐字取自 spec，必须原样保留。
FORBIDDEN_PHRASES = [
    "应该可以",
    "看起来没问题",
    "基本完成",
    "已修复（无输出）",
    "应该没问题",
    "大概没问题",
]

# spec §6 规则 7 的多意图词
MULTI_INTENT_MARKERS = ["和", "以及", "顺便", "同时"]

# 「动作」栏的动作词组：同一组内的词是同义词，只算一个动作。
ACTION_GROUPS = [
    ["删除", "删掉", "移除", "清空", "delete", "drop", "truncate", "remove"],
    ["覆盖", "写入", "改写", "overwrite", "write"],
    ["创建", "新建", "create"],
    ["迁移", "migrate"],
    ["重启", "停止", "restart", "stop"],
    ["部署", "发布", "推送", "deploy", "release", "push"],
    ["修改", "重命名", "变更", "modify", "rename"],
]

BRIEF_SECTIONS = ["目标", "完成判据", "歧义决议", "状态来源", "程序预算与并发"]
REPORT_SECTIONS = ["做了什么", "验证", "未验证", "残留风险", "假设"]

# 「验证」节的候选标题片段（用于定位 §2 表格）
REPORT_TABLE_MARKERS = ("验证", "完成判据", "证据")

# description 长度：中文按字数 8–40，英文按词数 5–10（spec §6）
DESC_CN_MIN, DESC_CN_MAX = 8, 40
DESC_WORD_MIN, DESC_WORD_MAX = 5, 10

# ---- 正则 ------------------------------------------------------------------

FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
PROGRAM_HEAD_RE = re.compile(r"^#{1,6}\s+程序\s*\d*\s*@")
DECISION_HEAD_RE = re.compile(r"^#{1,6}\s+决策\s*(?:#?\d+|@|<)")
DECISION_ANY_RE = re.compile(r"^#{1,6}\s+决策")
DECISION_BULLET_RE = re.compile(r"^\s*[-*]\s*决定\s*[:：]", re.M)
PROGRAM_ID_BULLET_RE = re.compile(r"^\s*[-*]\s*程序\s*id\s*[:：]", re.M)
BULLET_RE = re.compile(r"^\s*[-*]\s*([^:：]+?)\s*[:：]\s*(.*)$")
PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}|<[^<>\n]{1,80}>")
BRACE_PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}")
EXIT_RE = re.compile(r"\bexit\s*=")
EXIT_ZERO_RE = re.compile(r"\bexit\s*=\s*0\b")
DIGIT_RE = re.compile(r"\d")
INT_RE = re.compile(r"-?\d+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
FIRST_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
SPACE_RE = re.compile(r"\s+")


# ---- 通用工具 --------------------------------------------------------------

class Check(object):
    """一条检查结果。status 取 PASS / FAIL / WARN。"""

    __slots__ = ("status", "name", "detail")

    def __init__(self, status, name, detail=""):
        self.status = status
        self.name = name
        self.detail = detail


def ok(name, detail=""):
    return Check("PASS", name, detail)


def bad(name, detail):
    return Check("FAIL", name, detail)


def soft(name, detail, strict):
    """strict 规则：`--strict` 下算 FAIL，否则只报 WARN。"""
    return Check("FAIL" if strict else "WARN", name, detail)


def strip_fences(text):
    """把围栏代码块的内容替换为空行，保持行号不变。"""
    out = []
    inside = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def headings(plain):
    found = []
    for line in plain.splitlines():
        m = HEADING_RE.match(line)
        if m:
            found.append(m.group(2))
    return found


def is_blank(value):
    return value.strip(" \t\u3000—-·、;；,，") == ""


def normalize_value(value):
    return value.strip().strip("。.;；").strip().lower()


def normalize_parens(value):
    return value.replace("（", "(").replace("）", ")")


def iter_blocks(text, head_re):
    """按 head_re 切块，返回 [(标题行号, 标题行文本, [(行号, 行文本), ...])]。

    围栏内的标题不切块；围栏标记行本身保留在块体内，供 program 解析证据块。
    """
    blocks = []
    current = None
    in_fence = False
    for no, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            if current is not None:
                current[2].append((no, line))
            continue
        if not in_fence and HEADING_RE.match(line):
            if current is not None:
                blocks.append(current)
                current = None
            if head_re.match(line):
                current = (no, line, [])
            continue
        if current is not None:
            current[2].append((no, line))
    if current is not None:
        blocks.append(current)
    return blocks


def parse_bullets(block_lines):
    """解析 `- 键: 值` 并接续缩进续行。返回 {键: (行号, 值)}。"""
    items = {}
    last = None
    for no, line in block_lines:
        m = BULLET_RE.match(line)
        if m:
            last = m.group(1).strip()
            items[last] = (no, m.group(2).strip())
        elif last is not None and line.strip() and line[:1] in (" ", "\t"):
            prev_no, prev_val = items[last]
            items[last] = (prev_no, (prev_val + " " + line.strip()).strip())
        else:
            last = None
    return items


def parse_program(body):
    """解析一个程序块，返回 ({栏位: (行号, 值)}, [证据块内的行])。"""
    items = {}
    evidence = []
    ev_state = 0  # 0=没有证据块 1=等证据围栏 2=证据围栏内
    in_fence = False
    last = None
    for no, line in body:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            if ev_state == 1 and in_fence:
                ev_state = 2
            elif ev_state == 2 and not in_fence:
                ev_state = 0
                last = None
            continue
        if ev_state == 2:
            evidence.append(line)
            continue
        if in_fence:
            continue
        m = BULLET_RE.match(line)
        if m:
            key = m.group(1).strip()
            items[key] = (no, m.group(2).strip())
            last = key
            if key == "证据":
                ev_state = 1
            continue
        if last is not None and line.strip() and line[:1] in (" ", "\t"):
            prev_no, prev_val = items[last]
            items[last] = (prev_no, (prev_val + " " + line.strip()).strip())
        else:
            last = None
    return items, evidence


def desc_measure(value):
    """返回 (计数, 单位, 下限, 上限)。中文按字数，其他按词数。"""
    text = value.strip()
    cjk = len(CJK_RE.findall(text))
    visible = len(SPACE_RE.sub("", text))
    if cjk and cjk * 2 >= visible:
        return cjk, "字", DESC_CN_MIN, DESC_CN_MAX
    return len(WORD_RE.findall(text)), "词", DESC_WORD_MIN, DESC_WORD_MAX


def count_action_groups(action):
    """「动作」栏里命中了几个动作组（同义词只算一个）。"""
    lowered = action.lower()
    return sum(1 for group in ACTION_GROUPS if any(word in lowered for word in group))


def output_lines(evidence):
    """证据块里的「输出行」：去掉 `$ ` 调用行与 `exit=` 行。"""
    return [
        line
        for line in evidence
        if not line.lstrip().startswith("$ ") and not EXIT_RE.search(line)
    ]


def detect_kind(text):
    """按内容探测产物类型。探测不出时返回 decision。"""
    plain = strip_fences(text)
    if any(PROGRAM_HEAD_RE.match(line) for line in plain.splitlines()):
        return "program"
    if DECISION_HEAD_RE.search(plain) or DECISION_BULLET_RE.search(plain):
        return "decision"
    hs = headings(plain)
    if any("做了什么" in h for h in hs) and any("残留风险" in h for h in hs):
        return "report"
    if any("完成判据" in h for h in hs) and any("目标" in h for h in hs):
        return "brief"
    if PROGRAM_ID_BULLET_RE.search(plain):
        return "program"
    return "decision"


# ---- 共用检查：禁用措辞 ----------------------------------------------------

def check_forbidden(text):
    """spec §6 规则 6：全文件禁用措辞。规则写的是「全文件」，故不剥围栏。"""
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        norm = normalize_parens(line)
        for phrase in FORBIDDEN_PHRASES:
            if normalize_parens(phrase) in norm:
                hits.append("L%d「%s」" % (no, phrase))
    if hits:
        return bad("禁用措辞", "出现 %d 处：%s" % (len(hits), "；".join(hits[:5])))
    return ok("禁用措辞", "未出现 spec §6 列出的 6 条禁用措辞")


# ---- program ---------------------------------------------------------------

def check_program_block(index, head_no, head_line, items, evidence, decision_count, strict):
    """校验一个程序块，返回该块的检查列表。"""
    tag = "程序块 %d @L%d" % (index, head_no)
    checks = []

    # 规则 1：九字段齐全（标题行 + 8 栏位），缺一即 FAIL
    missing = []
    if not PROGRAM_HEAD_RE.match(head_line):
        missing.append("标题行 `## 程序 <n> @ <ISO 时间>`")
    for key in PROGRAM_FIELDS:
        if key not in items:
            missing.append("`%s`" % key)
            continue
        no, value = items[key]
        if key == "证据":
            if is_blank(value) and not any(line.strip() for line in evidence):
                missing.append("`证据`(L%d 为空)" % no)
        elif is_blank(value):
            missing.append("`%s`(L%d 为空)" % (key, no))
    if missing:
        checks.append(bad(tag + " 九字段齐全", "缺 %d 项：%s" % (len(missing), "、".join(missing))))
    else:
        checks.append(ok(tag + " 九字段齐全", "标题行 + 8 栏位均在"))

    # 规则 2：证据块含 `exit=` 行与以 `$ ` 开头的调用行
    lacks = []
    if not any(EXIT_RE.search(line) for line in evidence):
        lacks.append("缺 `exit=` 行")
    if not any(line.lstrip().startswith("$ ") for line in evidence):
        lacks.append("缺以 `$ ` 开头的调用行")
    if lacks:
        checks.append(bad(tag + " 证据块格式", "；".join(lacks)))
    else:
        checks.append(ok(tag + " 证据块格式", "`exit=` 行与 `$ ` 调用行均在"))

    # 判决取值（pass | fail | partial）
    verdict = None
    if "判决" in items:
        raw_verdict = normalize_value(items["判决"][1])
        m = FIRST_WORD_RE.search(raw_verdict)
        verdict = m.group(0).lower().replace("’", "'") if m else ""
        if verdict in VERDICT_VALUES:
            checks.append(ok(tag + " 判决取值", verdict))
        else:
            checks.append(
                bad(
                    tag + " 判决取值",
                    "「判决」只能是 pass | fail | partial，实际是：%s" % (raw_verdict[:40] or "空"),
                )
            )
    else:
        checks.append(bad(tag + " 判决取值", "缺「判决」栏，无法判定 pass / fail / partial"))

    if verdict == "pass":
        # 规则 3：pass 时「可失败性」不得为空、不得是占位取值
        fail_value = items["可失败性"][1] if "可失败性" in items else ""
        if is_blank(fail_value):
            checks.append(bad(tag + " pass 可失败性", "「判决: pass」但「可失败性」为空"))
        elif PLACEHOLDER_RE.search(fail_value):
            checks.append(bad(tag + " pass 可失败性", "「可失败性」仍是占位符：%s" % fail_value[:40]))
        elif normalize_value(fail_value) in FAILABILITY_PLACEHOLDERS:
            checks.append(
                bad(
                    tag + " pass 可失败性",
                    "「可失败性」写成「%s」——pass 必须给出会变红的具体条件" % fail_value[:20],
                )
            )
        else:
            checks.append(ok(tag + " pass 可失败性", "已给出会变红的具体条件"))

        # 规则 4：pass 且证据块既无 `exit=0` 也无任何数字 -> FAIL
        has_zero = any(EXIT_ZERO_RE.search(line) for line in evidence)
        has_digit = any(DIGIT_RE.search(line) for line in output_lines(evidence))
        if has_zero:
            checks.append(ok(tag + " pass 有数字证据", "证据块含 `exit=0`"))
        elif has_digit:
            checks.append(ok(tag + " pass 有数字证据", "证据输出行含数字"))
        else:
            checks.append(
                bad(
                    tag + " pass 有数字证据",
                    "「判决: pass」但证据块既无 `exit=0` 也无任何数字——无证据的通过",
                )
            )
    else:
        checks.append(ok(tag + " pass 可失败性", "判决为 %s，规则 3 不适用" % (verdict or "未知")))
        checks.append(ok(tag + " pass 有数字证据", "判决为 %s，规则 4 不适用" % (verdict or "未知")))

    # 规则 5：不可逆动作不是「无」时，同文件必须有 `## 决策` 块
    irreversible = items["不可逆动作"][1] if "不可逆动作" in items else ""
    if is_blank(irreversible):
        checks.append(ok(tag + " 不可逆动作需决策块", "「不可逆动作」为空，由九字段规则处理"))
    elif normalize_value(irreversible) in ("无", "none", "-", "—"):
        checks.append(ok(tag + " 不可逆动作需决策块", "「不可逆动作」为无"))
    elif decision_count > 0:
        checks.append(ok(tag + " 不可逆动作需决策块", "同文件有 %d 个决策块" % decision_count))
    else:
        checks.append(
            bad(
                tag + " 不可逆动作需决策块",
                "「不可逆动作」为「%s」，但同文件内没有 `## 决策` 块" % irreversible[:40],
            )
        )

    # 规则 7（--strict）：description 长度
    name = tag + " description 长度（--strict）"
    if "description" in items:
        desc = items["description"][1]
        count, unit, low, high = desc_measure(desc)
        if low <= count <= high:
            checks.append(ok(name, "%d %s，区间 %d–%d %s" % (count, unit, low, high, unit)))
        else:
            checks.append(
                soft(
                    name,
                    "%d %s，超出 %d–%d %s：%s" % (count, unit, low, high, unit, desc[:40]),
                    strict,
                )
            )
    else:
        checks.append(soft(name, "缺「description」栏，无法计数", strict))

    # 规则 7（--strict）：意图含多意图词
    name = tag + " 意图单一（--strict）"
    if "意图" in items:
        intent = items["意图"][1]
        hits = [marker for marker in MULTI_INTENT_MARKERS if marker in intent]
        if hits:
            checks.append(
                soft(name, "「意图」出现多意图词 %s：%s" % ("、".join(hits), intent[:40]), strict)
            )
        else:
            checks.append(ok(name, "无多意图词"))
    else:
        checks.append(soft(name, "缺「意图」栏", strict))

    # 规则 7（--strict）：疑似吞异常（P1）
    name = tag + " 无吞异常（--strict）"
    ev_text = "\n".join(evidence)
    if "catch" in ev_text:
        if "throw" in ev_text or "return {" in ev_text:
            checks.append(ok(name, "证据块含 catch，同时含 throw 或 `return {`"))
        else:
            checks.append(
                soft(name, "证据块含 `catch` 但同块无 `throw` / `return {`——疑似吞异常（P1）", strict)
            )
    else:
        checks.append(ok(name, "证据块无 catch"))

    return checks


def check_program(text, plain, strict):
    checks = []
    blocks = iter_blocks(text, PROGRAM_HEAD_RE)
    if blocks:
        checks.append(ok("找到程序块", "共 %d 个程序块" % len(blocks)))
    else:
        checks.append(bad("找到程序块", "未找到程序块：每个程序以 `## 程序 <n> @ <ISO 时间>` 开头"))
    decision_count = len(iter_blocks(plain, DECISION_ANY_RE))
    for index, (head_no, head_line, body) in enumerate(blocks, 1):
        items, evidence = parse_program(body)
        checks.extend(
            check_program_block(index, head_no, head_line, items, evidence, decision_count, strict)
        )
    checks.append(check_forbidden(text))
    return checks


# ---- decision --------------------------------------------------------------

def check_decision(plain, strict):
    checks = []
    blocks = iter_blocks(plain, DECISION_HEAD_RE)
    if blocks:
        checks.append(ok("找到决策块", "共 %d 个决策块" % len(blocks)))
    else:
        checks.append(bad("找到决策块", "未找到决策块：每个决策以 `## 决策 <n> @ <time>` 开头"))

    for index, (head_no, head_line, body) in enumerate(blocks, 1):
        items = parse_bullets(body)
        tag = "决策块 %d @L%d" % (index, head_no)

        # 标准版十一栏：标题行 + 10 栏
        missing = []
        if not DECISION_HEAD_RE.match(head_line):
            missing.append("标题行 `## 决策 <n> @ <time>`")
        for key in DECISION_KEYS:
            if key not in items:
                missing.append("`%s`" % key)
                continue
            no, value = items[key]
            if is_blank(value):
                missing.append("`%s`(L%d 为空)" % (key, no))
            elif PLACEHOLDER_RE.search(value):
                missing.append("`%s`(L%d 仍是占位符)" % (key, no))
        if missing:
            checks.append(bad(tag + " 十一栏齐全", "缺 %d 项：%s" % (len(missing), "、".join(missing))))
        else:
            checks.append(ok(tag + " 十一栏齐全", "标题行 + 10 栏均在"))

        # PTC 新增三栏
        missing = []
        for key in DECISION_PTC_KEYS:
            if key not in items:
                missing.append("`%s`" % key)
                continue
            no, value = items[key]
            if is_blank(value):
                missing.append("`%s`(L%d 为空)" % (key, no))
            elif PLACEHOLDER_RE.search(value):
                missing.append("`%s`(L%d 仍是占位符)" % (key, no))
        if missing:
            checks.append(bad(tag + " 新增三栏", "缺 %d 项：%s" % (len(missing), "、".join(missing))))
        else:
            checks.append(ok(tag + " 新增三栏", "程序 id / description / 程序内不可逆动作数 均在"))

        # 决定取值
        if "决定" in items:
            raw = normalize_value(items["决定"][1])
            m = FIRST_WORD_RE.search(raw)
            word = m.group(0).lower().replace("’", "'") if m else ""
            if word in DECISION_VALUES:
                checks.append(ok(tag + " 决定取值", word))
            else:
                checks.append(
                    bad(
                        tag + " 决定取值",
                        "「决定」只能是 act | act-with-mitigation | don't-act，实际是：%s"
                        % (raw[:40] or "空"),
                    )
                )
        else:
            checks.append(bad(tag + " 决定取值", "缺「决定」栏"))

        # 「程序内不可逆动作数」与「动作」栏的矛盾
        name = tag + " 计数与动作一致"
        if "动作" in items and "程序内不可逆动作数" in items:
            action = items["动作"][1]
            raw_count = items["程序内不可逆动作数"][1]
            m = INT_RE.search(raw_count)
            groups = count_action_groups(action)
            if not m:
                checks.append(
                    bad(name, "「程序内不可逆动作数」不是整数：%s" % (raw_count[:20] or "空"))
                )
            elif groups >= 2 and int(m.group(0)) == 1:
                checks.append(
                    soft(
                        name,
                        "「动作」含 %d 组动作词，但「程序内不可逆动作数」写 1——"
                        "多个动作挤在一个 program 里（P5 捆绑不可逆）" % groups,
                        strict,
                    )
                )
            else:
                checks.append(ok(name, "「动作」含 %d 组动作词，计数 %s" % (groups, m.group(0))))
        else:
            checks.append(bad(name, "缺「动作」或「程序内不可逆动作数」栏"))

    return checks


# ---- brief -----------------------------------------------------------------

def check_brief(plain):
    checks = []
    hs = headings(plain)
    missing = [name for name in BRIEF_SECTIONS if not any(name in h for h in hs)]
    if missing:
        checks.append(bad("必需节标题", "缺少 %d 节：%s" % (len(missing), "、".join(missing))))
    else:
        checks.append(
            ok("必需节标题", "%d 节均在：%s" % (len(BRIEF_SECTIONS), "、".join(BRIEF_SECTIONS)))
        )

    placeholders = []
    for no, line in enumerate(plain.splitlines(), 1):
        for m in BRACE_PLACEHOLDER_RE.finditer(line):
            placeholders.append("L%d %s" % (no, m.group(0)))
    if placeholders:
        checks.append(
            bad("无未填占位符", "发现 %d 处未填：%s" % (len(placeholders), "；".join(placeholders[:5])))
        )
    else:
        checks.append(ok("无未填占位符", "未出现 {{...}}"))
    return checks


# ---- report ----------------------------------------------------------------

def section_present(hs, name):
    """报告节标题判定。「验证」不得被「未验证」冒充。"""
    for h in hs:
        if name == "验证":
            if "验证" in h and "未验证" not in h:
                return True
        elif name in h:
            return True
    return False


def section_table(plain):
    """返回「验证」节里第一行表格行（表头）；没有表格返回 None。"""
    inside = False
    for line in plain.splitlines():
        m = HEADING_RE.match(line)
        if m:
            title = m.group(2)
            inside = "未验证" not in title and any(k in title for k in REPORT_TABLE_MARKERS)
            continue
        if inside and line.strip().startswith("|"):
            return line
    return None


def check_report(plain, text):
    checks = []
    hs = headings(plain)
    missing = [name for name in REPORT_SECTIONS if not section_present(hs, name)]
    if missing:
        checks.append(bad("必需节标题", "缺少 %d 节：%s" % (len(missing), "、".join(missing))))
    else:
        checks.append(ok("必需节标题", "%d 节均在" % len(REPORT_SECTIONS)))

    header = section_table(plain)
    if header is None:
        checks.append(bad("§2 含程序 id 列", "在「验证」节里未找到 markdown 表格"))
    elif "程序id" in SPACE_RE.sub("", header):
        checks.append(ok("§2 含程序 id 列", "表头：%s" % header.strip()[:60]))
    else:
        checks.append(bad("§2 含程序 id 列", "表头缺「程序 id」列：%s" % header.strip()[:60]))

    checks.append(check_forbidden(text))
    return checks


# ---- 主流程 ----------------------------------------------------------------

def run_checks(kind, text, plain, strict):
    if kind == "program":
        return check_program(text, plain, strict)
    if kind == "decision":
        return check_decision(plain, strict)
    if kind == "report":
        return check_report(plain, text)
    return check_brief(plain)


def emit(checks):
    """逐条打印检查结果，最后一行打印汇总。返回 FAIL 条数。"""
    for check in checks:
        line = "%s  %s" % (check.status, check.name)
        if check.detail:
            line += " — " + check.detail
        print(line)
    passed = sum(1 for check in checks if check.status == "PASS")
    failed = sum(1 for check in checks if check.status == "FAIL")
    warned = sum(1 for check in checks if check.status == "WARN")
    print("%d passed, %d failed, %d warnings" % (passed, failed, warned))
    return failed


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ptc_lint.py",
        description="校验 无限tokenPTC版 的 program 账本 / 决策日志 / 任务契约书 / 运行报告",
    )
    parser.add_argument("file", help="要校验的 markdown 文件")
    parser.add_argument(
        "--kind",
        choices=["brief", "report", "decision", "program"],
        help="产物类型，省略则按内容自动识别",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="启用 strict 规则（program 三条、decision 的计数矛盾），由 WARN 升级为 FAIL",
    )
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print("错误：文件不存在：%s" % args.file, file=sys.stderr)
        return 2
    if path.is_dir():
        print("错误：不是文件：%s" % args.file, file=sys.stderr)
        return 2
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print("错误：无法读取 %s：%s" % (args.file, exc), file=sys.stderr)
        return 2

    kind = args.kind or detect_kind(text)
    plain = strip_fences(text)

    print("kind: %s%s" % (kind, "" if args.kind else "（自动识别）"))
    failed = emit(run_checks(kind, text, plain, args.strict))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
