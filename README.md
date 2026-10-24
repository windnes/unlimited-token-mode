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

### Option 3: Let an agent install it for you

Copy the prompt below into any coding agent that can read and write files on the machine where DSH runs (DSH itself, Claude Code, Codex, Cursor, …). It installs both the skill directory and the preset, then proves it worked.

The prompt is written defensively on purpose: it forbids path guessing, forbids overwriting without a backup, forbids skipping a failed step, and demands real command output instead of "should be fine". Those are exactly the failure modes this mode exists to counter — so the installer prompt is held to the same standard.

```text
Install unlimited-token-mode into the DeepSeek Harness on this machine.
Repo: https://github.com/windnes/unlimited-token-mode

Work through the steps in order. Report the result of each step before starting the next.

1. Discover the environment. Do not assume any path — find it.
   - DSH_HOME: use $DSH_HOME if set, otherwise ~/.dsh
   - Target profile: list the directories under $DSH_HOME/profiles/. If there is exactly
     one, use it. If there are several, ask me which one instead of picking.
   - Report: DSH_HOME, profile name, and whether
     $DSH_HOME/profiles/<profile>/cordis.patch.yml exists.

2. Fetch the repo into a temporary directory (git clone --depth 1, or download and
   extract the zip). Do not clone it into the DSH directory.

3. Install the skill directory: copy the repo's unlimited-token-mode/ directory to
   $DSH_HOME/preset-skills/unlimited-token-mode
   If the target already exists, back it up to .bak-<timestamp> first, then replace.
   Never delete the old one outright.

4. Install the preset:
   4.1 Back up cordis.patch.yml to cordis.patch.yml.bak-<timestamp>.
   4.2 Read preset/cordis-preset.yml from the repo. It is one entry of the top-level
       YAML array (it starts with `- insert:`). Append that entry to the end of the
       top-level array in cordis.patch.yml.
   4.3 Before appending, check whether cordis.patch.yml already has an entry with
       id preset-unlimited-token, or a preset with config.id unlimited-token:
       - exists and identical  -> skip it, report "already installed"
       - exists but different  -> stop, show me the diff, ask whether to overwrite
       - does not exist        -> append
   4.4 Keep the indentation level of the existing top-level entries. Do not nest it
       inside another entry.

5. Verify. Actually run every check and paste the real output. Do not describe what
   the result "should" be.
   a. Skill files present: count the files under
      $DSH_HOME/preset-skills/unlimited-token-mode — it should match the repo's
      copy of that directory (10 files).
   b. Validator self-test:
      python $DSH_HOME/preset-skills/unlimited-token-mode/scripts/selftest.py
      It should print that all 19 cases pass.
   c. Top-level entry count of cordis.patch.yml: count before the install and after.
      It should increase by exactly 1. Zero-dependency check:
      grep -c "^- " <path-to-cordis.patch.yml>
      Also confirm there are no tab characters in the file, since YAML forbids them
      for indentation: grep -P "\t" <path-to-cordis.patch.yml>   (no output = good)
   d. Real YAML parse, to prove the file still loads. The yaml package lives in the
      DSH install directory's node_modules, not globally, so point NODE_PATH at it.
      Find that directory first — it is the one containing
      node_modules/@deepseek-ai/dsh-agent-preset — then run:
      NODE_PATH="<dsh-install-dir>/node_modules" node -e "const yaml=require('yaml'),fs=require('fs'); const d=yaml.parse(fs.readFileSync(process.argv[1],'utf8')); const p=d.filter(e=>e.insert).flatMap(e=>e.insert).find(e=>e.id==='preset-unlimited-token'); console.log('entries:',d.length); console.log('preset:', p ? p.config.id+' / '+p.config.name+' / '+p.config.plugins.length+' plugins' : 'NOT FOUND');" <path-to-cordis.patch.yml>
      Expected: entries = (the count from step c), and
      preset: unlimited-token / 无限token模式 / 20 plugins
      Warnings about unresolved !!js tags are expected and harmless.

6. Finish with a report that contains:
   - every file you changed (absolute paths)
   - where the backups are
   - the real output of checks a-d
   - what I have to do to actually use it (restart DSH, open a new session, pick
     "无限token模式" in the mode dropdown in the session header)

Constraints:
- Do not touch any other entry in cordis.patch.yml.
- Do not modify the files you downloaded from the repo.
- If any step fails, stop and report the error. Do not skip it and continue.
- If a path I gave you does not exist, or there are several candidates, ask me.
  Do not choose on your own.
- Never use "should", "probably", or "usually" in place of an actual result.
```

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

### 方式三：让 agent 帮你装

