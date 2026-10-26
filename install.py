#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py —— 把无限token模式（标准版 / PTC 版）装进本机的 DSH profile。

它做三件手工步骤容易做错的事：

1. **整段复制，不重新拼**：直接搬仓库里 `preset/cordis-preset*.yml` 的完整内容
   （含全部工具插件），而不是在这里手写一份精简版。
   预设之间没有继承机制，漏一个插件就等于少一批工具。
2. **绝对路径**：把插件路径改写成绝对路径再写入。
   相对路径在 DSH 里按「谁加载它」解析——顶层 patch 按 patch 文件所在目录，
   复制到别处就静默失效。v1 的文档只说"填路径"，没说这一层。
3. **去重 + 备份 + 幂等**：改 `cordis.patch.yml` 前先建带时间戳的备份；
   安装前先删掉所有同名 preset 条目（包括用户早先手工拷进去的）。
   重复 id 会让 agent-preset-registry 抛 `Duplicate agent preset`，
   整个预设加载失败——这是实测踩到的坑。

用法：
    python install.py --profile core-021                  # 安装两个模式
    python install.py --profile core-021 --dry-run        # 只打印将要做什么
    python install.py --profile core-021 --only std       # 只装标准版
    python install.py --profile core-021 --uninstall      # 移除本工具装的条目
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import pathlib
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(HERE, "preset")
PLUGIN = os.path.normpath(os.path.join(HERE, "unlimited-token-mode", "plugins", "utc-ledger.mjs"))

STD_MARK = "preset-unlimited-token"
PTC_MARK = "preset-unlimited-token-ptc"

GEN_BEGIN = "# ==== 无限token模式（由 install.py 生成：begin）===="
GEN_END = "# ==== 无限token模式（由 install.py 生成：end）===="

PRESET_FILES = {
    STD_MARK: "cordis-preset.yml",
    PTC_MARK: "cordis-preset-ptc.yml",
}

# 技能目录名。装到 $DSH_HOME/preset-skills/<名字>/。
SKILL_DIRS = {
    STD_MARK: "unlimited-token-mode",
    PTC_MARK: "unlimited-token-ptc",
}

# 不参与同步的目录 / 后缀（构建缓存，不是技能内容）
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIX = (".pyc", ".pyo")

NEWLINE = "\n"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_skill_files(root: str) -> list[str]:
    """列出目录下全部文件的相对路径（正斜杠），跳过缓存。

    用于**两端各自实际扫描**再比对，而不是在代码里写死「应该有 18 个文件」。
    写死的数字每加一个文件就过期一次，而且过期时不会报错，只会静默少装。
    """
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(SKIP_SUFFIX):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def diff_skill_tree(src_root: str, dst_root: str) -> dict:
    """比对仓库源与已装副本：缺哪些、多哪些、内容不同的是哪些。

    这是 install.py 验证步骤的唯一依据 —— 不存任何预期数字。
    """
    want = list_skill_files(src_root)
    have = set(list_skill_files(dst_root))
    missing = [f for f in want if f not in have]
    extra = sorted(have - set(want))
    differing: list[str] = []
    for rel in want:
        if rel not in have:
            continue
        a = os.path.join(src_root, rel)
        b = os.path.join(dst_root, rel)
        if sha256_of(a) != sha256_of(b):
            differing.append(rel)
    return {
        "repo_total": len(want),
        "installed_total": len(have),
        "missing": missing,
        "extra": extra,
        "differing": differing,
        "in_sync": not missing and not differing,
    }


def sync_skill_tree(src_root: str, dst_root: str, dry_run: bool) -> dict:
    """把仓库技能目录同步到 preset-skills 下，返回同步前后的比对结果。

    只覆盖、不删除：仓库里已移除的文件会作为 extra 报告出来，但不会自动删，
    因为那是用户机器上的数据，静默删除属于不可逆动作。
    """
    before = diff_skill_tree(src_root, dst_root)
    if dry_run or before["in_sync"]:
        return before

    for rel in list_skill_files(src_root):
        src = os.path.join(src_root, rel)
        dst = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return diff_skill_tree(src_root, dst_root)


def default_dsh_home() -> str:
    return os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline=NEWLINE) as fh:
        fh.write(text)


def to_file_url(path: str) -> str:
    """把本地绝对路径转成 file:// URL。

    preset 里不能写裸的 Windows 绝对路径 `C:\\...`：cordis loader 对不以 `.`
    开头的 name 直接走 `import(name)`，而 Node 的 ESM loader 只接受
    file:/data:/node: 三种 scheme，会抛 ERR_UNSUPPORTED_ESM_URL_SCHEME。
    表现是插件静默不加载、预设整条变成「加载失败」而不进下拉框。
    """
    return pathlib.Path(path).resolve().as_uri()


