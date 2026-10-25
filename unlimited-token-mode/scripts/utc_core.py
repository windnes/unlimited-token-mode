"""utc_core —— 无限token模式 v2 的共享判定引擎。

设计前提（v1 为什么被打穿）：
    v1 的所有"证据"都是模型自己写进 markdown 的散文，lint 只能做字符串交集，
    于是「抄一遍字段 + 编一句我确认过了」就能合法通过。
    根因不是规则写得不细，而是**证据的提供者和证据的校验者是同一个不可信来源**。

v2 的修正（分两档，且明确标注档位、不假装等价）：
    [ledger 档] 证据由运行时写。`plugins/utc-ledger.mjs` 在 DSH 里旁听每一次
        tool/call 与 tool/result，落成 append-only 的 ledger.jsonl，模型无法写。
        lint 加 --ledger 后：声称执行过的命令必须在账本里找得到；
        声称的输出必须是真实输出的连续子串；声称的 exit 必须等于真实 exit。
        → 编造 = 找不到调用；改写 = 子串失配；假 exit = 数值不等。三条都是客观判定。
    [shape-only 档] 没装插件时的退化档：只要求「可执行命令 + 形状化输出」同时出现，
        并禁止纯声明式核对。CLI 会打印 evidence_tier=shape-only 明示它更弱。

    两档共有的客观检查：占位符、空栏、不具名实体、无量化后果、决定枚举、
    核心禁令（指出成本却仍 act 且无缓解）、报告里 PASS 必须有命令+输出。
    词元交集这类**启发式**一律降级为 warn，不默认判失败——v1 把它当错误，
    于是把「用 df -h 查磁盘水位」这种诚实核对误判成假核对。

两个模式共用本文件；两份拷贝必须逐字节相同（selftest 校验 sha256）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field as dc_field

# ---------------------------------------------------------------------------
# 编码：任何输入都不能让校验器自己崩掉
# ---------------------------------------------------------------------------

class InputError(Exception):
    """输入不可解析 / 用法错误 -> exit 2。"""


def read_text_any(path: str) -> str:
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        raise InputError(f"无法读取 {path}: {exc}") from exc
    if not raw:
        return ""

    # UTF-16 只靠 BOM 认。没有 BOM 又含 NUL 的，一律当二进制。
    # 不能"试着用 utf-16 解一下看能不能解出来"：任何字节流都能被 utf-16 解码成功，
    # b"\x00\xff" * 500 用 utf-16-be 会解成 'ÿ' 重复（U+00FF 是合法字母），
    # 于是二进制文件会被当成正文放过去。
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            text = raw.decode("utf-16")
        except (UnicodeDecodeError, UnicodeError) as exc:
            raise InputError(f"{path} 声称是 UTF-16（有 BOM）但解码失败: {exc}") from exc
        if not _looks_textual(text):
            raise InputError(f"{path} 有 UTF-16 BOM 但内容不像文本，疑似二进制")
        return text

    if b"\x00" in raw[:8192]:
        raise InputError(f"{path} 看起来是二进制文件，不是 markdown")

    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if _looks_textual(text):
            return text
    raise InputError(f"{path} 无法用 utf-8/gb18030/utf-16 解成正文本")


# 控制字符（除常见空白）占比超过这个值就认为不是文本
CONTROL_RATIO_LIMIT = 0.10


def _looks_textual(text: str) -> bool:
    """判断解出来的字符串像不像 markdown 正文。

    UTF-16 解码能把任意字节流"成功"解成一串字符，所以不能只看解码是否成功：
    b"\\x00\\xff" * 500 用 utf-16-le 会解成 U+FF00 重复 —— Unicode 里未分配的码位。
    真正的判据是"可打印、且是 Unicode 里真有定义的字符占多数"。
    """
    if not text:
        return True
    sample = text[:8192]
    bad = 0
    for ch in sample:
        if ch in "\r\n\t":
            continue
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            bad += 1
            continue
        if 0xE000 <= code <= 0xF8FF:     # 私用区
            bad += 1
            continue
        if code == 0xFFFD:                # 替换字符
            bad += 1
            continue
        category = unicodedata.category(ch)
        if category in ("Cn", "Cc", "Cf", "Co", "Cs"):
            # 未分配 / 控制 / 格式 / 私用 / 代理：这些出现在正文里就是乱码
            bad += 1
    return bad / max(len(sample), 1) <= CONTROL_RATIO_LIMIT


# ---------------------------------------------------------------------------
# 围栏隔离
#   堵掉 v1 的口子：v1 的模板把示例块当"回归用例"内嵌在正文里，
#   于是原样抄模板里的示例就能通过。v2 只解析围栏外的正文，
#   模板示例必须写在 ``` 里，示例因此永远不可能被当成真实留档。
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def strip_fences(text: str) -> str:
    """剥掉代码围栏，但保留「字段值围栏」。

    两种围栏语义不同，不能一视同仁：
    - 顶层围栏（行首无缩进）：格式示例或引用代码，里面的程序块不得被当成真实记录 -> 清空。
    - 缩进围栏（>=2 空格）：`- 证据:` 这类字段的值，本身就是被校验的内容 -> 保留正文，只丢标记行。

    v2 的洞：一律清空，于是模板自带的证据块被读成空字段，
    templates/program-log.md 照原样跑 --kind program 直接报 PRG-FIELD-EMPTY。
    """
    out: list[str] = []
    inside = False
    keep = False
    marker = ""
    for line in text.splitlines():
        m = FENCE_RE.match(line)
        if m:
            token = m.group(1)
            indent = len(line) - len(line.lstrip())
            if not inside:
                inside, marker = True, token
                keep = indent >= 2
                # 保留型围栏整行跳过：留下空行会让 parse_fields 提前结束字段累积，
                # 证据正文就挂不到 `- 证据:` 上，照样读成空字段。
                if not keep:
                    out.append("")
                continue
            if token[0] == marker[0] and len(token) >= len(marker):
                inside = False
                keep = False
                out.append("")
                continue
        if not inside:
            out.append(line)
        elif keep:
            out.append(line)
        else:
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 占位符
# ---------------------------------------------------------------------------

PLACEHOLDER_PATTERNS = [
    r"\{\{[^}]*\}\}",
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bFIXME\b",
    r"待填",
    r"待补",
    r"待定",
    r"未填写",
    r"<[^>\n]{1,40}>",
    r"（例[：:][^）]*）",
    r"\(例[：:][^)]*\)",
]
PLACEHOLDER_RE = re.compile("|".join(PLACEHOLDER_PATTERNS), re.IGNORECASE)


def has_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER_RE.search(text))

# 纯声明式"核对"：没有命令、没有输出，只有一句结论
DECLARATIVE_CHECK_RE = re.compile(
    r"我(已经)?(确认|核对|检查|验证|看过|查过)"
    r"|已(经)?(确认|核对|检查|验证|核实)"
    r"|检查通过|核对通过|验证通过|无需(进一步)?(核查|检查|核对)"
    r"|(看起来|似乎|应该是)(没问题|正常|对的)"
    r"|I (have )?(confirmed|verified|checked)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 命令与输出形态
# ---------------------------------------------------------------------------

SHELL_WORDS = (
    "ls|cat|du|df|grep|find|wc|awk|sed|curl|wget|ps|ss|netstat|ping|dig|nslookup|"
    "git|docker|kubectl|helm|psql|mysql|redis-cli|sqlite3|python|python3|node|npm|"
    "pnpm|yarn|pip|pip3|systemctl|journalctl|service|tar|unzip|zip|md5sum|sha256sum|"
    "shasum|certutil|Get-ChildItem|Get-Content|Get-Process|Get-Service|Get-Item|"
    "Get-ChildItem|Select-String|Test-Path|Test-NetConnection|Invoke-WebRequest|"
    "Measure-Object|Where-Object|nvidia-smi|smartctl|iostat|vmstat|free|top|uptime|"
    "dmesg|lsblk|fdisk|mount|env|printenv|whoami|id|hostname|date|time|"
    "pytest|jest|mocha|vitest|cargo|go|dotnet|java|mvn|gradle|make|cmake|cmake"
)

# 反引号里的内容：只要像命令就认（含 shell 元字符 / 已知命令 / 路径 / 带 flag）
BACKTICK_CMD_RE = re.compile(r"`([^`\n]{2,2000})`")
# 行首裸命令（有人不写反引号）
LINE_CMD_RE = re.compile(
    r"(?:^|\n)[ \t>$\w-]{0,8}((?:sudo\s+|env\s+)?(?:" + SHELL_WORDS + r")\b[^\n]{0,240})"
    r"|(?:^|\n)[ \t]*\$?[ \t]*((?:await\s+)?tools\.[A-Za-z_][\w]*\s*\([^\n]{0,240})",
    re.IGNORECASE,
)

CMD_META_RE = re.compile(r"[\s][-|/>&]{1,2}[\w$\{\(\[\"'./-]|[/\\][\w.-]+|\$\{|%[A-Z_]+%")
KNOWN_CMD_RE = re.compile(r"^(?:sudo\s+|env\s+)?(?:" + SHELL_WORDS + r")\b", re.IGNORECASE)


# PTC 的证据命令常常是 SDK 绑定调用而不是 shell 命令：
#   await tools.read({ file_path: "state/run.json" })
#   await tools.ops_bash({ command: "..." })
# 模板示例与真实程序账本都会这么写，认不出来就会误报 PRG-EVIDENCE-CMD。
TOOLS_CALL_RE = re.compile(r"^\$?\s*(?:await\s+)?tools\.[A-Za-z_][\w]*\s*\(")


def looks_like_command(token: str) -> bool:
    token = token.strip()
    if len(token) < 2 or has_placeholder(token):
        return False
    if TOOLS_CALL_RE.match(token):
        return True
    if KNOWN_CMD_RE.match(token):
        return True
    if CMD_META_RE.search(token) and re.search(r"[A-Za-z]", token):
        return True
    return False


def extract_commands(text: str) -> list[str]:
    cmds: list[str] = []
    for m in BACKTICK_CMD_RE.finditer(text):
        token = m.group(1).strip()
        if looks_like_command(token):
            cmds.append(token)
    for m in LINE_CMD_RE.finditer(text):
        # 该正则有两个分支（shell 裸命令 / tools.* 绑定），命中的分组不固定
        token = next((g for g in m.groups() if g), "").strip()
        if len(token) >= 3:
            cmds.append(token)
    seen: set[str] = set()
    out: list[str] = []
    for c in cmds:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def has_evidence_cmd(text: str) -> bool:
    return bool(extract_commands(text))


# 形状化输出：数值+单位 / 计数 / 布尔 / 退出码 / 明确的键值结果
EVIDENCE_OUT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:GB|MB|KB|TB|ms|sec|min|分钟|秒|小时|天|%|元|条|个|次|行|台|核|万|亿)"
    r"|(?:→|->|=>|=|:|：)\s*-?\d"
    r"|exit\s*(?:code)?\s*[=:]?\s*-?\d"
    r"|\b(?:true|false)\b"
    r"|@L\d+"                       # 行号引用：FAIL program @L12
    r"|\b\d{2,}\b",
    re.IGNORECASE,
)

EXIT_CLAUSE_RE = re.compile(r"exit\s*(?:code)?\s*[=:]?\s*(-?\d+)", re.IGNORECASE)


def has_evidence_out(text: str) -> bool:
    return bool(EVIDENCE_OUT_RE.search(text))


def strip_exit_clause(text: str) -> str:
    return EXIT_CLAUSE_RE.sub(" ", text)


# ---------------------------------------------------------------------------
# 实体 / 量化 / 缓解
# ---------------------------------------------------------------------------

GENERIC_ENTITY = {
    "系统", "服务", "用户", "它", "他们", "该实体", "相关方", "下游", "平台",
    "程序", "代码", "数据", "东西", "情况", "环境", "机器", "业务", "项目",
    "the system", "the service", "users", "it", "they", "downstream", "platform",
    "something", "stuff", "things",
}

NAMED_ENTITY_RE = re.compile(
    r"`[^`\n]{2,60}`"
    r"|(?:[A-Za-z]:[\\/]|\.{0,2}/)[^\s，,。;；]{2,80}"
    r"|\b[a-zA-Z][\w.]*(?:-[\w.]+)+\b"
    r"|\b[a-zA-Z]+_[a-zA-Z0-9_]+\b"
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"
    r"|\b[a-zA-Z][\w-]*\.(?:py|js|mjs|ts|tsx|json|jsonl|yml|yaml|md|log|env|ini|toml|sh|ps1|db|sql)\b"
    r"|[\u4e00-\u9fff]{2,12}(?:表|库|服务|主机|节点|集群|目录|文件|账号|账户|队列|流水线|任务|容器|镜像|接口|配置|脚本|进程|端口|环境|版本|分支|快照|磁盘|扇区|索引|视图|账户)"
)

QUANT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:GB|MB|KB|TB|ms|sec|min|分钟|秒|小时|天|周|月|年|"
    r"%|元|美元|条|个|次|行|台|核|万|亿|人|处|份|倍|QPS|rps|RPS)",
    re.IGNORECASE,
)

MITIGATION_RE = re.compile(
    r"缓解|回滚|rollback|备份|快照|snapshot|canary|灰度|限流|配额|告警|监控|"
    r"暂停|分批|增量|dry-?run|预演|试运行|最小替代|SET UNUSED|可撤销|"
    r"先只|先对|只删|先执行|限额|熔断|双写|影子|旁路",
    re.IGNORECASE,
)

COST_RE = re.compile(
    r"成本|代价|损失|延迟|积压|失败|不可用|中断|超时|报错|回滚|重建|"
    r"downtime|cost|latency|outage|fail|error|break",
    re.IGNORECASE,
)


def named_entities(text: str) -> set[str]:
    out: set[str] = set()
    for m in NAMED_ENTITY_RE.finditer(text):
        token = m.group(0).strip().strip("`").strip().lower()
        if not token or len(token) < 2:
            continue
        if token in GENERIC_ENTITY:
            continue
        if has_placeholder(token):
            continue
        out.add(token)
    return out


def has_named_entity(text: str) -> bool:
    return bool(named_entities(text))


def has_quant(text: str) -> bool:
    return bool(QUANT_RE.search(text))


# ---------------------------------------------------------------------------
# 词元（只用于启发式 warn，绝不单独判失败）
# ---------------------------------------------------------------------------

def tokens(text: str) -> list[str]:
    out = re.findall(r"[A-Za-z][A-Za-z0-9_.\-]{1,}", text)
    out += re.findall(r"[\u4e00-\u9fff]{2}", text)
    return [t.lower() for t in out]


STOP_TOKENS = {
    "的", "了", "吗", "呢", "是", "在", "和", "与", "及", "或", "也", "都", "就",
    "the", "a", "an", "of", "to", "in", "on", "is", "are", "and", "or",
}


def content_tokens(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in STOP_TOKENS and len(t) >= 2}


def jaccard(a: str, b: str) -> float:
    sa, sb = content_tokens(a), content_tokens(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# ledger（运行时写的证据账本）
# ---------------------------------------------------------------------------


@dataclass
class LedgerRecord:
    kind: str
    call_id: str
    tool: str
    command: str
    output: str
    exit_code: int | None
    time: str
    # "inner" = PTC 程序内部的真实工具调用（tool/ptc-dispatch）；
    # 其它/缺省 = 外层调用。内层记录的命令与退出码来自真实执行，
    # 外层 run_code 记录的命令是整段程序源码、exit 是整段程序的退出码。
    scope: str = "outer"


class Ledger:
    def __init__(self, path: str | None):
        self.path = path
        self.records: list[LedgerRecord] = []
        self.loaded = False
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            raise InputError(f"ledger 不存在: {path}")
        text = read_text_any(path)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            self.records.append(
                LedgerRecord(
                    kind=str(obj.get("kind", "")),
                    call_id=str(obj.get("callId", "")),
                    tool=str(obj.get("tool", "")),
                    command=str(obj.get("command", "")),
                    output=str(obj.get("output", "")),
                    exit_code=obj.get("exit"),
                    time=str(obj.get("t", "")),
                    scope=str(obj.get("scope", "outer")),
                )
            )
        self.loaded = True

    def results(self) -> list[LedgerRecord]:
        return [r for r in self.records if r.kind == "result"]

    def execution_provable(self) -> bool:
        """账本能否证明「这条命令真的被执行过」。

        在 run_code 这种「整段程序一次调用」的工具面上，插件只看得见外层
        `run_code`，看不见程序内部 `await tools.ops_bash(...)` 的真实调用，
        于是账本里记录的 command 是**整段程序源码**、output 是**程序返回值**。
        这时命令字面量出现在源码里就能匹配上，匹配成功只证明「源码里写了」，
        不证明「跑过了」。只有当账本里存在非 run_code 的记录时，
        命令与输出才对应到一次真实工具执行。
        """
        res = self.results()
        # 内层 PTC 调用（tool/ptc-dispatch）落账后，命令与退出码都来自真实执行，
        # 这才算「执行可证」。仅凭 tool != "run_code" 会把任何旁路工具记成可证。
        if any(r.scope == "inner" for r in res):
            return True
        return bool(res) and any(r.tool != "run_code" for r in res)

    def candidates(self, claimed_cmd: str) -> list["LedgerRecord"]:
        """找出可能对应这条声称的命令的账本记录。

        不能要求字面相等：agent 常常把同一条逻辑命令重写、重跑几次
        （换引号、加 -LiteralPath、拆行），账本里因此有多条记录。
        这里用"包含"或词元重合度挑候选，再由输出比对决定是否真的成立。

        但**锚点必须吻合**：命令里的具体文件名 / 脚本名 / 路径是这条命令的身份，
        换掉它就是另一条命令。只比词元重合度会把
        `python sum_data.py` 与 `python does_not_exist.py` 判成同一条
        （`$null`、`exit_code`、`LASTEXITCODE` 这些模板词占了大部分权重）。
        """
        want = norm_cmd(claimed_cmd)
        if len(want) < 3:
            return []
        want_tokens = content_tokens(want)
        want_anchors = cmd_anchors(want)
        scored: list[tuple[float, int, LedgerRecord]] = []
        for order, rec in enumerate(self.results()):
            real = norm_cmd(rec.command)
            if len(real) < 3:
                continue
            if want_anchors:
                if not want_anchors <= cmd_anchors(real):
                    continue
            # 内层记录优先：它对应一次真实工具执行，外层 run_code 记录只是程序源码。
            # 同分时若不区分，源码里写到过的命令会抢走真实执行的记录。
            bonus = 0.5 if rec.scope == "inner" else 0.0
            if want in real or real in want:
                scored.append((1.0 + bonus, order, rec))
                continue
            real_tokens = content_tokens(real)
            if not want_tokens or not real_tokens:
                continue
            overlap = len(want_tokens & real_tokens) / len(want_tokens | real_tokens)
            if overlap >= 0.55:
                scored.append((overlap + (0.5 if rec.scope == "inner" else 0.0), order, rec))
        # 得分并列时偏向**更晚**的记录：同一条命令可能在多段程序源码里都出现过，
        # 按插入顺序取最早那条会让「最近一次真实执行」永远轮不到，
        # 进而拿一条早已作废的记录去比对输出，把诚实证据判成改写。
        # 先按得分（含内层加权），再按时间先后偏向更晚的记录。
        scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
        return [rec for _, _, rec in scored]

    def find_call(self, claimed_cmd: str) -> LedgerRecord | None:
        found = self.candidates(claimed_cmd)
        return found[0] if found else None

    def find_matching(self, claimed_cmd: str, claimed_out: str) -> "LedgerRecord | None":
        """命令与输出都成立才算找到：优先返回输出也对得上的那条记录。"""
        found = self.candidates(claimed_cmd)
        for rec in found:
            if self.output_matches(rec, claimed_out):
                return rec
        return found[0] if found else None

    def output_matches(self, rec: LedgerRecord, claimed_out: str) -> bool:
        """声称的输出片段必须能在真实输出里找到；支持 ... 截断。

        反引号只是 markdown 的排版，不是内容：报告里把输出包进 `` ` `` 里属于正常写法，
        比对前两侧都去掉，否则诚实的报告会被误判成改写。
        """
        real = norm_out(rec.output).replace("`", "")
        claimed_out = extract_claim_output(claimed_out).replace("`", "")
        if not real:
            return False
        segs = [s for s in re.split(r"…|\.\.\.|\[\.\.\.\]|\[snip\]", claimed_out)]
        segs = [norm_out(s) for s in segs]
        long_segs = [s for s in segs if len(s) >= 6]
        if long_segs:
            return all(s in real for s in long_segs)
        # 全是短片段：先要求字面包含（短片段本来就该原样出现在输出里）
        short = [s for s in segs if s]
        if short and all(s in real for s in short):
            return True
        # 再退化为数字集合子集，但至少要两个不同数字。
        # v2 的洞：单个数字就能命中任何含该数字的记录——「错误 0」可以匹配上
        # 任意一条输出里出现过 0 的记录，等于没有校验。
        claimed_nums = set(re.findall(r"-?\d+(?:\.\d+)?", claimed_out))
        if len(claimed_nums) >= 2:
            return claimed_nums <= set(re.findall(r"-?\d+(?:\.\d+)?", rec.output))
        return False


