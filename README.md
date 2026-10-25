# Unlimited Token Mode

**English** | [中文](#中文)

> Also known as **"白饭管够模式"** (All-You-Can-Eat Rice Mode) — because the whole point is to stop the model from going into power-saving mode.

**In one sentence:** long-running coding agents don't usually fail because they can't write code. They fail because they quietly mis-count, skip a check and say they didn't, or declare "done" without ever verifying. This repository adds a governance layer that makes those specific failures **visible and provable**.

An external governance layer for models whose *judgment* can be trusted but whose *bookkeeping* cannot.

This is not a prompt-trick collection. It is a set of **hard rules + scripts + a validator** that move the model's ledger, its self-checks, and its definition of "done" out of its thinking and into something verifiable.

> **Read this first if you are new:** jump to [What it fixes](#what-it-fixes) for the five concrete failure modes, then [Usage](#usage) to install. Terms like `ledger`, `tier`, and `run_code` are defined where they first appear.

---

## Two editions

This repository ships the same seven contracts twice, because the failure mode changes when the harness does.

| | **Standard edition** | **PTC edition** |
|---|---|---|
| Model sees | one tool call per step | a single `run_code` tool; every other capability is an in-program SDK binding `await tools.<name>(args)` |
| Failure mode treated | laziness one action at a time | one big mistake inside a program |
| Contracts | seven | the same seven, re-stated for programs |
| Extra hazards | — | **P1–P7** — seven ways a program goes wrong that a native-tool harness cannot produce: swallowed exceptions, self-certified verdicts, summaries instead of evidence, implicit ledgers, bundled irreversible actions, truncation read as completion, concurrent read-modify-write. Full detail in `references/ptc-hazards.md` |
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

### Three gears: how much process a task actually needs

Not every task needs the full kit. Before starting, classify the task — the gear decides **which contracts you follow and what you have to leave behind**.

| Gear | When it applies | Contracts you follow | Artifacts you must produce |
|---|---|---|---|
| **Light** | read-only, ≤3 steps, nothing irreversible | 4 (verification is free) + 7 (falsifiable finish) | none — but "done" still needs a real exit code and real output |
| **Standard** | writes, counting, ≥4 steps | 1, 2, 3, 4, 7 | task contract + state files + run report |
| **Strict** | irreversible, externally visible, long-running, AIOps | all seven | all of the above **plus one decision block per irreversible action** |

**When unsure, go up a gear.** The cost is asymmetric: writing one extra contract costs a few minutes; skipping one consequence analysis can be permanent.

The full table, with the PTC-specific triggers (how many irreversible actions sit inside one program, how many programs you'll write), is in [`unlimited-token-mode/SKILL.md`](unlimited-token-mode/SKILL.md#判档) and [`unlimited-token-ptc/SKILL.md`](unlimited-token-ptc/SKILL.md#判档开工第一步).

---

## Repository layout

```
unlimited-token-mode/                 # ← STANDARD EDITION skill
├── SKILL.md                          # Main skill file (seven contracts, three gears, five startup steps, banned-phrase list)
├── plugins/
│   ├── utc-ledger.mjs                # Runtime evidence ledger + per-step state injection (contracts 2 / 4 / 7)
│   ├── test-irreversible.mjs         # Regression: prose about a command is not that command being run
│   ├── test-dispatch.mjs             # Regression: in-program tool calls get recorded with real exit codes
│   └── replay-dispatch.mjs           # Replays a recorded session log and diffs the ledger against it
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
    ├── ab_compare.py                 # Feeds the same cases to the string-only and ledger versions, prints both verdicts
    ├── e2e_verify.py                 # Replays a real session ledger against its report
    └── selftest.py                   # Validator self-test (17 checks, including the two plugin regressions)

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
    └── selftest.py                   # Validator self-test (14 checks)

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
      Standard edition: 18 files. PTC edition: 13 files.
   b. Validator self-test, one per installed edition:
      python $DSH_HOME/preset-skills/unlimited-token-mode/scripts/selftest.py
      python $DSH_HOME/preset-skills/unlimited-token-ptc/scripts/selftest.py
      Expected: 17/17 and 14/14. Each prints its own case count and a pass/fail
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
      unlimited-token / 无限token标准版 / order 5 / 32 plugins / presentation native
      unlimited-token-ptc / 无限tokenPTC版 / order 6 / 33 plugins / presentation ptc
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

# self-tests (both print their own case count and a pass/fail summary)
python unlimited-token-mode/scripts/selftest.py    # 17 checks
python unlimited-token-ptc/scripts/selftest.py     # 14 checks
```

Exit codes: `0` pass / `1` judgement failed / `2` bad input or usage. A crash is never a valid outcome.

#### Evidence tiers — not all checks are equal

Every run prints which tier it used. **A higher tier is a stronger guarantee; the tool always tells you which one you got.**

| Tier | When | What it proves | What it cannot prove |
|---|---|---|---|
| `ledger` | `--ledger` given, and the ledger contains a record of a real tool execution | the command really ran, the output was not rewritten, the exit code matches | — |
| `source-literal` | `--ledger` given, but the ledger only holds coarse program-level records | the command **text appears in what was submitted** | **that the command was ever executed** |
| `shape-only` | no ledger at all | the artifact has commands, has shaped output, and contains no pure-declaration check | **nothing about whether any of it actually ran** |

`source-literal` exists because of one specific limitation: when a harness runs a whole program as a single tool call, a coarse ledger can only record the program source and the program's return value. Text appearing in that source is not evidence of execution. The validator detects this case, says so, and downgrades — under `--strict` it fails outright.

#### How the ledger makes evidence falsifiable

`plugins/utc-ledger.mjs` listens to every `tool/call` and `tool/result` and appends them to `.utc/ledger.jsonl`. The model cannot write that file. So when a report claims a command and its output, the validator can settle it objectively:

- command never executed → `LEDGER-NO-CALL`
- output rewritten → `LEDGER-OUTPUT-MISMATCH`
- exit code fabricated → `LEDGER-EXIT-MISMATCH`

**Why this matters:** if the "real output" cell is written by the model, then "copy the field descriptions, then add *I confirmed it*" passes. String comparison cannot tell a real check from a well-copied fake. Runtime records can. The next section shows how big that difference is.

The same plugin also injects per-step state (call count, error count, irreversible-action count) — that is contract 2, implemented by the runtime instead of demanded of the model.

#### Why the ledger matters: a before/after comparison

The first version of this validator (**v1**) had no ledger at all — it only compared strings. You can still run it to see the difference. The same 23 test artifacts were fed to both versions:

| | correct | let through | false-alarm | crashed |
|---|---|---|---|---|
| v1 (strings only) | 8 | 14 | 1 | 0 |
| v2 (runtime ledger) | 23 | 0 | 0 | 0 |

Reproduce with `python unlimited-token-mode/scripts/ab_compare.py <v1 scripts dir>`.

**What the numbers mean.** 14 of 23 bad artifacts slipped past v1: it could not tell a real check from a well-copied fake, because in v1 the "real output" cell was written by the model itself.

The **6 honest artifacts** in that set are the more important half. A validator that only catches bad guys is trivial to write — just always say no. v1 also produced one **false alarm**: a legitimate `df -h` disk check was rejected as fake, because the validator was matching on word similarity and the model had paraphrased the command. That single false alarm is the reason for the rule below.

---

## Boundaries

- For read-only, one-off small tasks the full kit is waste — use the **light gear** (contracts 4 and 7 only). See [Three gears](#three-gears-how-much-process-a-task-actually-needs) for what each gear drops.
- If the **framework itself** already enforces counting and verification (for example, the runtime injects state into every step), then two of the seven contracts — *externalize state* and *verification is free* — reduce to "confirm the injection actually exists". The other five still apply.
- This mode treats **process defects**, not capability limits. It stops the model from making known mistakes; it does not make it smarter.
- **`shape-only` proves nothing about execution.** Without the ledger plugin the validator can only check shape. It says so on every run.
- **It validates the record, not the intent.** A model determined to fool it can run an unrelated command and paste that command's real output. That hole is structural, and this mode does not pretend to close it.
- **Word-similarity heuristics never fail an artifact.** They are advisory only, for one concrete reason: a model that legitimately paraphrases a command (`df -h` written as "check disk usage") looks *different* from the recorded command, so any rule that treats "different wording" as "fabricated" will reject honest work. Similarity can raise a flag; it cannot pass sentence. Hard verdicts come only from the runtime ledger, which records what actually ran.
- The PTC edition assumes a `run_code`-only harness. On a native-tool harness it is the wrong edition — its rules describe machinery that is not there.
- The PTC edition cannot fix what the runtime hides: if a program is killed by `timeoutMs`, nothing it computed reaches the conversation. The countermeasure is structural (segment your work, write progress to disk as you go), not rhetorical.
- **In-program tool calls are recorded when the harness emits `tool/ptc-dispatch` events** (current DSH does). The ledger then holds the *real* command, the *real* tool name, and the *real* exit code with `scope: "inner"`, which is what makes the `ledger` tier reachable under PTC. On older harnesses without those events the tier degrades to `source-literal` and says so. Cost: the ledger grows to roughly 3× its previous row count (measured: 158 outer calls produced 491 inner records in one session).

---

## Origin

The problem list and measured data come from `AGENT-CAPABILITY-REPORT.md` in [karminski/deepseek-v4.1-flash-1017-issue-pov](https://github.com/karminski/deepseek-v4.1-flash-1017-issue-pov), plus the author's usage notes.

The author's recommendation, summarised: **it writes code just fine, but using it for AIOps requires keeping your wits about you.**

---

# 中文

[English](#unlimited-token-mode) | **中文**

> 又名 **白饭管够模式**。

**一句话：** 长流程的编程 Agent 一般不是败在不会写代码，而是败在三件小事上——悄悄数错、跳过了核对却说没跳过、没验证就宣布"做完了"。这个仓库加的那一层，专门让这三类失败**看得见、可证伪**。

给「判断力可信、账本不可信」的模型加装一层外置治理。

不是提示词技巧合集，是一套**硬规则 + 脚本 + 校验器**：把模型的账本、核对、收工判据，从 thinking 里搬到外面。

> **第一次来先看这两节：** [这个模式治什么](#这个模式治什么)讲五个具体失效场景，[用法](#用法)讲怎么装。文中 `账本`、`证据档`、`run_code` 这类词在首次出现处都有解释。

---

## 两个版本

同一个仓库把七条契约发两遍，因为运行时的失效方式变了，契约就得跟着改口径。

| | **无限token标准版** | **无限tokenPTC版** |
|---|---|---|
| 模型看到什么 | 每步一次工具调用 | 只有一个 `run_code`，其余能力都是程序内 SDK 绑定 `await tools.<名字>(参数)` |
| 治的失效方式 | 一次一次地犯懒 | 在程序里一次性犯错 |
| 契约 | 七条 | 同样七条，按程序口径重述 |
| 额外缺陷 | — | **P1–P7**（Programmatic Tool Calling 下独有的七种翻车方式）：吞异常、自证判决、摘要代替证据、隐式账本、捆绑不可逆、截断即完成、并发读改写。详见 `references/ptc-hazards.md` |
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

### 三档：一份任务到底需要多少流程

不是每个任务都要全套。开工前先给任务判档——**档位决定你守哪几条契约、要留哪些档**。

| 档位 | 什么时候用 | 守哪些契约 | 必须留什么 |
|---|---|---|---|
| **轻** | 只读、≤3 步、没有不可逆动作 | 4（核对免费）+ 7（可证伪收工） | 什么都不用写——但"做完了"仍要给真实退出码和真实输出 |
| **标准** | 有写入、有计数、≥4 步 | 1、2、3、4、7 | 任务契约书 + 状态文件 + 运行报告 |
| **严格** | 不可逆、外部可见、长流程、AIOps | 全部七条 | 上面全部，**外加每个不可逆动作一块决策** |

**拿不准就上一档。** 代价不对称：多写一份契约书花几分钟；漏掉一次后果推演可能是永久性的。

完整判档表（含 PTC 特有的触发条件：一段程序里有几个不可逆动作、这次要写几段程序）见 [`unlimited-token-mode/SKILL.md`](unlimited-token-mode/SKILL.md#判档) 与 [`unlimited-token-ptc/SKILL.md`](unlimited-token-ptc/SKILL.md#判档开工第一步)。

---

## 仓库内容

```
unlimited-token-mode/                 # ← 无限token标准版技能
├── SKILL.md                          # 技能主文件（七条契约、三档齿轮、开工五步、禁用清单）
├── plugins/
│   ├── utc-ledger.mjs                # 运行时证据账本 + 每步状态注入（契约 2 / 4 / 7）
│   ├── test-irreversible.mjs         # 回归：文字里提到某命令 ≠ 真的执行了它
│   ├── test-dispatch.mjs             # 回归：程序内的工具调用要带上真实退出码落账
│   └── replay-dispatch.mjs           # 回放真实录制的会话日志，与账本逐条比对
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
    ├── ab_compare.py                 # 同一批用例分别喂给纯字符串版与账本版，出对照表
    ├── e2e_verify.py                 # 把真实会话的账本与报告对账
    └── selftest.py                   # 校验器自测（17 项，含两个插件回归）

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
    └── selftest.py                   # 校验器自测（14 项）

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
- `preset/cordis-preset-ptc.yml` —— PTC 版预设块。与标准版块**只有三处结构差异**：
  1. id 组换成 `preset-unlimited-token-ptc` / `unlimited-token-ptc` / order 6；
  2. 关掉 `workflow-ptc` 与 `tool-workflow`（PTC 下模型只有一个 `run_code`，那套分步工作流工具没有对应机制）；
  3. 加 `tool-presentation: { mode: ptc }` —— 这一项决定了模型到底看到"一堆原生工具"还是"一个 `run_code`"，是整份配置里最关键的一行。
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
      和仓库里那个目录比对。标准版 18 个文件，PTC 版 13 个文件。
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
      unlimited-token / 无限token标准版 / order 5 / 32 个插件 / 呈现 native
      unlimited-token-ptc / 无限tokenPTC版 / order 6 / 33 个插件 / 呈现 ptc
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

**改前 / 改后的实测对照。** 校验器的第一版（**v1**）完全没有账本，只比对字符串。同一批 23 个测试产物分别喂给两版：

| | 判对 | 放过 | 误杀 | 崩溃 |
|---|---|---|---|---|
| v1（纯字符串） | 8 | 14 | 1 | 0 |
| v2（运行时账本） | 23 | 0 | 0 | 0 |

14 个坏产物被 v1 放过了——因为那一版里"真实输出"这一栏是模型自己写的，它分不出真核对和抄得很像的假核对。复现：`python unlimited-token-mode/scripts/ab_compare.py <v1 脚本目录>`。

那 23 个用例里有 **6 个是诚实产物**，这一半更重要：只抓坏人的校验器很好写——永远说 no 就行。v1 还**误杀过 1 个**：一次真实的 `df -h` 磁盘检查被判成了假核对，原因是那一版按词元相似度定罪，而模型把命令换了个说法。那一次误杀就是下面这条规则的由来。

**为什么非要一个外部账本？** 因为校验器和被校验的东西如果出自同一个来源，规则写得再细也没用。只要"真实输出"这一栏是模型自己填的，它就能先抄一遍字段说明、再写一句"我确认过了"，然后通过。字符串比对分不出「真核对」和「抄得很像的假核对」。运行时账本可以：命令没执行过就是 `LEDGER-NO-CALL`，输出被改写就是 `LEDGER-OUTPUT-MISMATCH`，退出码编造就是 `LEDGER-EXIT-MISMATCH`。而那个文件模型写不了。

---

## 适用边界

- 只读、一次性的小任务上全套是浪费——用**轻档**（只守契约 4 与 7）。各档丢掉了什么，见 [三档](#三档一份任务到底需要多少流程)。
- 如果**框架本身**已经强制了计数和核对（比如运行时每步都会注入状态），那七条契约里有两条——*状态外置*和*核对免费*——就退化成「确认注入真的存在」。其余五条照旧。
- 这个模式治的是**流程缺陷**，不治能力上限。它让模型不犯已知的错，不会让它变聪明。
- **证据档不是同一回事，每次运行都会打印这次用的是哪一档。** 档越高，能证明的越多：
  `ledger` 档（装了账本插件、且账本里有真实工具执行记录）= 能证明命令真的跑过、输出没被改写、退出码对得上；
  `source-literal` 档（装了插件，但账本只有程序级的粗记录）= 只能证明「这段命令文字出现在提交内容里」，**证明不了它被执行过**，`--strict` 下直接判失败；
  `shape-only` 档（没装插件）= 只能看形态，证明不了任何执行。
- **它校验的是留档，不是意图。** 一个铁了心要骗过它的模型，可以先跑一条无关命令，再把那条命令的真实输出贴进来。这个洞是结构性的，本模式不假装能堵。
- **词元相似度永不判失败。** 这类信号只作提示，原因很具体：一个诚实的模型会把命令换个说法（`df -h` 写成"看一下磁盘占用"），于是它的措辞和账本里记的命令**长得不一样**。任何把"措辞不同"当成"编造"的规则，都会误伤这种诚实行为。相似度可以报警，不能定罪；定罪只能靠运行时账本——它记的是真的跑过什么。
- PTC 版假设宿主只给一个 `run_code`。在原生工具调用的宿主上，它是错的版本——它的规则描述的是并不存在的机制。
- PTC 版治不了运行时藏起来的东西：程序被 `timeoutMs` 杀掉时，它算出的任何结果都进不了对话。对策是结构性的（分段提交、边跑边落盘），不是措辞性的。
- **程序内的工具调用会落账，前提是框架发出 `tool/ptc-dispatch` 事件**（当前 DSH 会发）。落账时带 `scope: "inner"`，记的是**真实命令、真实工具名、真实退出码**——这才让 PTC 下也能到 `ledger` 档。老版本框架不发这些事件时会降级为 `source-literal` 档并明说。代价：账本行数约为原来的 3 倍（实测一个会话里 158 次外层调用产生了 491 条内层记录）。

---

## 出处

针对的问题清单与实测数据来自 [karminski/deepseek-v4.1-flash-1017-issue-pov](https://github.com/karminski/deepseek-v4.1-flash-1017-issue-pov) 的 `AGENT-CAPABILITY-REPORT.md`，以及作者的使用建议。

总结作者的建议：**用它写代码没毛病，但用它做 AIOps 需要长个心眼。**