把下面这段提示词粘给任意能读写本机文件的编码 agent（DSH 自己、Claude Code、Codex、Cursor 都行），它会装好技能目录和预设，并把验证结果贴给你。

这段提示词是**故意写成防御式**的：禁止猜路径、禁止不备份就覆盖、禁止跳过失败步骤、要求贴真实命令输出而不是「应该没问题」。因为这些正是本模式要治的毛病——装它的提示词也得守同样的标准。

```text
帮我在本机装好 unlimited-token-mode（无限token模式）。
仓库：https://github.com/windnes/unlimited-token-mode

按顺序做，每步做完先把结果告诉我，再进下一步。

1. 探明环境。不要假设任何路径，去找。
   - DSH_HOME：有 $DSH_HOME 就用它，否则用 ~/.dsh
   - 目标 profile：列出 $DSH_HOME/profiles/ 下的目录。只有一个就用它；
     有多个就问我用哪个，不要自己挑。
   - 告诉我：DSH_HOME、profile 名、以及
     $DSH_HOME/profiles/<profile>/cordis.patch.yml 是否存在。

2. 把仓库取到临时目录（git clone --depth 1，或下载 zip 解压）。
   不要 clone 到 DSH 目录里。

3. 装技能目录：把仓库里的 unlimited-token-mode/ 目录整个复制到
   $DSH_HOME/preset-skills/unlimited-token-mode
   如果目标已存在，先备份成 .bak-<时间戳> 再替换，不要直接删掉旧的。

4. 装预设：
   4.1 先把 cordis.patch.yml 备份成 cordis.patch.yml.bak-<时间戳>。
   4.2 读仓库里的 preset/cordis-preset.yml。它是顶层 YAML 数组的一个条目
       （以 `- insert:` 开头）。把这个条目追加到 cordis.patch.yml 顶层数组的末尾。
   4.3 追加前先检查 cordis.patch.yml 里是否已有 id 为 preset-unlimited-token
       的条目、或 config.id 为 unlimited-token 的预设：
       - 已存在且内容相同 -> 跳过，告诉我「已装过」
       - 已存在但内容不同 -> 停下，把差异给我看，问我要不要覆盖
       - 不存在 -> 追加
   4.4 保持与已有顶层条目相同的缩进层级，不要嵌到别的条目里面去。

5. 验证。每一项都要真跑，把真实输出贴出来，不要描述「应该」是什么结果。
   a. 技能文件齐不齐：数一下 $DSH_HOME/preset-skills/unlimited-token-mode
      下的文件数，应该和仓库里那个目录一致（10 个）。
   b. 校验器自测：
      python $DSH_HOME/preset-skills/unlimited-token-mode/scripts/selftest.py
      应该输出 19 个用例全部通过。
   c. cordis.patch.yml 的顶层条目数：装之前数一次，装之后数一次，应该正好 +1。
      零依赖的查法：
      grep -c "^- " <cordis.patch.yml 的路径>
      顺便确认文件里没有 tab 字符（YAML 缩进不许用 tab）：
      grep -nP "\x09" <cordis.patch.yml 的路径>   （没有输出就是好的）
   d. 真正解析一遍 YAML，证明文件还能被加载。yaml 包在 DSH 安装目录的
      node_modules 里，全局没有，所以要把 NODE_PATH 指过去。
      先找到那个目录（就是包含 node_modules/@deepseek-ai/dsh-agent-preset 的那个），然后跑：
      NODE_PATH="<dsh安装目录>/node_modules" node -e "const yaml=require('yaml'),fs=require('fs'); const d=yaml.parse(fs.readFileSync(process.argv[1],'utf8')); const p=d.filter(e=>e.insert).flatMap(e=>e.insert).find(e=>e.id==='preset-unlimited-token'); console.log('条目数:',d.length); console.log('预设:', p ? p.config.id+' / '+p.config.name+' / '+p.config.plugins.length+' 个插件' : '没找到');" <cordis.patch.yml 的路径>
      预期：条目数 = 步骤 c 数出来的那个数，
      预设: unlimited-token / 无限token模式 / 20 个插件
      出现 !!js 标签未解析的 warning 是正常的，不用管。

6. 最后给我一份报告，包含：
   - 你改了哪些文件（绝对路径）
   - 备份在哪
   - a-d 四项检查的真实输出
   - 我要怎么才能用上（重启 DSH、新开会话、在会话头部模式下拉里选「无限token模式」）

约束：
- 不要动 cordis.patch.yml 里其他任何条目。
- 不要修改从仓库下载下来的文件。
- 任何一步失败就停下报错，不要跳过继续往下做。
- 我给的路径不存在、或有多个候选时，问我，不要自己决定。
- 不许用「应该」「大概」「通常」代替实际结果。
```

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
