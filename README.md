# Unlimited Token Mode

**English** | [中文](#中文)

> Also known as **"白饭管够模式"** (All-You-Can-Eat Rice Mode) — because the whole point is to stop the model from going into power-saving mode.

An external governance layer for models whose *judgment* can be trusted but whose *bookkeeping* cannot.

This is not a prompt-trick collection. It is a set of **hard rules + scripts + a validator** that move the model's ledger, its self-checks, and its definition of "done" out of its thinking and into something verifiable.

---

## Two editions

This repository ships the same seven contracts twice, because the failure mode changes when the harness does.

| | **Standard edition** | **PTC edition** |
|---|---|---|
| Model sees | one tool call per step | a single `run_code` tool; every other capability is an in-program SDK binding `await tools.<name>(args)` |
| Failure mode treated | laziness one action at a time | one big mistake inside a program |
| Contracts | seven | the same seven, re-stated for programs |
| Extra hazards | — | **P1–P7**: swallowed exceptions, self-certified verdicts, summaries instead of evidence, implicit ledgers, bundled irreversible actions, truncation read as completion, concurrent read-modify-write |
| Preset `config.id` | `unlimited-token` | `unlimited-token-ptc` |
| Skill name | `unlimited-token-mode` | `unlimited-token-ptc` |

Use the **standard edition** unless your harness actually runs Programmatic Tool Calling. The PTC edition assumes the model has exactly one tool; on a native-tool harness its rules point at machinery that is not there.

The PTC edition exists because the standard edition's contracts survive the move to `run_code` **unchanged in wording but changed in failure mode**. "The model never counts in its head" becomes "the model never keeps a ledger in a program variable". "Verification is free" becomes "verification output never enters the conversation unless you return it, so the only way to skip it is to swallow it". "Falsifiable finish" becomes "return the exit code and the raw output, not a `pass: true` the program computed about itself".

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
unlimited-token-mode/                 # ← STANDARD EDITION skill
├── SKILL.md                          # Main skill file (seven contracts, three gears, five startup steps, banned-phrase list)
├── plugins/
│   └── utc-ledger.mjs                # Runtime evidence ledger + per-step state injection (contracts 2 / 4 / 7)
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
    ├── utc_core.py                   # The single judgement engine (byte-identical copy in the PTC edition)
    ├── unlimited_lint.py             # CLI shell -> utc_core.main
    ├── adversarial_suite.py          # 23 cases (17 attacks + 6 honest) × default/strict = 46 assertions
    ├── ab_compare.py                 # Same cases fed to v1 and v2, verdict table
    ├── e2e_verify.py                 # Replays a real session ledger against its report
    └── selftest.py                   # Validator self-test (15 checks)

unlimited-token-ptc/                  # ← PTC EDITION skill
├── SKILL.md                          # Same seven contracts, re-stated for a run_code-only harness
├── references/
│   ├── ptc-hazards.md                # P1–P7 in full: symptom / mechanism / hard rule / counter-example / example / self-check
│   ├── harness-patterns.md           # run_code patterns: one intent per program, state on disk, evidence returns, concurrency, timeouts
│   ├── evidence-map.md               # Same measured numbers, plus the structural evidence for P1–P7
│   ├── consequence-gate.md           # Consequence gate with three extra columns (program id, description, irreversible-action count)
│   └── prompt-compiler.md            # Compile a request into a PTC-edition prompt (six steps)
├── templates/
│   ├── task-brief.md                 # Task contract, plus a program-budget-and-concurrency section
│   ├── decision-log.md               # Decision block, plus program id / description / irreversible-action count
│   ├── run-report.md                 # Falsifiable finish, §2 cites the program ledger
│   └── program-log.md                # Program ledger — one block per run_code program (validated by ptc_lint.py)
└── scripts/
    ├── utc_core.py                   # Byte-identical copy of the standard engine (sha256-checked by selftest)
    ├── ptc_lint.py                   # CLI shell -> utc_core.main
    └── selftest.py                   # Validator self-test (13 checks)

preset/
├── cordis-preset.yml                 # Standard edition preset block
├── prompt-prefix-ptc.md              # PTC edition resident prefix
├── cordis-preset-ptc.yml             # PTC edition preset block
└── prompt-prefix.md                  # Standard edition resident prefix

install.py                            # Install skills + preset blocks into a profile, with backups
sync_prefix.py                        # Project prompt-prefix*.md into the preset persona.prefix (--check)
```

Also included:

- `preset/cordis-preset.yml` — the DeepSeek Harness agent preset block for the standard edition (insert directly into `cordis.patch.yml`).
- `preset/cordis-preset-ptc.yml` — the same for the PTC edition. It differs from the standard block in exactly three places: the ids, `workflow-ptc`/`tool-workflow` disabled, and `tool-presentation: { mode: ptc }`.
- `preset/prompt-prefix.md` — the standard edition resident governance prefix, usable as a system prompt in any other framework.
- `preset/prompt-prefix-ptc.md` — the PTC edition resident prefix.

---

## Usage

### Option 1: DeepSeek Harness preset (recommended)

Pick the edition that matches your harness. Both are installed the same way.

```bash
# installs both editions: skill copies + preset blocks, with timestamped backups
python install.py --profile <your-profile>

# or just one
python install.py --profile <your-profile> --only std
python install.py --profile <your-profile> --only ptc

# see what it would do, change nothing
python install.py --profile <your-profile> --dry-run
```

Then restart DSH and pick **"无限token标准版"** or **"无限tokenPTC版"** from the mode dropdown in the session header.

What the installer does that a manual copy does not:

1. **Copies the whole preset block**, not a hand-trimmed one. Presets do not inherit from each other, so a trimmed block means missing tools.
2. **Rewrites the plugin path to an absolute path.** Relative paths in DSH resolve against *whatever loads them* — a top-level `--patch` resolves against the patch file's own directory, so a relative path silently stops working as soon as the block moves. This is the single most common way the ledger plugin ends up loaded-but-dead.
3. **Removes duplicate preset ids before writing.** If you previously copied a preset block in by hand, the registry throws `Duplicate agent preset` and *the whole preset fails to load*. The installer strips any existing block with the same id first.
4. **Backs up** `cordis.patch.yml` with a timestamp before touching it.

You can install both. They coexist: different preset ids, different skill names, same `preset-skills` directory.

The preset carries a resident prefix (seven hard rules + five startup steps + banned-phrase list) that is present in every conversation — **you do not need to invoke `/unlimited-token-mode` manually**. The skill itself is loaded only when you need templates, scripts, or the full criteria.

> **The PTC preset only works if `@deepseek-ai/dsh-agent-tool-presentation` can compose `ctx.ptcRuntime`** — that is, if `@deepseek-ai/dsh-ptc-runtime-node` is mounted in the host composition. DSH's base bundle already mounts it. If the host does not, the preset mount is rejected rather than silently degrading to native tool calls.

### Option 2: Any agent framework

Inject `preset/prompt-prefix.md` (standard) or `preset/prompt-prefix-ptc.md` (PTC) as part of the system prompt, and make `SKILL.md` and `templates/` readable to the model.

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

3. Ask me which edition(s) to install - standard, PTC, or both. Do not guess. Then
   install each chosen edition's skill directory:
   - standard: repo unlimited-token-mode/  -> $DSH_HOME/preset-skills/unlimited-token-mode
   - PTC:      repo unlimited-token-ptc/   -> $DSH_HOME/preset-skills/unlimited-token-ptc
   If a target already exists, back it up to .bak-<timestamp> first, then replace.
   Never delete the old one outright.

4. Install the preset(s), one per chosen edition:
   4.1 Back up cordis.patch.yml to cordis.patch.yml.bak-<timestamp>.
   4.2 Read the matching preset file from the repo. It is one entry of the top-level
       YAML array (it starts with `- insert:`). Append that entry to the end of the
       top-level array in cordis.patch.yml.
       - standard: preset/cordis-preset.yml      (id preset-unlimited-token,     config.id unlimited-token)
       - PTC:      preset/cordis-preset-ptc.yml  (id preset-unlimited-token-ptc, config.id unlimited-token-ptc)
   4.3 Before appending, check whether cordis.patch.yml already has that entry id,
       or a preset with that config.id:
       - exists and identical  -> skip it, report "already installed"
       - exists but different  -> stop, show me the diff, ask whether to overwrite
       - does not exist        -> append
   4.4 Keep the indentation level of the existing top-level entries. Do not nest it
       inside another entry.

5. Verify. Actually run every check and paste the real output. Do not describe what
   the result "should" be.
   a. Skill files present: count the files under each installed
      $DSH_HOME/preset-skills/<skill-name> and compare with the repo copy.
      Standard edition: 15 files. PTC edition: 13 files.
   b. Validator self-test, one per installed edition:
      python $DSH_HOME/preset-skills/unlimited-token-mode/scripts/selftest.py
      python $DSH_HOME/preset-skills/unlimited-token-ptc/scripts/selftest.py
      Expected: 15/15 and 13/13. Each prints its own case count and a pass/fail
      summary. Paste both. A failure here is not "probably fine" - stop.
   c. Top-level entry count of cordis.patch.yml: count before the install and after.
      It should increase by exactly the number of editions you installed. Zero-dependency check:
      grep -c "^- " <path-to-cordis.patch.yml>
      Also confirm there are no tab characters in the file, since YAML forbids them
      for indentation: grep -P "	" <path-to-cordis.patch.yml>   (no output = good)
   d. Real YAML parse, to prove the file still loads. The yaml package lives in the
      DSH install directory's node_modules, not globally, so point NODE_PATH at it.
      Find that directory first - it is the one containing
      node_modules/@deepseek-ai/dsh-agent-preset - then run:
      NODE_PATH="<dsh-install-dir>/node_modules" node -e "const yaml=require('yaml'),fs=require('fs'); const d=yaml.parse(fs.readFileSync(process.argv[1],'utf8')); const ps=d.filter(e=>e.insert).flatMap(e=>e.insert).filter(e=>String(e.id).startsWith('preset-unlimited-token')); console.log('entries:',d.length); for (const p of ps) console.log('preset:', p.config.id+' / '+p.config.name+' / order '+p.config.order+' / '+p.config.plugins.length+' plugins / presentation '+(p.config.plugins.find(x=>x.id==='tool-presentation')?.config.mode ?? 'native'));" <path-to-cordis.patch.yml>
      Expected: entries = (the count from step c), and exactly one line per installed
      edition - if any line appears twice, an old hand-copied block is still there and
      the registry will reject the whole preset with "Duplicate agent preset":
      unlimited-token / 无限token标准版 / order 5 / 21 plugins / presentation native
      unlimited-token-ptc / 无限tokenPTC版 / order 6 / 22 plugins / presentation ptc
      Warnings about unresolved !!js tags are expected and harmless.
   e. PTC only: confirm the host can actually compose PTC. The PTC preset needs
      ctx.ptcRuntime, which @deepseek-ai/dsh-ptc-runtime-node provides. Check that
      node_modules/@deepseek-ai/dsh-ptc-runtime-node exists in the DSH install
      directory. If it does not, tell me the PTC preset will be rejected at mount
      and stop - do not claim it is installed.

6. Finish with a report that contains:
   - every file you changed (absolute paths)
   - where the backups are
   - the real output of checks a-e
   - what I have to do to actually use it (restart DSH, open a new session, pick
     "无限token标准版" or "无限tokenPTC版" in the mode dropdown in the session header)

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
# standard edition
python unlimited-token-mode/scripts/unlimited_lint.py <artifact> --kind brief|decision|report

# PTC edition (adds a fourth kind, `program`)
python unlimited-token-ptc/scripts/ptc_lint.py <artifact> --kind brief|decision|report|program

# with the runtime ledger: upgrades shape-checking into falsifiable checking
python unlimited-token-mode/scripts/unlimited_lint.py report.md --kind report --ledger .utc/ledger.jsonl

# self-tests
python unlimited-token-mode/scripts/selftest.py    # 15 checks
python unlimited-token-ptc/scripts/selftest.py     # 13 checks
```

Exit codes: `0` pass / `1` judgement failed / `2` bad input or usage. A crash is never a valid outcome.

#### Evidence tiers — not all checks are equal

| Tier | When | What it proves | What it cannot prove |
|---|---|---|---|
| `ledger` | `--ledger` given | the command really ran, the output was not rewritten, the exit code matches | — |
| `shape-only` | no ledger | the artifact has commands, has shaped output, and contains no pure-declaration check | **nothing about whether any of it actually ran** |

The CLI prints the tier it used. `shape-only` is a weaker guarantee and says so.

#### How the ledger makes evidence falsifiable

`plugins/utc-ledger.mjs` listens to every `tool/call` and `tool/result` and appends them to `.utc/ledger.jsonl`. The model cannot write that file. So when a report claims a command and its output, the validator can settle it objectively:

- command never executed → `LEDGER-NO-CALL`
- output rewritten → `LEDGER-OUTPUT-MISMATCH`
- exit code fabricated → `LEDGER-EXIT-MISMATCH`

This is what closes the hole that v1 could not close: in v1 the "real output" cell was written by the model, so "copy the field descriptions, then write *I confirmed it*" passed. String heuristics cannot tell a real check from a well-copied fake. Runtime records can.

The same plugin also injects per-step state (call count, error count, irreversible-action count) — that is contract 2, implemented by the runtime instead of demanded of the model.

#### v1 vs v2 on the same 23 cases

| | correct | let through | false-alarm | crashed |
|---|---|---|---|---|
| v1 | 8 | 14 | 1 | 0 |
| v2 | 23 | 0 | 0 | 0 |

Reproduce with `python unlimited-token-mode/scripts/ab_compare.py <v1 scripts dir>`. The 23 cases are 17 attacks plus **6 honest artifacts** — the honest half matters, because a validator that only catches bad guys can be written to always say no, and v1's false alarm was a genuine `df -h` check rejected as fake.

---

## Boundaries

- For read-only, one-off small tasks the full kit is waste — use the light gear.
- If the **framework itself** already enforces counting and verification (harness injection), contracts 2 and 4 reduce to "confirm the injection exists".
- This mode treats **process defects**, not capability limits. It stops the model from making known mistakes; it does not make it smarter.
- **`shape-only` proves nothing about execution.** Without the ledger plugin the validator can only check shape. It says so on every run.
- **It validates the record, not the intent.** A model determined to fool it can run an unrelated command and paste that command's real output. That hole is structural, and this mode does not pretend to close it.
- **Heuristics never fail an artifact.** Token-similarity signals are advisory only — the v1 false alarm came from treating them as verdicts.
- The PTC edition assumes a `run_code`-only harness. On a native-tool harness it is the wrong edition — its rules describe machinery that is not there.
- The PTC edition cannot fix what the runtime hides: if a program is killed by `timeoutMs`, nothing it computed reaches the conversation. The countermeasure is structural (segment your work, write progress to disk as you go), not rhetorical.

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

## 两个版本

同一个仓库把七条契约发两遍，因为运行时的失效方式变了，契约就得跟着改口径。

| | **无限token标准版** | **无限tokenPTC版** |
|---|---|---|
| 模型看到什么 | 每步一次工具调用 | 只有一个 `run_code`，其余能力都是程序内 SDK 绑定 `await tools.<名字>(参数)` |
| 治的失效方式 | 一次一次地犯懒 | 在程序里一次性犯错 |
| 契约 | 七条 | 同样七条，按程序口径重述 |
| 额外缺陷 | — | **P1–P7**：吞异常、自证判决、摘要代替证据、隐式账本、捆绑不可逆、截断即完成、并发读改写 |
| 预设 `config.id` | `unlimited-token` | `unlimited-token-ptc` |
| 技能名 | `unlimited-token-mode` | `unlimited-token-ptc` |

**除非你的框架真的跑 Programmatic Tool Calling，否则用标准版。** PTC 版假设模型只有一个工具；在原生工具调用的框架上，它的规则指向并不存在的机制。

PTC 版之所以存在，是因为标准版那七条契约搬到 `run_code` 下**字面没变、失效方式全变了**：
- 「不自己在心里数数」→「不把账本放在程序变量里」（`per-call` 下变量每次消失；`persistent-in-memory` 下变量活着，但不可见、无 `_rev`、无并发保护，所以它仍然不是账本）
- 「核对免费」→「核对输出根本不进对话，除非你 `return` 它」，于是跳过的唯一办法就是 `try/catch` 把它吞掉
- 「可证伪收工」→「返回退出码和原始输出，而不是程序自己算出的 `pass: true`」。程序自证判决无法被推翻，所以它不是检验。

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
unlimited-token-mode/                 # ← 无限token标准版技能
├── SKILL.md                          # 技能主文件（七条契约、三档齿轮、开工五步、禁用清单）
├── plugins/
│   └── utc-ledger.mjs                # 运行时证据账本 + 每步状态注入（契约 2 / 4 / 7）
├── references/
│   ├── evidence-map.md               # 七条契约的实测数字、出处，以及已被证伪的错误做法
│   ├── consequence-gate.md           # 后果门完整推演模板 + 两个 worked example
│   ├── harness-patterns.md           # 循环外置、状态文件、环境注入的代码范式
│   └── prompt-compiler.md            # 把任意需求编译成「无限token标准版提示词」
├── templates/
│   ├── task-brief.md                 # 任务契约书（歧义决议表）
│   ├── decision-log.md               # 决策块（二阶三阶后果）
│   └── run-report.md                 # 可证伪收工报告
└── scripts/
    ├── utc_core.py                   # 唯一判定引擎（PTC 版有逐字节相同的拷贝）
    ├── unlimited_lint.py             # CLI 外壳 -> utc_core.main
    ├── adversarial_suite.py          # 23 例（17 攻击 + 6 诚实）× 默认/严格 = 46 条断言
    ├── ab_compare.py                 # 同一批用例喂 v1 与 v2，出判定对照表
    ├── e2e_verify.py                 # 把真实会话的账本与报告对账
    └── selftest.py                   # 校验器自测（15 项）

unlimited-token-ptc/                  # ← 无限tokenPTC版技能
├── SKILL.md                          # 同样七条契约，按 run_code 口径重述
├── references/
│   ├── ptc-hazards.md                # P1–P7 全文：症状 / 机制 / 硬规则 / 反例 / 正例 / 自检
│   ├── harness-patterns.md           # run_code 范式：一程序一意图、状态落盘、证据返回、并发、超时
│   ├── evidence-map.md               # 同样的实测数字，加上 P1–P7 的结构性证据
│   ├── consequence-gate.md           # 后果门，多三栏（程序 id / description / 程序内不可逆动作数）
│   └── prompt-compiler.md            # 把需求编译成 PTC 版提示词（六步）
├── templates/
│   ├── task-brief.md                 # 任务契约书，多一节「程序预算与并发」
│   ├── decision-log.md               # 决策块，多三栏
│   ├── run-report.md                 # 可证伪收工，§2 引用程序账本
│   └── program-log.md                # 程序账本，每个 run_code 程序一条（ptc_lint.py 校验）
└── scripts/
    ├── utc_core.py                   # 与标准版逐字节相同的引擎（selftest 校验 sha256）
    ├── ptc_lint.py                   # CLI 外壳 -> utc_core.main
    └── selftest.py                   # 校验器自测（13 项）

preset/
├── cordis-preset.yml                 # 标准版预设块
├── prompt-prefix-ptc.md              # PTC 版常驻前缀
├── cordis-preset-ptc.yml             # PTC 版预设块
└── prompt-prefix.md                  # 标准版常驻前缀

install.py                            # 把技能与预设块装进 profile，自动备份
sync_prefix.py                        # 把 prompt-prefix*.md 投影进预设的 persona.prefix（--check）
```

另附：

- `preset/cordis-preset.yml` —— 无限token标准版的 DeepSeek Harness Agent 预设配置块（可直接 insert 进 `cordis.patch.yml`）。
- `preset/cordis-preset-ptc.yml` —— PTC 版预设块。与标准版块**只有三处结构差异**：id 组（`preset-unlimited-token-ptc` / `unlimited-token-ptc` / order 6）、`workflow-ptc` 与 `tool-workflow` 置 `disabled: true`、新增 `tool-presentation: { mode: ptc }`。
- `preset/prompt-prefix.md` —— 标准版那段常驻治理前缀全文，供非 DSH 环境直接当系统提示词用。
- `preset/prompt-prefix-ptc.md` —— PTC 版常驻前缀全文。

---

## 用法

### 方式一：DeepSeek Harness 预设（推荐）

先选版本，再按同样两步装。两个都装也可以——它们共存：预设 id 不同、技能名不同、共用 `preset-skills` 目录。

```bash
# 两个版本一起装：技能副本 + 预设块，改前自动备份
python install.py --profile <你的 profile>

# 只装一个
python install.py --profile <你的 profile> --only std
python install.py --profile <你的 profile> --only ptc

# 只看会做什么，不动文件
python install.py --profile <你的 profile> --dry-run
```

然后重启 DSH，在会话头部的模式下拉里选 **「无限token标准版」** 或 **「无限tokenPTC版」**。

安装器比手工复制多做四件事：

1. **整段复制预设块**，不手动裁剪。预设之间没有继承机制，裁一刀就少一批工具。
2. **把插件路径写成 `file://` 绝对 URL**。DSH 里的相对路径按「谁加载它」解析——顶层 `--patch` 按 patch 文件自己所在的目录算，块一挪走相对路径就静默失效。但改成裸的 Windows 绝对路径 `C:\...` 也不行：cordis loader 对不以 `.` 开头的 `name` 直接走 `import(name)`，而 Node 的 ESM loader 只接受 `file:`/`data:`/`node:` 三种 scheme，会抛 `ERR_UNSUPPORTED_ESM_URL_SCHEME`。结果是**预设整条变成加载失败、被 UI 过滤掉，下拉框里根本不出现**——这是账本插件「装上了但没跑」最常见的原因。
3. **写入前先删同名条目**。如果你以前手工拷过同一份预设块，registry 会抛 `Duplicate agent preset`，**整个预设加载失败**。安装器会先清掉同 id 的旧块。
4. **改前备份** `cordis.patch.yml`（带时间戳）。

预设自带常驻前缀（七条硬规则 + 开工五步 + 禁用清单），每次对话都在，**不需要再手动 `/unlimited-token-mode`**。技能本身只在需要模板、脚本、完整判据时才加载。

> **PTC 预设只有在宿主能组合出 `ctx.ptcRuntime` 时才生效** —— 也就是 `@deepseek-ai/dsh-ptc-runtime-node` 已挂在宿主组合里。DSH 的 base bundle 默认就挂了。宿主没挂时，预设挂载会被**拒绝**，而不是悄悄退化成原生工具调用。

### 方式二：任意 agent 框架

把 `preset/prompt-prefix.md`（标准版）或 `preset/prompt-prefix-ptc.md`（PTC 版）作为系统提示词的一部分注入，再让模型能读到 `SKILL.md` 和 `templates/`。

### 方式三：让 agent 帮你装

把下面这段提示词粘给任意能读写本机文件的编码 agent（DSH 自己、Claude Code、Codex、Cursor 都行），它会装好技能目录和预设，并把验证结果贴给你。

这段提示词是**故意写成防御式**的：禁止猜路径、禁止不备份就覆盖、禁止跳过失败步骤、要求贴真实命令输出而不是「应该没问题」。因为这些正是本模式要治的毛病——装它的提示词也得守同样的标准。

```text
帮我在本机装好 unlimited-token-mode（无限token标准版）。
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

3. 问我要装哪个版本 —— 标准版、PTC 版、还是两个都装。不要自己猜。然后装选中的技能目录：
   - 标准版：仓库 unlimited-token-mode/  -> $DSH_HOME/preset-skills/unlimited-token-mode
   - PTC 版：仓库 unlimited-token-ptc/   -> $DSH_HOME/preset-skills/unlimited-token-ptc
   如果目标已存在，先备份成 .bak-<时间戳> 再替换，不要直接删掉旧的。

4. 装预设（每个选中的版本各装一个）：
   4.1 先把 cordis.patch.yml 备份成 cordis.patch.yml.bak-<时间戳>。
   4.2 读仓库里对应的预设文件。它是顶层 YAML 数组的一个条目（以 `- insert:` 开头）。
       把这个条目追加到 cordis.patch.yml 顶层数组的末尾。
       - 标准版：preset/cordis-preset.yml     （id preset-unlimited-token，     config.id unlimited-token）
       - PTC 版：preset/cordis-preset-ptc.yml （id preset-unlimited-token-ptc， config.id unlimited-token-ptc）
   4.3 追加前先检查 cordis.patch.yml 里是否已有那个条目 id、或那个 config.id 的预设：
       - 已存在且内容相同 -> 跳过，告诉我「已装过」
       - 已存在但内容不同 -> 停下，把差异给我看，问我要不要覆盖
       - 不存在 -> 追加
   4.4 保持与已有顶层条目相同的缩进层级，不要嵌到别的条目里面去。

5. 验证。每一项都要真跑，把真实输出贴出来，不要描述「应该」是什么结果。
   a. 技能文件齐不齐：数一下每个已装目录 $DSH_HOME/preset-skills/<技能名> 下的文件数，
      和仓库里那个目录比对。标准版 10 个文件，PTC 版 12 个文件。
   b. 校验器自测，每个已装版本各跑一次：
      python $DSH_HOME/preset-skills/unlimited-token-mode/scripts/selftest.py
      python $DSH_HOME/preset-skills/unlimited-token-ptc/scripts/selftest.py
      各自打印自己的用例数和通过/失败汇总，两个输出都要贴。
   c. cordis.patch.yml 的顶层条目数：装之前数一次，装之后数一次，
      应该正好 +（你装了几个版本）。
      零依赖的查法：
      grep -c "^- " <cordis.patch.yml 的路径>
      顺便确认文件里没有 tab 字符（YAML 缩进不许用 tab）：
      grep -nP "	" <cordis.patch.yml 的路径>   （没有输出就是好的）
   d. 真正解析一遍 YAML，证明文件还能被加载。yaml 包在 DSH 安装目录的
      node_modules 里，全局没有，所以要把 NODE_PATH 指过去。
      先找到那个目录（就是包含 node_modules/@deepseek-ai/dsh-agent-preset 的那个），然后跑：
      NODE_PATH="<dsh安装目录>/node_modules" node -e "const yaml=require('yaml'),fs=require('fs'); const d=yaml.parse(fs.readFileSync(process.argv[1],'utf8')); const ps=d.filter(e=>e.insert).flatMap(e=>e.insert).filter(e=>String(e.id).startsWith('preset-unlimited-token')); console.log('条目数:',d.length); for (const p of ps) console.log('预设:', p.config.id+' / '+p.config.name+' / order '+p.config.order+' / '+p.config.plugins.length+' 个插件 / 呈现 '+(p.config.plugins.find(x=>x.id==='tool-presentation')?.config.mode ?? 'native'));" <cordis.patch.yml 的路径>
      预期：条目数 = 步骤 c 数出来的那个数，并且每个已装版本一行 ——
      unlimited-token / 无限token标准版 / order 5 / 20 个插件 / 呈现 native
      unlimited-token-ptc / 无限tokenPTC版 / order 6 / 21 个插件 / 呈现 ptc
      出现 !!js 标签未解析的 warning 是正常的，不用管。
   e. 只对 PTC 版：确认宿主真的能组合出 PTC。PTC 预设需要 ctx.ptcRuntime，
      由 @deepseek-ai/dsh-ptc-runtime-node 提供。检查 DSH 安装目录下是否存在
      node_modules/@deepseek-ai/dsh-ptc-runtime-node。如果不存在，告诉我 PTC 预设
      会在挂载时被拒绝，然后停下 —— 不要声称已经装好。

6. 最后给我一份报告，包含：
   - 你改了哪些文件（绝对路径）
   - 备份在哪
   - a-e 各项检查的真实输出
   - 我要怎么才能用上（重启 DSH、新开会话、在会话头部模式下拉里选「无限token标准版」或「无限tokenPTC版」）

约束：
- 不要动 cordis.patch.yml 里其他任何条目。
- 不要修改从仓库下载下来的文件。
- 任何一步失败就停下报错，不要跳过继续往下做。
- 我给的路径不存在、或有多个候选时，问我，不要自己决定。
- 不许用「应该」「大概」「通常」代替实际结果。
```

### 自检校验器

标准版（`unlimited-token-mode/scripts/`）：

```bash
python scripts/selftest.py                    # 校验器自测，应全部通过
python scripts/unlimited_lint.py <产物>        # 校验决策日志 / 运行报告 / 任务契约书
python scripts/unlimited_lint.py <产物> --strict
```

PTC 版（`unlimited-token-ptc/scripts/`）：

```bash
python scripts/selftest.py                    # 校验器自测，应全部通过
python scripts/ptc_lint.py <产物>              # --kind brief | report | decision | program（省略时自动探测）
python scripts/ptc_lint.py <产物> --strict     # 追加 description 词数、多意图、疑似吞异常三项检查
```

PTC 校验器多出第四类产物 `program`：程序账本（`templates/program-log.md`），每个 `run_code` 程序一条。它会拒绝这样的 `判决: pass`——没有 `exit=` 行、没有数字证据、或者「可失败性」栏写的是占位词。因为程序对自己算出的判决不是检验。

---

## 适用边界

- 只读、一次性的小任务上全套是浪费——用轻档。
- 如果**框架本身**已经强制了计数和核对（harness 注入），契约 2/4 只保留「确认注入存在」。
- 这个模式治的是**流程缺陷**，不治能力上限。它让模型不犯已知的错，不会让它变聪明。
- **`shape-only` 档证明不了执行过。** 没装账本插件时校验器只能看形态，而且每次运行都会明说。
- **它校验的是留档，不是意图。** 一个铁了心要骗过它的模型，可以先跑一条无关命令，再把那条命令的真实输出贴进来。这个洞是结构性的，本模式不假装能堵。
- **启发式永不判失败。** 词元相似度这类信号只作提示——v1 的误杀正是把提示当成了判决。
- PTC 版假设宿主只给一个 `run_code`。在原生工具调用的宿主上，它是错的版本——它的规则描述的是并不存在的机制。
- PTC 版治不了运行时藏起来的东西：程序被 `timeoutMs` 杀掉时，它算出的任何结果都进不了对话。对策是结构性的（分段提交、边跑边落盘），不是措辞性的。

---

## 出处

针对的问题清单与实测数据来自 [karminski/deepseek-v4.1-flash-1017-issue-pov](https://github.com/karminski/deepseek-v4.1-flash-1017-issue-pov) 的 `AGENT-CAPABILITY-REPORT.md`，以及作者的使用建议。

一句话总结作者的原话：**用它写代码没毛病，但用它做 AIOps 需要长个心眼。**
