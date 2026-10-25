---
name: unlimited-token-ptc
description: 无限tokenPTC版。这是 Programmatic Tool Calling 下的版本：模型只有一个 run_code 工具，其余能力都是程序内 SDK 绑定 await tools.<name>(args)。标准版的七条契约仍然成立，但失效方式变了——模型不再一个动作一个动作地犯懒，而是在一段程序里一次性犯错：错误被 catch 吞掉、被 return {ok:true} 洗白、被 timeoutMs 打断后当成跑完。本版把证据交给运行时账本（utc-ledger 插件旁听 tool/call 与 tool/result），程序自己写的 program-log 必须与账本对得上，编造与改写都会被判失败。多步执行、长流程、有不可逆动作、要下「完成」结论时加载。
whenToUse: 在 PTC 环境（只有 run_code 一个工具，其余能力是程序内 SDK 绑定）下执行超过 3 步的任务、长流程监控、含不可逆动作、或需要给出「做完了」的结论时。框架不支持 PTC 时改用标准版。
---

# 无限tokenPTC版

> 标准版的七条契约全部继承。本文件只写 **PTC 额外引入的失效面**，
> 以及与标准版不同的那一部分（`program` 留档）。

---

## 为什么 PTC 需要单独一版

标准版假定模型「一次犯一个错」，靠逐条的核对与留档拦住。
PTC 下模型只有 `run_code` 一个工具，其余能力是程序内 `await tools.<name>(args)`：

```python
result = await tools.bash(command="df -h /data", description="check disk")
if "9" in result.text:
    return {"ok": True, "msg": "磁盘正常"}
```

这段程序里，**核对、判断、结论、留档全在一处**。标准版的七条契约仍然成立，
但它们的失效方式变了：

| # | PTC 特有的失效 | 表现 | 对应机制 |
| --- | --- | --- | --- |
| 1 | 吞异常 | `try/except: pass`，错误消失，程序返回 ok | `PROGRAM` 证据必须含真实退出码与输出 |
| 2 | 自证判决 | `return {"ok": True}` 又当执行者又当裁判 | 判决必须与账本记录一致 |
| 3 | 摘要代替证据 | 程序里算出结论，日志只写「已完成」 | 证据栏缺输出 → `PRG-EVIDENCE-VALUE` |
| 4 | 隐式账本 | 在程序变量里累计，外部完全看不到 | 状态写文件，或每轮落一行 JSON |
| 5 | 捆绑不可逆 | 读和删写在同一个程序里，没有中间门 | `PRG-IRREVERSIBLE-NO-GATE` |
| 6 | 截断即完成 | `timeoutMs` 打断后当成跑完 | 可失败性必须写成「若 X 则 Y」 |
| 7 | 并发读改写 | 多个 await 并行改同一份状态 | 状态读写栏必须显式声明 |

**第 3 条是本版和 v1 最大的差别。** v1 的 `ptc_lint.py` 有个洞：
证据里只要出现 `exit=0` 就算「有数字证据」——

```
证据: `rm -rf /var/tmp/build-cache` exit=0
```

这行在 v1 下通过。但 `exit=0` 只说明命令没崩，什么都不说明。
v2 把 exit 子句剔掉之后还要求有真实输出，并且与账本比对：
命令真的执行过、输出真的是这个、exit 真的是这个。

---

## 七条契约（继承自标准版，此处只列 PTC 落点）

### 1. 歧义契约

PTC 下尤其危险：程序一次跑完，中途没人能打断你按错的理解。
开工前填 `templates/task-brief.md`，`--kind brief` 校验。

### 2. 状态外置

**PTC 是这一条最容易失守的地方。** 程序里 `count += 1` 写在变量里，
外部完全看不到。规则：

- 每轮状态落一行 JSON（`state/iter.jsonl`），不要只在内存里累计
- 程序结束前把聚合结果写到 `state/aggregate.json`
- 运行时每步会注入当前调用数与报错数（`utc-ledger` 提供），那是权威数字

### 3. 后果门

PTC 下不可逆动作常与读取写在同一段程序里。**拆开：**
先跑只读程序拿到事实，再写决策块，最后跑执行程序。
`PRG-IRREVERSIBLE-NO-GATE` 拦的是「同一块里声明了不可逆动作，
却既没有决策记录也没有缓解措施」。

### 4. 核对免费

程序里的 `await tools.bash(command="... --dry-run")` 不算浪费。

### 5. 信号时相

`timeoutMs` 到期不是「跑完了」，是「被打断」。被打断时程序可能停在任一步，
状态可能半完成——写进报告的必须是「未验证」，不是「已完成」。

### 6. 预算不设限

PTC 的 `run_code` 里循环很便宜，别为了「省一次调用」把核对删掉。

### 7. 可证伪收工

填 `templates/run-report.md` + `templates/program-log.md`，两份都校验：

```bash
python scripts/ptc_lint.py run-report.md --kind report --ledger .utc/ledger.jsonl
python scripts/ptc_lint.py program-log.md --kind program --ledger .utc/ledger.jsonl
```

---

## program 留档格式

```markdown
## 程序 P1 @ 2026-10-25T01:00:00Z
- 程序: <这段程序做什么>
- 意图: <为什么需要它>
- 不可逆动作: <有就具名，没有写「无」>
- 状态读写: <读哪个文件，写哪个文件>
- 证据: `<命令>` exit=0，输出 `<真实输出片段>`
- 判决: pass | fail | partial
- 可失败性: 若 <X 不成立> 则 <输出 Y 或阈值>
```

