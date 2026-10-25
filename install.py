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


def preset_text(mark: str, plugin_url: str) -> str:
    """读取仓库里的预设文件，把插件路径换成绝对 file:// URL。"""
    src = os.path.join(PRESET_DIR, PRESET_FILES[mark])
    if not os.path.exists(src):
        raise SystemExit(f"找不到预设文件: {src}")
    text = read(src)
    replaced, n = re.subn(
        r"^(\s*)name:\s*'\.\./unlimited-token-mode/plugins/utc-ledger\.mjs'\s*$",
        lambda m: m.group(1) + "name: '" + plugin_url + "'",
        text,
        flags=re.MULTILINE,
    )
    if n == 0:
        print(f"警告: {src} 里没找到相对插件路径，将按原样安装", file=sys.stderr)
    return replaced.rstrip(NEWLINE)


def strip_existing(text: str) -> tuple[str, bool]:
    """移除所有已存在的目标 preset 条目，以及本工具此前生成的区块。

    只删自己生成的块是不够的：用户可能早先手工拷过同样的 preset，
    留着就会产生重复 id。
    """
    changed = False

    # 1) 先删本工具生成的整段，保证反复运行不会越积越多
    begin = text.find(GEN_BEGIN)
    if begin >= 0:
        end = text.find(GEN_END, begin)
        if end >= 0:
            text = text[:begin] + text[end + len(GEN_END):]
        else:
            text = text[:begin]
        changed = True

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
        chunks.append(preset_text(mark, to_file_url(PLUGIN)))
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
