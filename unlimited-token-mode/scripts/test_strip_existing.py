"""strip_existing() 必须保住生成块内的外来行。

背景：dsh-ops 的配置迁移会用 configEditor.edit() 把 dsh-ops-file 行追加到
profile patch 的末尾，正好落在 install.py 的生成块（GEN_BEGIN..GEN_END）之间。
旧实现按区间删除，把这一行一起删了；插件下次启动再写回，于是每次重装
都删一次、每次重启都加一次。文件工具在中间那一刻是真的缺失。

本测试锁定的是：重装不得删除本工具没写过的行。

install.py 只在仓库里，不在已安装的技能副本里。找不到它时本测试**跳过**
（输出 SKIP、退出码 0），因为已装副本本来就不该带安装器。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..")))

try:
    import install  # noqa: E402
except ModuleNotFoundError:
    print("SKIP  找不到 install.py（本测试只在仓库里可跑，已装副本不含安装器）")
    sys.exit(0)

FOREIGN_ROW = """- id: dsh-ops-file
  name: dsh-ops/file
  config:
    promptPolicy: true
    required: false
    configUpdateVersion: 2
"""


def build(block_body: str) -> str:
    return (
        "[] 占位前的用户配置\n"
        "- id: dsh-ops-shell\n"
        "  name: dsh-ops/shell\n"
        "  config:\n"
        "    promptPolicy: true\n"
        "\n"
        + install.GEN_BEGIN
        + "\n"
        + block_body
        + "\n"
        + install.GEN_END
        + "\n"
    )


# 真实模板里 preset 一定包在 `- insert:` 里，缩进 4 空格；照这个形状构造。
PRESET_INSERT = (
    "- insert:\n"
    "    - id: preset-unlimited-token\n"
    "      name: 无限token标准版\n"
    "      config:\n"
    "        order: 5\n"
)


def case_foreign_row_survives() -> tuple[bool, str]:
    """生成块内的外来行必须被搬到块外保留，不能被删。"""
    text = build(PRESET_INSERT + FOREIGN_ROW)
    out, changed = install.strip_existing(text)

    if not changed:
        return False, "changed 应为 True（确实删掉了生成块）"
    if "dsh-ops-file" not in out:
        return False, "外来行 dsh-ops-file 被删掉了"
    if "preset-unlimited-token" in out:
        return False, "本工具自己的 preset 行没有被删"

    # 输出里该行必须位于新的生成块之外，否则下次重装还会被删
    b = out.find(install.GEN_BEGIN)
    e = out.find(install.GEN_END, b if b >= 0 else 0)
    idx = out.find("dsh-ops-file")
    if b >= 0 and e > b and b < idx < e:
        return False, "外来行仍落在生成块区间内，下次重装还会被删"
    return True, "外来行已搬到块外，本工具行已删"


def case_clean_block() -> tuple[bool, str]:
    """块内只有本工具内容时，行为与旧版一致：整块消失，不产生额外行。"""
    text = build("    - id: preset-unlimited-token\n      name: 无限token标准版\n")
    out, changed = install.strip_existing(text)
    if not changed:
        return False, "changed 应为 True"
    if "dsh-ops-file" in out:
        return False, "不该凭空产生 dsh-ops-file 行"
    if install.GEN_BEGIN in out or install.GEN_END in out:
        return False, "生成块标记应被清掉"
    return True, "干净块被整段移除"


def case_no_block() -> tuple[bool, str]:
    """没有生成块时不应改动内容。"""
    text = "- id: dsh-ops-shell\n  name: dsh-ops/shell\n"
    out, changed = install.strip_existing(text)
    if changed:
        return False, "没有生成块时 changed 应为 False"
    if out.strip() != text.strip():
        return False, "内容被改动了"
    return True, "无块时不动"


def case_idempotent() -> tuple[bool, str]:
    """对同一份输出连跑两次，第二次不应再变化（旧版这里 idempotent=False）。"""
    preset_body = "    - id: preset-unlimited-token\n      name: 无限token标准版\n"
    text = build(preset_body + FOREIGN_ROW)
    once, _ = install.strip_existing(text)
    twice, changed2 = install.strip_existing(once)
    if changed2:
        return False, "第二次调用仍然 changed=True（不幂等）"
    if once != twice:
        return False, "两次输出不一致"
    return True, "幂等"


def case_foreign_row_idempotent() -> tuple[bool, str]:
    """外来行被搬到块外后，再跑一次不能被当成新的块内内容。"""
    preset_body = "    - id: preset-unlimited-token\n      name: 无限token标准版\n"
    text = build(preset_body + FOREIGN_ROW)
    once, _ = install.strip_existing(text)
    twice, _ = install.strip_existing(once)
    if "dsh-ops-file" not in twice:
        return False, "第二次处理后外来行丢了"
    if twice.count("dsh-ops-file") > 1:
        return False, "外来行被复制成多份"
    return True, "外来行只保留一份"


def case_multiple_foreign_rows() -> tuple[bool, str]:
    """多个外来行都要保住，且保持原顺序。"""
    extra = "- id: dsh-ops-panel\n  name: dsh-ops/panel\n  disabled: true\n"
    preset_body = "    - id: preset-unlimited-token\n      name: 无限token标准版\n"
    text = build(preset_body + FOREIGN_ROW + extra)
    out, _ = install.strip_existing(text)
    if "dsh-ops-file" not in out:
        return False, "dsh-ops-file 丢了"
    if "dsh-ops-panel" not in out:
        return False, "dsh-ops-panel 丢了"
    if out.find("dsh-ops-file") > out.find("dsh-ops-panel"):
        return False, "外来行顺序被打乱"
    return True, "两个外来行都保住且顺序不变"


def case_full_install_twice() -> tuple[bool, str]:
    """连续两次「完整安装」不得让块内注释累加。

    这一条针对第一版救援逻辑引入的真 bug：模板本身以注释开头，
    救援逻辑把注释当外来行抬到块外，下一轮安装又新生成一份注释，
    于是每次重装多 10 行。只测 strip_existing() 看不出来，必须走
    完整的「strip + 重新拼装」两轮。
    """
    comment = "# --- 无限token标准版 ---\n# 为什么需要它：模型判断力可信、账本不可信。\n"
    body = comment + PRESET_INSERT + FOREIGN_ROW
    text = build(body)

    def full_install(src: str) -> str:
        cleaned, _ = install.strip_existing(src)
        chunks = [install.GEN_BEGIN, ""]
        chunks.append(comment + PRESET_INSERT)
        chunks.append("")
        chunks.append(install.GEN_END)
        new = install.NEWLINE.join(chunks)
        return cleaned.rstrip(install.NEWLINE) + install.NEWLINE + install.NEWLINE + new + install.NEWLINE

    one = full_install(text)
    two = full_install(one)
    three = full_install(two)

    c1 = one.count("# --- 无限token标准版 ---")
    c2 = two.count("# --- 无限token标准版 ---")
    c3 = three.count("# --- 无限token标准版 ---")
    if not (c1 == c2 == c3 == 1):
        return False, f"注释重复累加：一轮 {c1} 处 / 二轮 {c2} 处 / 三轮 {c3} 处"
    if one.count("dsh-ops-file") != 1:
        return False, "dsh-ops-file 数量不是 1"
    if not (len(one.splitlines()) == len(two.splitlines()) == len(three.splitlines())):
        return False, (f"行数在增长：{len(one.splitlines())} -> "
                       f"{len(two.splitlines())} -> {len(three.splitlines())}")
    return True, f"三轮安装行数稳定在 {len(three.splitlines())} 行，注释只有 1 份"


CASES = [
    ("生成块内外来行必须幸存并搬到块外", case_foreign_row_survives),
    ("干净生成块整段移除", case_clean_block),
    ("没有生成块时不动", case_no_block),
    ("幂等：连跑两次第二次不变", case_idempotent),
    ("外来行搬到块外后再跑一次不丢不重", case_foreign_row_idempotent),
    ("多个外来行都保住且保持顺序", case_multiple_foreign_rows),
    ("连续三轮完整安装：注释不累加、行数稳定", case_full_install_twice),
]


def main() -> int:
    failed = 0
    for name, fn in CASES:
        try:
            ok, msg = fn()
        except Exception as exc:  # noqa: BLE001 - 测试用例自身的崩溃也算失败
            ok, msg = False, f"用例崩溃: {type(exc).__name__}: {exc}"
        print(("ok   " if ok else "FAIL ") + name + " —— " + msg)
        if not ok:
            failed += 1
    print(f"\nstrip_existing: {len(CASES) - failed}/{len(CASES)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