### 判定规则

| 你写的 | 校验器报的 | 结果 |
| --- | --- | --- |
| 证据只有 `exit=0`，没有输出 | `PRG-EVIDENCE-VALUE` | 失败——exit=0 只说明没崩 |
| 证据没有退出码 | `PRG-EVIDENCE-EXIT` | 失败 |
| 证据没有命令 | `PRG-EVIDENCE-CMD` | 失败 |
| 判决不是 pass/fail/partial | `PRG-VERDICT` | 失败 |
| 可失败性写「无法失败/总是通过」 | `PRG-NO-FAILABILITY` | 失败——那不是检查 |
| 可失败性没有「若…则…」也没有阈值 | `PRG-FAILABILITY-VAGUE` | 失败 |
| 声明不可逆动作但无决策/缓解 | `PRG-IRREVERSIBLE-NO-GATE` | 失败 |
| 命令没在账本里出现过 | `LEDGER-NO-CALL` | 失败——证据不是运行时产生的 |
| exit 与账本不符 | `LEDGER-EXIT-MISMATCH` | 失败——假 exit |
| 输出与账本不符 | `LEDGER-OUTPUT-MISMATCH` | 失败——改写或编造 |

---

## 校验器

```bash
# 推荐：带账本
python scripts/ptc_lint.py program-log.md --kind program --ledger .utc/ledger.jsonl

# 没账本：形态档，会明确告诉你它更弱
python scripts/ptc_lint.py program-log.md --kind program

# 四类 kind
python scripts/ptc_lint.py <file> --kind brief|decision|report|program
```

退出码：`0` 通过 / `1` 判定失败 / `2` 输入或用法错误（不含崩溃）。

**本版与标准版共用同一个判定引擎** `scripts/utc_core.py`，
两份拷贝逐字节相同（自测校验 sha256）。因此两个模式的闸门能力一致，
不会出现「标准版能抓、PTC 版抓不到」的不对称。

---

## 与标准版的差别一览

|  | 标准版 | PTC 版 |
| --- | --- | --- |
| 工具面 | 每个工具一次调用 | 只有 `run_code`，其余是程序内 SDK |
| 主要失效 | 逐动作偷懒、叙述替代核对 | 一段程序里一次性犯错（吞异常、自证判决） |
| 特有留档 | — | `program-log.md`（`--kind program`） |
| 状态外置难点 | 跨步保持的量 | 程序内变量累计 |
| 判定引擎 | `utc_core.py` | 同一份 `utc_core.py`（逐字节相同） |

---

## 安装

### 只装技能（形态档）

把 `unlimited-token-ptc/` 复制到 `~/.dsh/preset-skills/`。

### 装插件（可证伪档，推荐）

在 PTC 预设的 `plugins:` 里加：

```yaml
- id: utc-ledger
  name: 'file:///C:/path/to/unlimited-token-mode/plugins/utc-ledger.mjs'
  config:
    ledgerPath: .utc/ledger.jsonl
    injectState: true
```

路径必须是 `file://` URL，不能是裸的 Windows 绝对路径 `C:\...`——否则 loader 抛
`ERR_UNSUPPORTED_ESM_URL_SCHEME`，预设整条变「加载失败」不进下拉框。

两个模式共用同一个插件文件（证据账本与运行时状态注入与模式无关）。

---

## 自测

```bash
python scripts/selftest.py    # 13 项：core 一致性 + program 类型 + 对抗集 + 编码 + 账本
```

对抗集来自标准版的 `adversarial_suite.py`，通过 `UTC_LINT` 环境变量
指向 `ptc_lint.py` 跑同一批用例——**证明两个模式的闸门能抓住同一批攻击**。

---

## 边界声明

1. **不提高能力上限。** 它只让失败可见，不让模型变聪明。
2. **`shape-only` 与 `source-literal` 两档都不能证明执行过。**
   没装插件时只能看形态；装了插件但账本里只有外层 `run_code` 时，
   只能证明命令写在源码里。只有账本里存在非 `run_code` 记录，才是可证伪的 `ledger` 档。
3. **启发式永不判失败。** 词元相似度这类信号只作提示。
4. **它校验的是留档，不是程序本身。** 一段逻辑错误的程序只要留档诚实，
   仍会通过——本版不替代代码审查。
5. **`run_code` 内部的子调用是否全部落账，取决于框架。**
   `utc-ledger` 旁听的是 `tool/call` 与 `tool/result` 事件；
   PTC 内部的 `tools.<name>()` 若框架不发出这两个事件，账本就看不到。

   **v2.1 实测更正**：这种情况下比对不会「因找不到调用而失败」，而是**反过来通过**——
   账本里记录的是整段程序源码与程序返回值，命令字面量出现在源码里就能匹配上。
   现在校验器会把这种情形标成 `证据档 source-literal` 并给
   `LEDGER-EXECUTION-UNPROVEN` 警告，`--strict` 下判失败。

   所以：`source-literal` 档只证明「源码里写了这条命令」，不证明「它被执行过」。
   报告里引用这类证据时必须自己写明这一点，不能当成 `ledger` 档用。
