#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unlimited_lint.py —— 校验 unlimited-token-mode 的三类产物（决策日志 / 运行报告 / 任务契约书）。

用法:
    python unlimited_lint.py <file> [--kind decision|report|brief] [--json]

--kind 省略时按内容自动识别；识别不出时按 decision 处理。
退出码: 0 通过 / 1 校验失败 / 2 用法错误。

约定: 被 ``` 或 ~~~ 围栏包裹的内容（模板里的格式示例）不参与校验。
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---- 规则表 ----------------------------------------------------------------

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

DECISION_VALUES = {"act", "act-with-mitigation", "don't-act"}

REPORT_SECTIONS = ["做了什么", "验证", "未验证", "残留风险", "假设"]

BRIEF_SECTIONS = ["目标", "完成判据", "歧义决议", "状态来源"]

PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}|<[^<>\n]{1,80}>")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
DECISION_HEAD_RE = re.compile(r"^#{1,6}\s+决策\s+(?:#?\d|<)", re.M)
DECISION_BULLET_RE = re.compile(r"^\s*[-*]\s*决定\s*[:：]", re.M)
BULLET_RE = re.compile(r"^\s*[-*]\s*([^:：]+?)\s*[:：]\s*(.*)$")
FIRST_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")


# ---- 通用工具 --------------------------------------------------------------

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


def headings(text):
    found = []
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            found.append(m.group(2))
    return found


def detect_kind(text):
    plain = strip_fences(text)
    if DECISION_HEAD_RE.search(plain) or DECISION_BULLET_RE.search(plain):
        return "decision"
    hs = headings(plain)
    if any("做了什么" in h for h in hs) and any("残留风险" in h for h in hs):
        return "report"
    if any("完成判据" in h for h in hs) and any("目标" in h for h in hs):
        return "brief"
    return "decision"


def is_blank(value):
    return value.strip(" \t\u3000—-·、;；,，") == ""


# ---- decision --------------------------------------------------------------

def iter_decision_blocks(plain):
    """产出 (起始行号, [(行号, 行文本), ...])。"""
    block = None
    for no, line in enumerate(plain.splitlines(), 1):
        if HEADING_RE.match(line):
            if block is not None:
                yield block
                block = None
            if DECISION_HEAD_RE.match(line):
                block = (no, [])
            continue
        if block is not None:
            block[1].append((no, line))
    if block is not None:
        yield block


def parse_bullets(block_lines):
    """解析 - 键: 值，并接续缩进的续行。返回 {键: (行号, 值)}。"""
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


def decision_value_ok(value):
    m = FIRST_WORD_RE.search(value.strip().strip("`*\"'“”"))
    if not m:
        return False
    return m.group(0).lower().replace("’", "'") in DECISION_VALUES


# ---- 语义检查：本模式的核心 ------------------------------------------------

VAGUE_ENTITY_WORDS = (
    "系统", "某些", "部分", "一些", "相关", "若干", "全部", "所有",
    "整体", "环境", "服务", "用户", "数据", "流程", "接口",
)
IDENT_RE = re.compile(r"[A-Za-z0-9]")
NOISE_RE = re.compile(r"[\s`*_（）()\[\]【】,，。；;:：/|、]+")
MIN_DERIVATION_CHARS = 8


def _bigrams(text):
    """中文字符二元组集合，用于粗粒度判断两段话是否在说同一件事。"""
    t = NOISE_RE.sub("", text).lower()
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _features(text):
    """ASCII 词元 + 中文二元组。

    两段描述同一件事时，通常至少共享一个词元（工具名、变量名、数字）
    或一个中文二元组。完全不共享才判定为「两回事」。
    """
    lowered = text.lower()
    return set(ASCII_TOKEN_RE.findall(lowered)) | _bigrams(lowered)


def check_cross_check(items):
    """假核对检测：「该规则读取的量」与「我核对过的量」不能是两回事。"""
    if "该规则读取的量" not in items or "我核对过的量" not in items:
        return []
    no_a, a = items["该规则读取的量"]
    no_b, b = items["我核对过的量"]
    if is_blank(a) or is_blank(b):
        return []
    if PLACEHOLDER_RE.search(a) or PLACEHOLDER_RE.search(b):
        return []
    fa, fb = _features(a), _features(b)
    if fa and fb and not (fa & fb):
        return [
            "决策块 L%d/L%d: 疑似假核对 —— 「该规则读取的量」与「我核对过的量」"
            "没有任何共同点。规则看的是 A，你查的是 B，这次核对无效。\n"
            "    规则读取: %s\n    实际核对: %s" % (no_a, no_b, a[:70], b[:70])
        ]
    return []


