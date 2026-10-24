# Unlimited Token Mode

**English** | [中文](#中文)

> Also known as **"白饭管够模式"** (All-You-Can-Eat Rice Mode) — because the whole point is to stop the model from going into power-saving mode.

An external governance layer for models whose *judgment* can be trusted but whose *bookkeeping* cannot.

This is not a prompt-trick collection. It is a set of **hard rules + scripts + a validator** that move the model's ledger, its self-checks, and its definition of "done" out of its thinking and into something verifiable.

---

## What it fixes

Five failure classes **reproduced** in long-horizon tasks on models like `deepseek-v4.1-flash-1017`. Every row is backed by measured numbers, not guesswork.

| # | Failure | Symptom | Countermeasure |
|---|---------|---------|----------------|
| 1 | **Budget counting inside thinking** | Ask it to "run the script 10 times then produce a statistics report" and it tallies rounds and running totals in its reasoning, drifts mid-way, and the final numbers don't match | Loops go to scripts, ledgers go to files |
| 2 | **"Check the state once first"** | Prompt says "look at current state, then monitor" → it either forgets to check, or treats the first snapshot as authoritative and never refreshes | State is injected by the runtime; never write "check once first" |
| 3 | **Ambiguity never clarified** | Any ambiguous quantifier in the prompt (at most / at least / default / normal) → it picks one reading, starts working, and never circles back | Turn ambiguity into a contract before starting |
| 4 | **Manufactured budget scarcity (stinginess)** | "Save as many tokens as you can", "you have limited steps" makes it economize by **cutting self-checks**, not by cutting waste. Eats rice, does no work | Forbid manufactured scarcity; verification is always free |
| 5 | **Narration instead of verification** | It says "let me check", then answers itself in its reasoning — no tool call actually happened | "Done" must be falsifiable |

Item 4 is the one the original author considers **most severe**. Repository-side measurements sharpen it:

- **"It hoards a budget that refreshes" — disproved.** Budget utilization was 0.98–1.00; no idle cycles.
- **"It verifies less once verification costs money" — confirmed.** Charging $1 per query dropped the number of rounds that actually asked from 8/8 to 5/8.

So what is forbidden is **manufacturing a sense of scarcity**, not setting limits.

---

## The seven contracts

Each one = a hard rule + a trigger condition + an artifact. Violating one is a task failure, not a style issue.

1. **Ambiguity contract** — Before acting, list every word that can be read more than one way. Either ask (all at once, ≤3 questions, each with a recommended answer and reasoning), or pin it down explicitly in the kickoff brief. No silent defaults.
2. **Externalize state** — Anything that accumulates, decrements, or must survive across steps (loop counters, budgets, balances, progress, retry counts) never lives in thinking. Loops run as scripts; ledgers are written to `state/*.json`.
3. **Consequence gate** — Before any irreversible action, write out second- and third-order consequences and **name the entities**. Naming must change the action; naming a risk without changing the action counts as not having done it.
4. **Verification is free** — dry-run / check / preview / query never counts as waste. Before saying "can't do it / not feasible / no options left", you must actually check once.
5. **Signal phase** — Classify every signal as **ex ante** or **ex post**. An ex-post notification (alert, interrupt, fallback, "it'll get blocked") is not a reason to wait; it only lets you cut losses — by the time it arrives, the thing already happened.
6. **No manufactured scarcity** — Don't create artificial scarcity for yourself. Save by eliminating duplicate actions, never by eliminating verification.
7. **Falsifiable finish** — Saying "done" requires **a check that could have failed but didn't**, plus its real output.

Three gears keyed to **irreversibility × scale**: light (read-only, instant rollback, ≤3 steps) / standard (writes, counting) / strict (irreversible, externally visible, AIOps). When unsure, shift up a gear.

---

## Repository layout

```
unlimited-token-mode/
├── SKILL.md                          # Main skill file (seven contracts, three gears, five startup steps, banned-phrase list)
├── references/
│   ├── evidence-map.md               # Measured numbers and sources per contract, plus disproved practices (don't copy them)
│   ├── consequence-gate.md           # Full consequence-gate derivation template + two worked examples
│   ├── harness-patterns.md           # Code patterns: externalized loops, state files, environment injection
│   └── prompt-compiler.md            # Compile any request into an "unlimited token mode" prompt
├── templates/
│   ├── task-brief.md                 # Task contract (ambiguity resolution table)
│   ├── decision-log.md               # Decision block (second/third-order consequences)
│   └── run-report.md                 # Falsifiable finish report
└── scripts/
    ├── unlimited_lint.py             # Artifact validator
    └── selftest.py                   # Validator self-test (19 cases)
```

Also included:

- `preset/cordis-preset.yml` — the DeepSeek Harness agent preset block (insert directly into `cordis.patch.yml`).
- `preset/prompt-prefix.md` — the full resident governance prefix, usable as a system prompt in any other framework.

---

## Usage

### Option 1: DeepSeek Harness preset (recommended)

1. Copy the whole `unlimited-token-mode/` directory to `~/.dsh/preset-skills/unlimited-token-mode`.
2. Insert the contents of `preset/cordis-preset.yml` into `~/.dsh/profiles/<your-profile>/cordis.patch.yml`.
3. Restart DSH and pick "无限token模式" from the mode dropdown in the session header.

The preset carries a resident prefix (seven hard rules + five startup steps + banned-phrase list) that is present in every conversation — **you do not need to invoke `/unlimited-token-mode` manually**. The skill itself is loaded only when you need templates, scripts, or the full criteria.

### Option 2: Any agent framework

Inject `preset/prompt-prefix.md` as part of the system prompt, and make `SKILL.md` and `templates/` readable to the model.

### Validator

```bash
python scripts/selftest.py                     # validator self-test, all cases should pass
python scripts/unlimited_lint.py <artifact>    # validate a decision log / run report / task brief
python scripts/unlimited_lint.py <artifact> --strict
```

---

## Boundaries

- For read-only, one-off small tasks the full kit is waste — use the light gear.
- If the **framework itself** already enforces counting and verification (harness injection), contracts 2 and 4 reduce to "confirm the injection exists".
- This mode treats **process defects**, not capability limits. It stops the model from making known mistakes; it does not make it smarter.

---

## Origin

The problem list and measured data come from `AGENT-CAPABILITY-REPORT.md` in [karminski/deepseek-v4.1-flash-1017-issue-pov](https://github.com/karminski/deepseek-v4.1-flash-1017-issue-pov), plus the author's usage notes.

The author's one-line summary: **it writes code just fine, but using it for AIOps requires keeping your wits about you.**

---

# 中文

[English](#unlimited-token-mode) | **中文**

> 又名 **白饭管够模式**。

给「判断力可信、账本不可信」的模型加装一层外置治理。

不是提示词技巧合集，是一套**硬规则 + 脚本 + 校验器**：把模型的账本、核对、收工判据，从 thinking 里搬到外面。

---

## 这个模式治什么

针对 `deepseek-v4.1-flash-1017` 一类模型在长流程任务里**已复现**的五类缺陷。每一条都有实测数字，不是猜测。

| # | 缺陷 | 现象 | 对策 |
|---|------|------|------|
| 1 | **thinking 里做预算计数** | 让它「跑 10 次然后出统计报告」，它会在推理里心算轮次和累计值，中途漂移，最后报告的数字对不上 | 循环交给脚本，账本交给文件 |
| 2 | **「先查一次状态」** | 提示词写「先看一下当前状态，然后监控」→ 要么忘了检查，要么把第一次的快照当权威，之后再也不刷新 | 状态由运行时注入，绝不写「先查一次」 |
| 3 | **歧义不澄清** | 提示词里任何有歧义的量词（最多/至少/默认/正常），它直接取一个读法开工，且从不回头核实 | 开工前把歧义谈成契约 |
| 4 | **制造预算稀缺（抠门）** | 「尽量省 token」「步数有限」会让它用**减少核对**来省，而不是减少浪费。真·吃米不干活 | 禁止制造稀缺感；核对永远免费 |
| 5 | **用叙述替代核对** | 说「让我检查一下」，然后在推理里自己回答了自己——没有真的调用工具 | 收工必须可证伪 |

第 4 条是作者认为**最严重**的一条。仓库侧的实测把它精确化了：

- **「囤积会刷新的额度」已被证伪** —— 额度利用率 0.98–1.00，没有空转周期。
- **「核对一旦要钱就少做」已被证实** —— 问一次收 1 元，去问的局数从 8/8 掉到 5/8。

所以禁的是**制造稀缺感**，不是禁止设限。

---

## 七条契约

每条 = 一条硬规则 + 触发条件 + 产物。违反即任务失败，不是「风格问题」。

1. **歧义契约** —— 动手前把所有可多种读法的词列出来，要么问（一次问完，≤3 问，每问自带推荐答案），要么定死写进开工简报。不许默认。
2. **状态外置** —— 任何需要累加、递减、跨步保持的量（循环次数、预算、余额、进度、重试计数）一律不放在 thinking 里。循环用脚本跑，账本写 `state/*.json`。
3. **后果推演门** —— 不可逆动作前写出二阶三阶后果，**必须点名实体**。点名之后必须改变动作，只点名不改动作等于没做。
4. **核对免费** —— dry-run / check / preview / 查询永远不算浪费。说「做不到 / 没有可选项」之前必须先真查一次。
5. **信号时相** —— 每个信号先分**事前**还是**事后**。事后通知（告警、中断、兜底、会被拦下）不能当作「可以先等着」的理由，只能用来止损。
6. **预算不设限** —— 不给自己制造稀缺。要省就省重复动作，不省核对。
7. **可证伪收工** —— 说「做完了」必须附带**一个本来可以失败、但没有失败的检验**，以及它的真实输出。

按 **不可逆性 × 规模** 分三档：轻（只读、秒回滚、≤3 步）/ 标准（有写入、有计数）/ 严格（不可逆、外部可见、AIOps）。拿不准就上一档。

---

## 仓库内容

```
unlimited-token-mode/
├── SKILL.md                          # 技能主文件（七条契约、三档齿轮、开工五步、禁用清单）
├── references/
│   ├── evidence-map.md               # 七条契约的实测数字、出处，以及已被证伪的错误做法
│   ├── consequence-gate.md           # 后果门完整推演模板 + 两个 worked example
│   ├── harness-patterns.md           # 循环外置、状态文件、环境注入的代码范式
│   └── prompt-compiler.md            # 把任意需求编译成「无限token模式提示词」
├── templates/
│   ├── task-brief.md                 # 任务契约书（歧义决议表）
│   ├── decision-log.md               # 决策块（二阶三阶后果）
│   └── run-report.md                 # 可证伪收工报告
└── scripts/
    ├── unlimited_lint.py             # 产物校验器
    └── selftest.py                   # 校验器自测（19 个用例）
```

另附：

- `preset/cordis-preset.yml` —— DeepSeek Harness 的 Agent 预设配置块（可直接 insert 进 `cordis.patch.yml`）。
- `preset/prompt-prefix.md` —— 那段常驻的治理前缀全文，供非 DSH 环境直接当系统提示词用。

---

## 用法

### 方式一：DeepSeek Harness 预设（推荐）

1. 把 `unlimited-token-mode/` 整个目录放到 `~/.dsh/preset-skills/unlimited-token-mode`。
2. 把 `preset/cordis-preset.yml` 的内容 insert 进 `~/.dsh/profiles/<你的 profile>/cordis.patch.yml`。
3. 重启 DSH，在会话头部的模式下拉里选「无限token模式」。

预设自带常驻前缀（七条硬规则 + 开工五步 + 禁用清单），每次对话都在，**不需要再手动 `/unlimited-token-mode`**。技能本身只在需要模板、脚本、完整判据时才加载。

### 方式二：任意 agent 框架

把 `preset/prompt-prefix.md` 作为系统提示词的一部分注入，再让模型能读到 `SKILL.md` 和 `templates/`。

### 自检校验器

```bash
python scripts/selftest.py                    # 校验器自测，应全部通过
python scripts/unlimited_lint.py <产物>        # 校验决策日志 / 运行报告 / 任务契约书
python scripts/unlimited_lint.py <产物> --strict
```

---

## 适用边界

- 只读、一次性的小任务上全套是浪费——用轻档。
- 如果**框架本身**已经强制了计数和核对（harness 注入），契约 2/4 只保留「确认注入存在」。
- 这个模式治的是**流程缺陷**，不治能力上限。它让模型不犯已知的错，不会让它变聪明。

---

## 出处

针对的问题清单与实测数据来自 [karminski/deepseek-v4.1-flash-1017-issue-pov](https://github.com/karminski/deepseek-v4.1-flash-1017-issue-pov) 的 `AGENT-CAPABILITY-REPORT.md`，以及作者的使用建议。

一句话总结作者的原话：**用它写代码没毛病，但用它做 AIOps 需要长个心眼。**