ARROW_SPLIT_RE = re.compile(r"\s*(?:→|->|=>|⇒)\s*")

# 命令的"锚点"：具体文件名、脚本名、路径、版本号这类标识。
# 这些是命令的身份，换掉就是换了命令；而 $null / LASTEXITCODE / -LiteralPath
# 这类模板词谁都在用，不能用来判断"是不是同一条命令"。
ANCHOR_RES = [
    re.compile(r"[A-Za-z]:\\[^\s\"'`;|<>]+"),                 # Windows 绝对路径
    re.compile(r"(?<![\w.])\.{1,2}[\\/][^\s\"'`;|<>]+"),       # 相对路径 ./x
    re.compile(r"(?<![\w/])/(?:[\w.\-]+/)*[\w.\-]+"),          # POSIX 路径
    re.compile(r"[\w.\-]+\.(?:py|ps1|sh|js|mjs|ts|json|jsonl|ya?ml|md|txt|log|sql|env|ini|toml|csv)\b", re.I),
]

# 这些"文件名"其实是管道/重定向的目标，不构成身份
ANCHOR_NOISE = {"$null", "/dev/null", "nul"}


def cmd_anchors(cmd: str) -> set[str]:
    out: set[str] = set()
    for pattern in ANCHOR_RES:
        for m in pattern.finditer(cmd):
            token = m.group(0).strip().strip("\"'`")
            low = token.lower()
            if low in ANCHOR_NOISE:
                continue
            if len(token) < 3:
                continue
            out.add(low)
    return out