def check_entity_specific(items):
    """受影响实体必须点名；只有笼统名词、没有标识符视为未点名。"""
    if "受影响实体" not in items:
        return []
    no, value = items["受影响实体"]
    if is_blank(value) or PLACEHOLDER_RE.search(value):
        return []
    if IDENT_RE.search(value):
        return []
    for word in VAGUE_ENTITY_WORDS:
        if word in value:
            return [
                "决策块 L%d: 「受影响实体」只有笼统名词（%s），没有点名。"
                "写不出具体标识就无法推演二阶后果。" % (no, word)
            ]
    return []


def check_derivation_depth(items):
    """二阶/三阶不能一句话带过，否则等于没推演。"""
    problems = []
    for key in ("二阶", "三阶"):
        if key not in items:
            continue
        no, value = items[key]
        if is_blank(value) or PLACEHOLDER_RE.search(value):
            continue
        if len(NOISE_RE.sub("", value)) < MIN_DERIVATION_CHARS:
            problems.append(
                "决策块 L%d: 「%s」过于简略（%d 字），未点名实体与代价"
                % (no, key, len(NOISE_RE.sub("", value)))
            )
    return problems


def check_decision(plain):
    """返回 (硬问题, 软警告)。

    硬问题：结构缺失、占位符、决定取值非法 —— 这些没有解释空间。
    软警告：假核对、实体不具体、推演过简 —— 启发式判断，可能有误报，
            默认不影响退出码，加 --strict 才升级为硬问题。
    """
    problems = []
    warnings = []
    blocks = list(iter_decision_blocks(plain))
    if not blocks:
        return ["未找到决策块：每个决策应以 `## 决策 <n> @ <time>` 开头"], []

    for start, body in blocks:
        items = parse_bullets(body)
        for key in DECISION_KEYS:
            if key not in items:
                problems.append("决策块 @L%d: 缺少栏位「%s」" % (start, key))
                continue
            no, value = items[key]
            if is_blank(value):
                problems.append("决策块 @L%d: 栏位「%s」(L%d) 为空" % (start, key, no))
            elif PLACEHOLDER_RE.search(value):
                problems.append(
                    "决策块 @L%d: 栏位「%s」(L%d) 仍是占位符: %s"
                    % (start, key, no, value[:60])
                )

        if "决定" in items:
            value = items["决定"][1]
            if not is_blank(value) and not PLACEHOLDER_RE.search(value):
                if not decision_value_ok(value):
                    problems.append(
                        "决策块 @L%d: 「决定」只能是 act | act-with-mitigation | "
                        "don't-act，实际是: %s" % (start, value[:60])
                    )

        warnings.extend(check_cross_check(items))
        warnings.extend(check_entity_specific(items))
        warnings.extend(check_derivation_depth(items))
    return problems, warnings


# ---- report / brief --------------------------------------------------------

def check_sections(plain, required, label):
    hs = headings(plain)
    problems = []
    for name in required:
        if not any(name in h for h in hs):
            problems.append("%s缺少小节：%s" % (label, name))
    return problems


# ---- 主流程 ----------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="unlimited_lint.py", description="校验 unlimited-token-mode 的决策日志 / 运行报告 / 任务契约书"
    )
    parser.add_argument("file", help="要校验的 markdown 文件")
    parser.add_argument(
        "--kind",
        choices=["decision", "report", "brief"],
        help="产物类型，省略则按内容自动识别",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="把软警告（假核对 / 实体不具体 / 推演过简）也当作失败",
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

    warnings = []
    if kind == "decision":
        problems, warnings = check_decision(plain)
    elif kind == "report":
        problems = check_sections(plain, REPORT_SECTIONS, "报告")
    else:
        problems = check_sections(plain, BRIEF_SECTIONS, "契约书")

    failed = bool(problems) or (args.strict and bool(warnings))
    summary = "PASS" if not failed else "FAIL %d problem(s)" % (
        len(problems) + (len(warnings) if args.strict else 0)
    )

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not failed,
                    "kind": kind,
                    "file": str(path),
                    "problems": problems,
                    "warnings": warnings,
                    "summary": summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for problem in problems:
            print(problem)
        for warning in warnings:
            prefix = "警告: " if not args.strict else ""
            print(prefix + warning)
        print(summary)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