def preset_text(mark: str, plugin_url: str, dsh_home: str | None = None) -> str:
    """读取仓库里的预设文件，把相对路径全部换成绝对路径。

    要换的三处都是**按加载者解析**的相对路径，装到别处就静默失效：

    1. 插件路径 `../unlimited-token-mode/plugins/utc-ledger.mjs` → file:// URL
    2. `ledgerPath: .utc/ledger.jsonl` → 账本是本机运行数据，落在 $DSH_HOME/.utc/
       下，不跟着某个会话的工作目录走，也不能写进仓库 checkout（否则 git 一操作就脏）
    3. persona 正文里的 `python scripts/xxx_lint.py` 与 `--ledger .utc/ledger.jsonl`
       → 技能目录下脚本的绝对路径。工作目录通常既不是技能目录也不是账本目录，
       照抄 persona 原文必然 exit 2。
    """
    src = os.path.join(PRESET_DIR, PRESET_FILES[mark])
    if not os.path.exists(src):
        raise SystemExit(f"找不到预设文件: {src}")
    text = read(src)

    home = dsh_home or default_dsh_home()
    skills_root = os.path.join(home, "preset-skills")
    skill_dir = os.path.join(skills_root, SKILL_DIRS[mark])
    ledger_abs = os.path.join(home, ".utc", "ledger.jsonl")

    # 1) 插件路径
    replaced, n = re.subn(
        r"^(\s*)name:\s*'\.\./unlimited-token-mode/plugins/utc-ledger\.mjs'\s*$",
        lambda m: m.group(1) + "name: '" + plugin_url + "'",
        text,
        flags=re.MULTILINE,
    )
    if n == 0:
        print(f"警告: {src} 里没找到相对插件路径，将按原样安装", file=sys.stderr)
    text = replaced

    # 2) ledgerPath
    text, n = re.subn(
        r"^(\s*)ledgerPath:\s*\.utc/ledger\.jsonl\s*$",
        lambda m: m.group(1) + "ledgerPath: '" + ledger_abs.replace("\\", "/") + "'",
        text,
        flags=re.MULTILINE,
    )
    if n == 0:
        print(f"警告: {src} 里没找到 ledgerPath 相对路径", file=sys.stderr)

    # 3) persona 正文里的占位符
    #    模板里写 `<技能目录>` / `<账本绝对路径>` 两个占位符（见 preset/prompt-prefix*.md），
    #    这里填成安装后的真实绝对路径。留占位符比留相对路径更糟：模型会照字面执行。
    lint_dir = skill_dir.replace("\\", "/")
    ledger_slash = ledger_abs.replace("\\", "/")
    text, n1 = re.subn(re.escape("<技能目录>"), lambda m: lint_dir, text)
    text, n2 = re.subn(re.escape("<账本绝对路径>"), lambda m: ledger_slash, text)
    if n1 == 0 or n2 == 0:
        print(f"警告: {src} 的 persona 里占位符没找全"
              f"（技能目录 {n1} 处 / 账本 {n2} 处）", file=sys.stderr)

    #    兼容旧模板里可能残留的相对写法
    lint_name = "ptc_lint.py" if mark == PTC_MARK else "unlimited_lint.py"
    text = text.replace("python scripts/" + lint_name,
                        'python "' + lint_dir + '/scripts/' + lint_name + '"')
    text = text.replace("--ledger .utc/ledger.jsonl",
                        '--ledger "' + ledger_slash + '"')

    return text.rstrip(NEWLINE)


def _split_generated_block(text: str) -> tuple[str, str, str] | None:
    """把文本切成「块前 / 块内 / 块后」，块标记本身不包含在任何一段里。

    找不到完整的一对标记时返回 None。
    """
    begin = text.find(GEN_BEGIN)
    if begin < 0:
        return None
    end = text.find(GEN_END, begin)
    if end < 0:
        # 只有 begin 没有 end：退化成「从这里到文件末尾都是块内」，
        # 但这不是正常状态，保留旧行为由调用方处理。
        return None
    return text[:begin], text[begin + len(GEN_BEGIN):end], text[end + len(GEN_END):]