def extract_claim_output(text: str) -> str:
    """把「命令 → 输出」里属于输出的那一段切出来。

    核对栏本来就该写成「命令 → 输出」，直接拿整串去比真实输出必然失配，
    所以只要出现箭头就取箭头之后的部分；没有箭头时剥掉反引号壳再比。
    """
    parts = ARROW_SPLIT_RE.split(text)
    if len(parts) >= 2:
        tail = parts[-1]
        if len(tail.strip()) >= 2:
            return tail
    # 「命令 exit=0，输出 X」这种写法：取「输出 / output / stdout」之后的部分。
    # 标签必须处在子句边界（行首 / 空白 / 标点之后），且不能被「的/地/得」引导——
    # 否则「证据里的输出与账本记录不一致」会被当成「输出: 与账本记录不一致」截断，
    # 反过来把一条诚实的证据判成 LEDGER-OUTPUT-MISMATCH。
    m = re.search(
        r"(?:^|[\s，,；;。、:：])(?<![的地得])(?:输出|output|stdout)\s*[:：]?\s*(.+)$",
        text, re.IGNORECASE,
    )
    if m and len(m.group(1).strip()) >= 2:
        return m.group(1)
    # 没有箭头：先把反引号壳剥掉
    naked = text.replace("`", " ").strip()
    stripped = re.sub(r"\s+", " ", naked).strip(" ：:，,;；")
    return stripped if stripped else text


