#!/usr/bin/env python3
"""无限token标准版（unlimited token mode）—— 校验器入口。

本文件只是 CLI 外壳，判定逻辑全在 `utc_core.py`（与 PTC 版逐字节相同）。

用法：
    python scripts/unlimited_lint.py <file.md> --kind decision
    python scripts/unlimited_lint.py <file.md> --kind report --ledger .utc/ledger.jsonl

v1 -> v2 改了什么（为什么 v1 被打穿）：
    v1 的证据是模型自己写的散文，lint 只能做字符串交集，
    于是「抄一遍字段 + 编一句我确认过了」就是合法通过。
    v2 把证据交给运行时（plugins/utc-ledger.mjs 旁听 tool/call 与 tool/result），
    模型无法写 ledger.jsonl。给了 --ledger 之后：
      编造核对  = 账本里找不到那次调用
      改写输出  = 声称的输出不是真实输出的子串
      假 exit   = 与账本记录的退出码不符
    没给 --ledger 时退化为"证据形态检查"，输出里会明示 evidence_tier=shape-only。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utc_core import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], default_kind="auto", title="unlimited_lint"))