def _foreign_rows(inner: str) -> list[str]:
    """从生成块内部挑出**不是本工具写的**顶层条目，按原始顺序返回。

    判定依据：本工具写的是若干 `- insert:` 块，块内条目**有缩进**；
    外来条目是顶层条目（行首无缩进、以 `- ` 开头）。因此：

    - 行首 `- insert:`（无缩进）→ 开始一个本工具区块，其后续缩进行归它
    - 行首 `- xxx`（无缩进且不是 insert）→ 外来条目，开始一个新条目
    - 缩进行 → 属于当前条目
    - 空行与注释行 → **跳过**，不算任何条目

    注释行必须跳过：预设模板本身就是以一段注释开头（`# --- 无限token标准版 ---`
    之类），它属于本工具生成的内容，每次安装都会重新产生。若把注释当外来行抬出
    块外，下一轮安装又会新生成一份，于是每次重装都多出一份重复注释——这正是
    第一版救援逻辑引入的 bug（一次安装 +10 行）。

    为什么要做救援：dsh-ops 的配置迁移会用 configEditor.edit() 把 `dsh-ops-file`
    行追加到 profile patch 末尾，正好落在生成块内部。旧实现按区间整段删除，于是
    每次重装都删掉这一行，插件下次启动再写回，文件工具在中间那一刻是真的缺失。
    """
    entries: list[tuple[bool, list[str]]] = []  # (is_insert, lines)
    for line in inner.splitlines():
        bare = line.strip()
        if not bare or bare.startswith("#"):
            continue
        top_level = line == line.lstrip() and bare.startswith("- ")
        if top_level:
            entries.append((bare.startswith("- insert:"), [line]))
            continue
        if not entries:
            # 块内第一行就是缩进行：无法归属，按外来行处理，宁可多救不可少救
            entries.append((False, [line]))
            continue
        entries[-1][1].append(line)

    rows = [NEWLINE.join(lines) for is_insert, lines in entries if not is_insert]
    return rows