def norm_cmd(cmd: str) -> str:
    cmd = cmd.replace("`", " ").strip()
    cmd = re.sub(r"^(?:\$|>|PS[ >]|>>>)\s*", "", cmd)
    cmd = re.sub(r"\s+", " ", cmd)
    return cmd.lower()


def norm_out(out: str) -> str:
    out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    level: str
    code: str
    message: str
    line: int | None = None
    # 启发式提示：基于文本相似度之类的不可靠信号作出，永远不升级为失败。
    # v1 的教训正是"用不可靠的启发式判失败"，把「用 df -h 查磁盘水位」
    # 这种诚实核对误判成假核对。宁可漏报也不能误杀诚实产物。
    heuristic: bool = False

    def render(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return f"[{self.level}]{loc} {self.code}: {self.message}"


@dataclass
class Result:
    findings: list[Finding] = dc_field(default_factory=list)
    evidence_tier: str = "shape-only"
    checked_blocks: int = 0

    def add(self, level: str, code: str, message: str, line: int | None = None,
            *, heuristic: bool = False) -> None:
        self.findings.append(Finding(level, code, message, line, heuristic))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def find_line(text: str, needle: str, start: int = 0) -> int | None:
    idx = text.find(needle, start)
    if idx < 0:
        return None
    return text.count("\n", 0, idx) + 1


def line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


FIELD_RE = re.compile(r"^\s*[-*]\s*([^:：]{1,24}?)\s*[:：]\s*(.*)$")


def parse_fields(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    for raw in block.splitlines():
        m = FIELD_RE.match(raw)
        if m:
            current = m.group(1).strip()
            fields[current] = m.group(2).strip()
        elif current is not None and raw.strip() and not raw.strip().startswith("#"):
            fields[current] = (fields[current] + " " + raw.strip()).strip()
        elif not raw.strip():
            current = None
    return fields


def get_field(fields: dict[str, str], *names: str):
    norm = {k.replace(" ", "").replace("　", ""): v for k, v in fields.items()}
    for name in names:
        key = name.replace(" ", "")
        if key in norm:
            return name, norm[key]
    return None, None


TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$", re.MULTILINE)
# 表格里的 `\|` 是转义的竖线（常见于命令中的管道），不能当分隔符
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def parse_table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for m in TABLE_ROW_RE.finditer(section):
        cells = [c.strip().replace("\\|", "|") for c in CELL_SPLIT_RE.split(m.group(1))]
        if not cells:
            continue
        if re.fullmatch(r"[-:\s]*", "".join(cells)):
            continue
        rows.append(cells)
    return rows


def is_table_header(row: list[str]) -> bool:
    """表头判定：任一单元格是纯标签词，或整行没有任何命令/数值。"""
    labels = {"#", "判据", "验证命令", "期望输出", "谁验", "真实输出", "结果",
              "术语", "读法 a", "读法 b", "选定", "理由", "项", "为什么没验证",
              "影响", "谁来补", "风险", "触发条件", "影响面", "缓解"}
    for cell in row:
        low = cell.strip().strip("*_` ").lower()
        if low in labels:
            return True
    return False


def section_body(body: str, pattern: str) -> str | None:
    m = re.search(r"^#{1,6}\s*.*?" + pattern + r".*?$", body, re.MULTILINE)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^#{1,6}\s", body[start:], re.MULTILINE)
    return body[start: start + nxt.start()] if nxt else body[start:]


# ---------------------------------------------------------------------------
# 决定块（契约 3）
# ---------------------------------------------------------------------------

DECISION_HEAD_RE = re.compile(r"^#{1,6}[ \t]*决策[ \t]*([0-9]+)[ \t]*(?:@[ \t]*(.*))?$", re.MULTILINE)

PROGRAM_HEAD_RE = re.compile(r"^#{1,6}[ \t]*程序[ \t]*([A-Za-z0-9_\-]+)[ \t]*(?:@[ \t]*(.*))?$", re.MULTILINE)

DECISION_ENUM = ("act-with-mitigation", "don't-act", "dont-act", "do-not-act", "act")
NEGATION_HEAD_RE = re.compile(
    r"^(?:不能|不要|不用|不得|不可|无法|不建议|不改|不做|不执行)"
    r"|^(?:don't|dont|do not|not|no|never)\b",
    re.IGNORECASE,
)


def parse_decision_value(raw: str) -> str | None:
    text = raw.strip()
    text = re.sub(r"^[`*_\s]+", "", text)
    low = text.lower()
    for value in DECISION_ENUM:
        if low.startswith(value):
            return value
    if text.startswith("不执行") or text.startswith("不做") or text.startswith("放弃") or text.startswith("不改"):
        return "don't-act"
    if text.startswith("执行") or text.startswith("做"):
        return "act"
    return None


def has_negation_prefix(raw: str) -> bool:
    text = raw.strip()
    return bool(NEGATION_HEAD_RE.match(text))


def check_decision_block(block: str, res: Result, ledger: Ledger, line: int) -> None:
    fields = parse_fields(block)
    if not fields:
        res.add("error", "DEC-EMPTY", "决策块没有任何 `- 键: 值` 形式的栏位", line)
        return

    required = [
        ("动作", ("动作",)),
        ("受影响实体", ("受影响实体", "受影响对象")),
        ("触发规则", ("触发规则",)),
        ("该规则读取的量", ("该规则读取的量", "规则读取的量")),
        ("我核对过的量", ("我核对过的量", "我核对的量")),
        ("二阶", ("二阶", "二阶后果")),
        ("三阶", ("三阶", "三阶后果")),
        ("不可逆性", ("不可逆性", "可逆性")),
        ("反事实", ("反事实",)),
        ("决定", ("决定",)),
    ]

    present: dict[str, str] = {}
    for label, aliases in required:
        key, val = get_field(fields, *aliases)
        if key is None:
            res.add("error", "DEC-FIELD-MISSING", f"缺少必备栏位「{label}」", line)
            continue
        if not val.strip():
            res.add("error", "DEC-FIELD-EMPTY", f"栏位「{label}」为空", line)
            continue
        if has_placeholder(val):
            res.add("error", "DEC-FIELD-PLACEHOLDER", f"栏位「{label}」仍是占位符", line)
            continue
        present[label] = val

    # ---- 决定：严格枚举（v1 只取首个 ASCII 词，"不能 act" 会被读成 act）----
    raw_decision = present.get("决定", "")
    if raw_decision:
        value = parse_decision_value(raw_decision)
        if value is None:
            if has_negation_prefix(raw_decision):
                res.add("error", "DEC-NEGATED",
                        f"决定栏以否定词开头（「{raw_decision[:20]}」），调用方无法解析；"
                        f"请直接写 don't-act", line)
            else:
                res.add("error", "DEC-ENUM",
                        f"决定栏必须是 act | act-with-mitigation | don't-act 之一，"
                        f"实际为「{raw_decision[:40]}」", line)

    # ---- 受影响实体：必须具名 ----
    entity = present.get("受影响实体", "")
    if entity and not has_named_entity(entity):
        res.add("error", "DEC-ENTITY-GENERIC",
                "受影响实体不具名（「系统/服务/用户」这类泛称无法推演二阶）；"
                "请写服务名、表名、主机名、账户 id 或路径", line)

    # ---- 核对量 ----
    rule_qty = present.get("该规则读取的量", "")
    checked = present.get("我核对过的量", "")
    if rule_qty and checked:
        check_cross_check(rule_qty, checked, res, ledger, line)

    # ---- 二阶 / 三阶 ----
    d2 = present.get("二阶", "")
    d3 = present.get("三阶", "")
    if d2:
        if not has_named_entity(d2):
            res.add("error", "DEC-D2-ENTITY",
                    "二阶没有点名任何实体——「无后果」本身就是最该被质疑的结论", line)
        if not has_quant(d2):
            res.add("error", "DEC-D2-QUANT", "二阶没有给出任何可量化的成本（数字+单位）", line)
        if len(d2) < 16:
            res.add("error", "DEC-D2-SHALLOW", f"二阶只有 {len(d2)} 字，是口号不是推演", line)
    if d3:
        if len(d3) < 16:
            res.add("error", "DEC-D3-SHALLOW", f"三阶只有 {len(d3)} 字，是口号不是推演", line)
        if d2:
            e2, e3 = named_entities(d2), named_entities(d3)
            # 允许用指代承接（"该服务""其""上述"），这不算断链
            anaphora = re.search(r"(该|此|上述|前述|其|这个|那个|前者)", d3)
            if e2 and e3 and not (e2 & e3) and not anaphora:
                res.add("warn", "DEC-D3-ENTITY",
                        "三阶没有复用二阶的任何实体，也没有用指代——"
                        "确认链条是连的（启发式提示，永不判失败）", line,
                        heuristic=True)
            if jaccard(d2, d3) > 0.72:
                res.add("error", "DEC-D3-DUP", "三阶与二阶几乎相同（只是在改写二阶）", line)

    # ---- 不可逆性 ----
    irr = present.get("不可逆性", "")
    if irr:
        if not re.search(r"(可逆|不可逆|撤销|回滚|恢复)", irr):
            res.add("error", "DEC-IRREV", "不可逆性栏必须明确写「可逆」或「不可逆」及撤销成本", line)
        elif re.search(r"不可逆", irr) and not re.search(r"(撤销|回滚|恢复|重建|成本|丢失|不可恢复)", irr):
            res.add("error", "DEC-IRREV-COST", "标了不可逆却没有写撤销成本", line)

    # ---- 反事实 ----
    cf = present.get("反事实", "")
    if cf and not re.search(r"(什么都不做|不作为|不做|最小替代|替代方案|替代|维持现状)", cf):
        res.add("error", "DEC-COUNTERFACTUAL",
                "反事实栏必须给出「什么都不做」或「最小替代」的对照", line)

    # ---- 核心禁令 ----
    check_core_prohibition(present, res, line)


def check_cross_check(
    rule_qty: str,
    checked: str,
    res: Result,
    ledger: Ledger,
    line: int,
) -> None:
    """v1 在这里只做 `_features(a) & _features(b)` 的字符串交集，
    既会把诚实的核对误判为假（"磁盘水位" vs `df -h`），
    也会被"抄一遍字段 + 编一句我确认过了"骗过。
    v2 用三条客观判据 + 一条启发式（降级为 warn）。
    """
    has_cmd = has_evidence_cmd(checked)
    has_out = has_evidence_out(checked)

    # 1) 纯声明式核对 —— v1 完全放行
    if DECLARATIVE_CHECK_RE.search(checked) and not (has_cmd and has_out):
        res.add("error", "DEC-FAKE-CHECK",
                "核对栏是纯声明式（「我确认过了 / 检查通过 / 无需进一步核查」），"
                "既没有命令也没有输出——这不是核对，是陈述", line)
        return

    # 2) 形态：必须真的有一条命令和一条形状化输出
    if not has_cmd:
        res.add("error", "DEC-CHECK-NOCMD",
                "核对栏里没有任何可执行命令；无法复核的核对等于没做", line)
    if not has_out:
        res.add("error", "DEC-CHECK-NOOUT",
                "核对栏里没有任何形状化输出（数字/计数/退出码）；只说「查过了」不算查过", line)

    # 3) 账本：命令必须真跑过，声称输出必须是真实输出
    if ledger.loaded:
        cmds = extract_commands(checked)
        if not cmds:
            res.add("error", "LEDGER-NOCMD",
                    "已提供运行时账本，但核对栏没有可匹配的命令", line)
        else:
            matched, matched_cmd = None, ""
            for cmd in cmds:
                matched = ledger.find_matching(cmd, checked)
                if matched:
                    matched_cmd = cmd
                    break
            if matched is None:
                res.add("error", "LEDGER-NO-CALL",
                        f"账本里没有这次调用的记录（声称执行：`{cmds[0][:60]}`）——"
                        f"证据不是运行时产生的", line)
            elif not ledger.output_matches(matched, checked):
                res.add("error", "LEDGER-OUTPUT-MISMATCH",
                        f"核对栏的输出与账本里 `{matched_cmd[:50]}` 的真实输出对不上"
                        f"（疑似改写或编造）", line)

    # 4) 启发式：词元无交集时只警告，不判失败
    #    v1 把它当错误，于是「用 df -h 查磁盘水位」被判成假核对。
    if has_cmd and has_out and not ledger.loaded:
        shared = content_tokens(rule_qty) & content_tokens(checked)
        cmd_tokens: set[str] = set()
        for cmd in extract_commands(checked):
            cmd_tokens |= content_tokens(cmd)
        if not shared and not (content_tokens(rule_qty) & cmd_tokens):
            res.add("warn", "DEC-CROSS-HEURISTIC",
                    "「该规则读取的量」与「我核对过的量」字面上没有任何共同词——"
                    "请确认命令真的读了那个量（启发式提示，永不判失败）", line,
                    heuristic=True)


def check_core_prohibition(present: dict[str, str], res: Result, line: int) -> None:
    """核心禁令：指出真实成本 + 决定仍是 act + 无缓解 = 此块无效。
    v1 把这句话写在 templates/decision-log.md:9-10，代码里 grep「缓解」= 0 处 —— 从未实现。
    """
    d2 = present.get("二阶", "")
    d3 = present.get("三阶", "")
    decision = present.get("决定", "")
    cf = present.get("反事实", "")

    if not decision:
        return
    if parse_decision_value(decision) != "act":
        return

    cost_named = False
    for segment in (d2, d3):
        if segment and has_named_entity(segment) and has_quant(segment) and COST_RE.search(segment):
            cost_named = True
            break
    if not cost_named:
        return

    # 缓解必须在「决定」栏或专门的「缓解」栏里被声明。
    # 不能靠二阶/三阶里出现的"告警/监控"这类词蒙混 —— 那些描述的是后果，不是已采取的措施。
    # 决定栏里显式写了缓解措施，等价于 act-with-mitigation，放行。
    if MITIGATION_RE.search(decision):
        return

    res.add("error", "DEC-CORE-PROHIBITION",
            "二阶/三阶写了具名实体与可量化成本，决定仍是 act 且没有任何缓解措施——"
            "这正是「指出风险却不改变动作」。改成 act-with-mitigation 或 don't-act", line)


# ---------------------------------------------------------------------------
# 运行报告（契约 7）
# ---------------------------------------------------------------------------

HEDGE_RE = re.compile(
    r"应该可以|应该是没|看起来没问题|基本完成|差不多完成|已修复（无输出）|已修复\(无输出\)|"
    r"大概没问题|应该没问题|应该行|应该好了|想必"
)

PASS_RE = re.compile(r"\b(PASS|OK|通过)\b|✅", re.IGNORECASE)

REPORT_SECTION_ALIASES = {
    "做了什么": ("做了什么", "做的什么"),
    "完成判据": ("完成判据", "验证命令", "真实输出"),
    "未验证项": ("未验证",),
    "残留风险": ("残留风险", "假设清单", "残留风险与假设"),
    "结论": ("结论",),
}


def check_report(text: str, res: Result, ledger: Ledger) -> None:
    body = strip_fences(text)

    for title, aliases in REPORT_SECTION_ALIASES.items():
        if not any(re.search(r"^#{1,6}\s*.*?" + re.escape(a), body, re.MULTILINE) for a in aliases):
            res.add("error", "REP-SECTION", f"缺少小节「{title}」")

    if has_placeholder(body):
        res.add("error", "REP-PLACEHOLDER", "报告里仍有未填的占位符")

    for m in HEDGE_RE.finditer(body):
        res.add("error", "REP-HEDGE", f"禁止的模糊措辞：「{m.group(0)}」", line_at(body, m.start()))

    # §2 完成判据表
    section = section_body(body, r"(完成判据|验证命令|真实输出)")
    if section is not None:
        rows = parse_table_rows(section)
        data_rows = [r for r in rows if len(r) >= 3 and not is_table_header(r)]
        if not data_rows:
            res.add("error", "REP-NO-CRITERIA", "§2 没有任何一条判据行")
        pass_count = 0
        for row in data_rows:
            label = row[1][:40] if len(row) > 1 else (row[0][:40] if row else "?")
            cmd_cell = row[2] if len(row) > 2 else ""
            out_cell = row[3] if len(row) > 3 else ""
            verdict = " ".join(row[4:]) if len(row) > 4 else row[-1]
            loc = find_line(body, row[0][:16]) if row and row[0] else None
            if not PASS_RE.search(verdict):
                continue
            pass_count += 1
            if not has_evidence_cmd(cmd_cell):
                res.add("error", "REP-PASS-NOCMD", f"判据「{label}」标 PASS 但没有可执行命令", loc)
            if not out_cell.strip() or has_placeholder(out_cell):
                res.add("error", "REP-PASS-NOOUT", f"判据「{label}」标 PASS 但没有真实输出", loc)
            elif not re.search(r"\d", out_cell) and not re.search(
                r"(?<![A-Za-z])(?:true|false|PASS|FAIL)(?![A-Za-z])", out_cell, re.IGNORECASE
            ):
                res.add("error", "REP-PASS-WEAKOUT",
                        f"判据「{label}」的「真实输出」里一个数字都没有——"
                        f"真实输出通常至少含一个计数/状态码/数值", loc)
            if ledger.loaded and cmd_cell:
                cmds = extract_commands(cmd_cell)
                rec = None
                for cmd in cmds:
                    rec = ledger.find_matching(cmd, out_cell)
                    if rec:
                        break
                if rec is None:
                    res.add("error", "LEDGER-NO-CALL",
                            f"§2 声称执行过 `{(cmds[0] if cmds else cmd_cell)[:50]}`，"
                            f"账本里没有这次调用", loc)
                elif out_cell and not ledger.output_matches(rec, out_cell):
                    res.add("error", "LEDGER-OUTPUT-MISMATCH",
                            f"§2 判据「{label}」的真实输出与账本记录不一致（疑似改写）", loc)
        if pass_count == 0 and data_rows:
            res.add("warn", "REP-NO-PASS", "§2 没有一条 PASS；若确实全失败，请在结论里明确写未达成")

    # §3 未验证项 vs 结论
    unverified = section_body(body, r"未验证")
    if unverified is not None:
        rows = [r for r in parse_table_rows(unverified) if len(r) >= 2]
        rows = [r for r in rows if not is_table_header(r)]
        conclusion = section_body(body, r"结论") or ""
        if rows and re.search(r"全部完成|全部通过|全部达成|全部验证|100%\s*(?:完成|通过)", conclusion):
            res.add("error", "REP-CONTRADICTION",
                    "§3 列了未验证项，结论却写「全部完成」——这是最典型的假收工")

    if not re.search(r"(为什么.*能失败|能失败|可失败性|可能失败)", body):
        res.add("warn", "REP-NO-FALSIFIABILITY",
                "缺少「这条检查为什么能失败」的说明，PASS 无法证伪")


# ---------------------------------------------------------------------------
# 任务契约书（契约 1）
# ---------------------------------------------------------------------------

BRIEF_SECTIONS = {
    "目标": ("目标",),
    "完成判据": ("完成判据",),
    "歧义决议": ("歧义决议",),
    "状态来源": ("状态来源",),
}


def check_brief(text: str, res: Result) -> None:
    body = strip_fences(text)

    for title, aliases in BRIEF_SECTIONS.items():
        if not any(re.search(r"^#{1,6}\s*.*?" + re.escape(a), body, re.MULTILINE) for a in aliases):
            res.add("error", "BRF-SECTION", f"缺少小节「{title}」")

    if has_placeholder(body):
        res.add("error", "BRF-PLACEHOLDER", "契约书里仍有未填的占位符——留空即未定义即不许开工")

    goal = section_body(body, r"目标")
    if goal is not None:
        if not has_quant(goal) and not re.search(
            r"(返回|小于|大于|等于|不超过|至少|连续|全部|为\s*0|归零|降到|升到)", goal
        ):
            res.add("error", "BRF-GOAL-VAGUE",
                    "目标不可度量：既没有数字/阈值，也没有可观测的终态")

    criteria = section_body(body, r"完成判据")
    if criteria is not None:
        rows = [r for r in parse_table_rows(criteria) if len(r) >= 3]
        rows = [r for r in rows if not is_table_header(r)]
        if not rows:
            res.add("error", "BRF-NO-CRITERIA", "§2 没有任何一条判据")
        for row in rows:
            cmd_cell = " ".join(row[2:]) if len(row) > 2 else ""
            if not has_evidence_cmd(cmd_cell):
                res.add("error", "BRF-CRITERIA-NOCMD",
                        f"判据「{row[1][:30] if len(row) > 1 else '?'}」没有可执行命令——"
                        f"写不出命令的判据不是判据，是愿望")

    ambiguity = section_body(body, r"歧义")
    if ambiguity is not None:
        rows = [r for r in parse_table_rows(ambiguity) if len(r) >= 4]
        rows = [r for r in rows if not is_table_header(r)]
        if not rows:
            res.add("error", "BRF-NO-AMBIGUITY",
                    "歧义决议表为空——多数任务的歧义不是零，空表意味着没找")
        for row in rows:
            chosen = row[3] if len(row) > 3 else ""
            if not chosen.strip() or has_placeholder(chosen):
                res.add("error", "BRF-AMBIGUITY-UNRESOLVED", f"歧义「{row[0][:24]}」没有选定读法")
            elif not re.search(r"(\bA\b|\bB\b|读法|取|定|默认)", chosen, re.IGNORECASE):
                res.add("warn", "BRF-AMBIGUITY-FORM",
                        f"歧义「{row[0][:24]}」的选定值不像 A/B 或明确取值：{chosen[:30]}")

    state = section_body(body, r"状态来源")
    if state is not None:
        if not re.search(r"(\.json|\.jsonl|\.yaml|\.yml|\.md|state/|ledger/)", state):
            res.add("error", "BRF-STATE-SOURCE", "§5 没有指明权威状态文件的路径")

    if re.search(r"(agent\s*自己.*(?:循环|数)|在思考里(?:循环|数)|thinking.*(?:loop|count))", body, re.IGNORECASE):
        res.add("error", "BRF-LOOP-IN-HEAD",
                "把循环放在 thinking 里属于违约；必须交给脚本或 harness")


# ---------------------------------------------------------------------------
# 程序日志（PTC）
# ---------------------------------------------------------------------------

PROGRAM_REQUIRED = [
    ("程序", ("程序", "程序 id", "程序id")),
    ("意图", ("意图",)),
    ("不可逆动作", ("不可逆动作", "不可逆")),
    ("状态读写", ("状态读写", "状态")),
    ("证据", ("证据", "证据块")),
    ("判决", ("判决", "结论")),
    ("可失败性", ("可失败性", "失败条件")),
]

VERDICT_ENUM = ("partial", "pass", "fail")


def check_program_block(block: str, res: Result, ledger: Ledger, line: int) -> None:
    fields = parse_fields(block)
    present: dict[str, str] = {}
    for label, aliases in PROGRAM_REQUIRED:
        key, val = get_field(fields, *aliases)
        if key is None:
            res.add("error", "PRG-FIELD-MISSING", f"缺少栏位「{label}」", line)
            continue
        if not val.strip() or has_placeholder(val):
            res.add("error", "PRG-FIELD-EMPTY", f"栏位「{label}」为空或仍是占位符", line)
            continue
        present[label] = val

    ev = present.get("证据", "")
    if ev:
        exit_m = EXIT_CLAUSE_RE.search(ev)
        cmds = extract_commands(ev)
        if not exit_m:
            res.add("error", "PRG-EVIDENCE-EXIT", "证据里没有退出码（exit=0/1）", line)
        if not cmds:
            res.add("error", "PRG-EVIDENCE-CMD", "证据里没有可执行命令", line)
        # v1 的洞：只要有 exit=0 就算有数字证据。剔掉 exit 子句后必须还有真实输出。
        remainder = strip_exit_clause(ev)
        if exit_m and not has_evidence_out(remainder):
            res.add("error", "PRG-EVIDENCE-VALUE",
                    "证据只有退出码没有输出内容——exit=0 只说明命令没崩，不说明结果正确", line)
        if ledger.loaded:
            rec = None
            for cmd in cmds:
                rec = ledger.find_matching(cmd, ev)
                if rec:
                    break
            if rec is None:
                res.add("error", "LEDGER-NO-CALL",
                        f"证据声称执行过 `{(cmds[0] if cmds else ev)[:50]}`，账本里没有记录", line)
            else:
                if exit_m and rec.exit_code is not None and int(exit_m.group(1)) != rec.exit_code:
                    res.add("error", "LEDGER-EXIT-MISMATCH",
                            f"证据声称 exit={exit_m.group(1)}，账本记录 exit={rec.exit_code}", line)
                if remainder.strip() and not ledger.output_matches(rec, remainder):
                    res.add("error", "LEDGER-OUTPUT-MISMATCH",
                            "证据里的输出与账本记录不一致（疑似改写或编造）", line)

    verdict = present.get("判决", "")
    if verdict:
        low = verdict.strip().lower()
        if not any(low.startswith(v) for v in VERDICT_ENUM):
            res.add("error", "PRG-VERDICT", "判决必须是 pass | fail | partial 之一", line)

    failability = present.get("可失败性", "")
    if failability:
        if re.search(r"(无法失败|不可能失败|总能通过|永远通过|没有失败条件|必然通过)", failability):
            res.add("error", "PRG-NO-FAILABILITY",
                    "声称「无法失败」的检查不是检查，删掉或换一条", line)
        elif not (has_quant(failability) or re.search(r"(若|如果|当|一旦).{2,80}(则|就|会)", failability)):
            res.add("error", "PRG-FAILABILITY-VAGUE",
                    "可失败性必须写成「若 X 不成立则输出 Y」或给出具体阈值", line)

    irrev = present.get("不可逆动作", "")
    if irrev and not re.search(r"^(?:无|没有|none|no|否)", irrev.strip(), re.IGNORECASE):
        if "决策" not in block and not MITIGATION_RE.search(block):
            res.add("error", "PRG-IRREVERSIBLE-NO-GATE",
                    "声明了不可逆动作，但同一块里既没有决策记录也没有缓解措施", line)


# ---------------------------------------------------------------------------
# 类型探测与调度
# ---------------------------------------------------------------------------


def detect_kind(text: str) -> str:
    body = strip_fences(text)
    if PROGRAM_HEAD_RE.search(body):
        return "program"
    if DECISION_HEAD_RE.search(body):
        return "decision"
    if re.search(r"^#{1,6}\s*.*?完成判据", body, re.MULTILINE) and re.search(
        r"^#{1,6}\s*.*?歧义", body, re.MULTILINE
    ):
        return "brief"
    if re.search(r"^#{1,6}\s*.*?(真实输出|未验证)", body, re.MULTILINE):
        return "report"
    return "unknown"


def run_check(kind: str, text: str, ledger_path: str | None) -> Result:
    res = Result()
    ledger = Ledger(ledger_path) if ledger_path else Ledger(None)
    res.evidence_tier = "ledger" if ledger.loaded else "shape-only"

    if kind == "unknown":
        res.add("error", "KIND-UNKNOWN",
                "无法识别文件类型；用 --kind 指定 decision/report/brief/program")
        return res

    # 账本在，但只能看见外层 run_code：命令字面量在源码里就算「对上」，
    # 这不是可证伪校验，必须显式降档并告警，不能继续冒充 ledger 档。
    if ledger.loaded and not ledger.execution_provable():
        res.evidence_tier = "source-literal"
        res.add("warn", "LEDGER-EXECUTION-UNPROVEN",
                "账本里只有 run_code 记录：命令字面量出现在程序源码里即可匹配，"
                "无法证明该命令真的被执行过。--strict 下判失败",
                heuristic=False)

    body = strip_fences(text)

    if kind == "decision":
        heads = list(DECISION_HEAD_RE.finditer(body))
        if not heads:
            res.add("error", "DEC-NONE",
                    "没有找到任何决策块（形如 `## 决策 1 @ 时间`，且不得写在代码围栏里）；"
                    "不可逆动作前必须留档")
            return res
        res.checked_blocks = len(heads)
        for i, head in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            check_decision_block(body[head.start():end], res, ledger, line_at(body, head.start()))

    elif kind == "report":
        res.checked_blocks = 1
        check_report(text, res, ledger)

    elif kind == "brief":
        res.checked_blocks = 1
        check_brief(text, res)

    elif kind == "program":
        heads = list(PROGRAM_HEAD_RE.finditer(body))
        if not heads:
            res.add("error", "PRG-NONE",
                    "没有找到任何程序块（形如 `## 程序 <id> @ 时间`，且不得写在代码围栏里）")
            return res
        res.checked_blocks = len(heads)
        for i, head in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            check_program_block(body[head.start():end], res, ledger, line_at(body, head.start()))

    return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str], *, default_kind: str = "auto", title: str = "unlimited_lint") -> int:
    import argparse

    ap = argparse.ArgumentParser(prog=title)
    ap.add_argument("path", help="待校验的 markdown 文件")
    ap.add_argument("--kind", default=default_kind,
                    choices=["auto", "decision", "report", "brief", "program"])
    ap.add_argument("--ledger", help="utc-ledger 插件落盘的 ledger.jsonl；给了才能校验证据真伪")
    ap.add_argument("--strict", action="store_true", help="把警告也升级为失败")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args(argv)

    try:
        text = read_text_any(args.path)
        kind = args.kind if args.kind != "auto" else detect_kind(text)
        res = run_check(kind, text, args.ledger)
    except InputError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    # --strict 只升级"客观但严重性中等"的警告；启发式提示永不升级。
    upgradable = [f for f in res.warnings if not f.heuristic]
    failed = bool(res.errors) or (args.strict and bool(upgradable))

    if args.json:
        print(json.dumps({
            "file": args.path,
            "kind": kind,
            "ok": not failed,
            "evidence_tier": res.evidence_tier,
            "blocks": res.checked_blocks,
            "errors": [{"code": f.code, "line": f.line, "message": f.message} for f in res.errors],
            "warnings": [{"code": f.code, "line": f.line, "message": f.message} for f in res.warnings],
        }, ensure_ascii=False, indent=2))
    else:
        for f in res.findings:
            print(f.render())
        print(f"\n{title}: {kind} | 块 {res.checked_blocks} | "
              f"错误 {len(res.errors)} | 警告 {len(res.warnings)} | "
              f"证据档 {res.evidence_tier} | {'FAIL' if failed else 'PASS'}")
        if res.evidence_tier == "shape-only":
            print("提示: 未提供 --ledger，只做了证据「形态」检查，"
                  "无法证明这些命令真的被执行过、输出没被改写。"
                  "装 plugins/utc-ledger.mjs 后加 --ledger 可升级为可证伪校验。",
                  file=sys.stderr)

    return 0 if not failed else 1


def sha256_of(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
