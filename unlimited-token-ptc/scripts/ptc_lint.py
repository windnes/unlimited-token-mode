#!/usr/bin/env python3
"""无限token PTC 版 —— 校验器入口。

与标准版共用同一个判定引擎 `utc_core.py`（两份拷贝逐字节相同，selftest 校验 sha256）。
PTC 版多一种文档类型：program（程序日志），用于 run_code 里编排多步程序时的留档。

用法：
    python scripts/ptc_lint.py <file.md> --kind program
    python scripts/ptc_lint.py <file.md> --kind report --ledger .utc/ledger.jsonl
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utc_core import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], default_kind="auto", title="ptc_lint"))