def strip_existing(text: str) -> tuple[str, bool]:
    """移除所有已存在的目标 preset 条目，以及本工具此前生成的区块。

    只删自己生成的块是不够的：用户可能早先手工拷过同样的 preset，
    留着就会产生重复 id。

    但反过来，块里可能混进**别人写的**行（见 _foreign_rows 的说明）。
    那些行不能删，必须搬到块外保留，否则每次重装都会删掉它们、
    下一次启动又被插件写回来，形成「删一次加一次」的循环。
    """
    changed = False

    # 1) 摘掉本工具生成的整段，但把块内的外来行救出来，放到新块之前
    rescue: list[str] = []
    parts = _split_generated_block(text)
    if parts is not None:
        head, inner, tail = parts
        rescue = _foreign_rows(inner)
        text = head + tail
        changed = True
    elif GEN_BEGIN in text:
        # 只有 begin 没有 end：退化成保留 begin 之前的部分（旧行为）
        text = text[: text.find(GEN_BEGIN)]
        changed = True

    if rescue:
        # 插到块外：紧跟在原有内容之后，保持与原有行的先后次序。
        # 末尾补一个空行，避免与随后追加的 GEN_BEGIN 黏在一起。
        text = text.rstrip(NEWLINE) + NEWLINE + NEWLINE + NEWLINE.join(rescue) + NEWLINE

    # 2) 再按锚点删掉任何声明了目标 preset id 的顶层 insert 块
    lines = text.splitlines()
    out: list[str] = []
    targets = ["- id: " + mark for mark in (STD_MARK, PTC_MARK)]
    i = 0
    while i < len(lines):
        if lines[i].startswith("- insert:"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("- insert:"):
                j += 1
            chunk = lines[i:j]
            body = NEWLINE.join(chunk)
            if any(t in body for t in targets):
                changed = True
                i = j
                continue
            out.extend(chunk)
            i = j
            continue
        out.append(lines[i])
        i += 1
    return NEWLINE.join(out), changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="core-021", help="DSH profile 名（默认 core-021）")
    ap.add_argument("--dsh-home", default=None, help="DSH_HOME（默认 ~/.dsh）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["std", "ptc", "both"], default="both")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    dsh_home = args.dsh_home or default_dsh_home()
    profile_dir = os.path.join(dsh_home, "profiles", args.profile)
    patch_path = os.path.join(profile_dir, "cordis.patch.yml")
    if not os.path.exists(patch_path):
        print(f"错误: 找不到 {patch_path}", file=sys.stderr)
        print("提示: 用 --profile 指定已存在的 profile。", file=sys.stderr)
        return 2
    if not os.path.exists(PLUGIN):
        print(f"错误: 找不到插件 {PLUGIN}", file=sys.stderr)
        return 2

    text = read(patch_path)
    cleaned, removed = strip_existing(text)

    if args.uninstall:
        if not removed:
            print("没有找到本工具安装的条目，无需移除。")
            return 0
        if args.dry_run:
            print("将移除 preset-unlimited-token / preset-unlimited-token-ptc 相关条目")
            return 0
        backup = patch_path + f".bak-uninstall-{dt.datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(patch_path, backup)
        write(patch_path, cleaned)
        print(f"已移除。备份: {backup}")
        return 0

    wanted = ([STD_MARK] if args.only == "std"
              else [PTC_MARK] if args.only == "ptc"
              else [STD_MARK, PTC_MARK])

    chunks: list[str] = [GEN_BEGIN, ""]
    for mark in wanted:
        chunks.append(preset_text(mark, to_file_url(PLUGIN), dsh_home))
        chunks.append("")
    chunks.append(GEN_END)

    new_text = cleaned.rstrip(NEWLINE) + NEWLINE + NEWLINE + NEWLINE.join(chunks) + NEWLINE

    skills_root = os.path.join(dsh_home, "preset-skills")

    if args.dry_run:
        print(f"profile : {patch_path}")
        print(f"插件    : {to_file_url(PLUGIN)}")
        print(f"将写入  : {', '.join(wanted)}")
        if removed:
            print("（会先删掉旧的同名条目与旧生成区块）")
        print("")
        print("--- 技能目录比对（仓库 vs 已装副本）---")
        for mark in wanted:
            src_root = os.path.join(HERE, SKILL_DIRS[mark])
            dst_root = os.path.join(skills_root, SKILL_DIRS[mark])
            d = diff_skill_tree(src_root, dst_root)
            print(f"  {SKILL_DIRS[mark]:26s} 仓库 {d['repo_total']:2d} 个 / 已装 {d['installed_total']:2d} 个"
                  f" / 缺 {len(d['missing'])} / 内容不同 {len(d['differing'])}"
                  f" / {'已同步' if d['in_sync'] else '需同步'}")
        print("")
        print("--- 预览（前 40 行）---")
        preview = NEWLINE.join(chunks).splitlines()
        print(NEWLINE.join(preview[:40]))
        print(f"...（共 {len(preview)} 行）")
        return 0

    backup = patch_path + f".bak-unlimited-v2-{dt.datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(patch_path, backup)
    write(patch_path, new_text)

    print(f"已安装到 {patch_path}")
    print(f"备份    : {backup}")
    print(f"插件    : {to_file_url(PLUGIN)}")
    print("")

    # 技能目录同步 —— 不存任何预期文件数，两端各扫一遍再比。
    # 之前 install.py 只写 cordis.patch.yml、完全不碰 preset-skills，
    # 于是仓库更新了、本机装的还是旧版，而自测仍能通过（旧用例自洽）。
    print("技能目录同步：")
    failed = False
    for mark in wanted:
        name = SKILL_DIRS[mark]
        src_root = os.path.join(HERE, name)
        dst_root = os.path.join(skills_root, name)
        before = diff_skill_tree(src_root, dst_root)
        after = sync_skill_tree(src_root, dst_root, False)
        print(f"  {name:26s} 仓库 {before['repo_total']:2d} 个 / 同步前已装 {before['installed_total']:2d} 个"
              f" -> 同步后 {after['installed_total']:2d} 个"
              f"（缺 {len(before['missing'])} / 内容不同 {len(before['differing'])}）")
        if after["missing"]:
            failed = True
            print(f"    ✗ 仍有文件未同步: {', '.join(after['missing'])}", file=sys.stderr)
        if after["differing"]:
            failed = True
            print(f"    ✗ 仍有内容不同的文件: {', '.join(after['differing'])}", file=sys.stderr)
        if after["extra"]:
            print(f"    提示: 已装目录里多出 {len(after['extra'])} 个仓库没有的文件"
                  f"（不会自动删除）: {', '.join(after['extra'])}")

    if failed:
        print("", file=sys.stderr)
        print("错误: 技能目录未能与仓库一致，安装不完整。", file=sys.stderr)
        return 1

    print("")
    print("下一步：")
    print("  1. 重启 DSH（预设清单在启动时组装，不重启看不到新条目）")
    print("  2. 在模式下拉里选「无限token标准版」或「无限tokenPTC版」")
    print("  3. 新会话里确认 .utc/ledger.jsonl 出现（说明账本插件生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
