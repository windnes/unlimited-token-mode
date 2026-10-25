---
name: unlimited-token-ptc
description: 无限tokenPTC版。这是 Programmatic Tool Calling 下的版本：模型只有一个 run_code 工具，其余能力都是程序内 SDK 绑定 await tools.<name>(args)。标准版的七条契约仍然成立，但失效方式变了——模型不再一个动作一个动作地犯懒，而是在一段程序里一次性犯错：错误被 catch 吞掉、被 return {ok:true} 洗白、被 timeoutMs 打断后当成跑完。治的是程序化执行引入的七类新缺陷：吞异常、自证判决、摘要代替证据、隐式账本、捆绑不可逆、截断即完成、并发读改写。多步执行、长流程、有不可逆动作、要下「完成」结论时加载。
whenToUse: 在 PTC 环境（只有 run_code 一个工具，其余能力是程序内 SDK 绑定）下执行超过 3 步的任务、长流程监控、含不可逆动作、或需要给出「做完了」的结论时。框架不支持 PTC 时改用标准版。
---

# 无限tokenPTC版

## 诊断

PTC 把工具调用从「一次一次的手」变成「一段程序」：模型只看到 **一个** 工具 `run_code`，其余全部降级为程序内 SDK 绑定 `await tools.<name>(args)`。七条契约一条都没作废——**失效方式变了**。

标准版里，模型是一个动作一个动作地犯懒：查一次状态就算查过，说一句「我核对一下」就当核对过。PTC 里它改在**一段程序里一次性犯错**：`catch {}` 把 `ToolCallError` 吞掉，`return { ok: true }` 把没跑的步骤洗白，被 `timeoutMs` 杀掉之后连 `return` 都没有，于是「没有输出」被读成「没消息就是好消息」。

两条结构性事实决定了这个版本怎么写：

- **只有 `return` 与 `console.log` 的内容进入对话**；其余中间结果全部丢弃。程序里做过的核对、抓到过的错误，不写进返回值就等于没发生。
- 默认执行模式 `per-call`：每次 `run_code` 是全新 VM，**变量不跨调用存活**；`persistent-in-memory` 下变量跨调用存活，但不可见、无版本、无并发保护。

硬上限一律写真实数字，不写含糊的「省着点」：`timeoutMs` 默认 `120000`、上限 `600000`；`maxOutputBytes` `67108864`（64 MiB）；`maxMessageBytes` `134217728`（128 MiB）；`maxPendingCalls` `128`；`maxOldGenerationSizeMb` `512`；`graceMs` `3000`。

七条契约的实测数字见 `references/evidence-map.md`；P1–P7 的完整定义见 `references/ptc-hazards.md`。

## 七条契约的 PTC 复述

每条 = 一条硬规则 + 一个 PTC 落点。违反即任务失败，不是「风格问题」。

### 1. 歧义契约

**规则**：动手前把任务里所有可多种读法的词列出来，每个词要么问、要么定。
**PTC 落点**：一个 program 只表达一个意图。提问程序必须显式放宽 `timeoutMs`——`ask_user_question` 在 PTC 里就是 `await tools.ask_user_question(...)`，人回答慢于默认 `120000` ms 会把整个程序杀掉、回答丢失，模型随后按默认读法继续，**这正是本条要治的失败**。提问程序只**返回**回答，不在同一个程序里据此动手。
**做法**：`run_code({ timeoutMs: 600000, ... })`，程序体只做一次提问，把回答原样 `return`。

### 2. 状态外置

**规则**：任何需要累加、递减、跨步保持的量，一律不放在程序变量里。
**PTC 落点**：循环天然在代码里（`for`），但**变量不是状态**。`per-call` 下变量每次消失，于是每段程序都从 0 开始数；`persistent-in-memory` 下变量活着，但不可见、无 `_rev`、无并发保护。账本只能落盘。
**做法**：`state/*.json` 读 → 改 → 写 → `_rev+1`；每个程序开头重读，写前再读一次比对 `_rev`。
**禁止**：用程序变量保存计数、预算、进度、待办队列。

### 3. 后果推演门

**规则**：任何不可逆动作之前写出二阶、三阶后果，**必须点名实体**。
**PTC 落点**：一个 program 最多一个不可逆动作，且必须在 `description` 里点名。`description` 是人在对话里唯一能看见的入口（必填，主动语态 5–10 词）。`sandbox_permissions` 是按**整个程序**授予的——把破坏性步骤夹在良性步骤里，等于偷偷提权。
**做法**：填 `templates/decision-log.md`，新增「程序 id / description / 程序内不可逆动作数」三栏。

### 4. 核对免费

**规则**：核对（dry-run / check / preview / 查询）永远不算浪费，任何不可逆动作前至少一次。
**PTC 落点**：核对输出不进对话（除非 `return`），核对的结构性成本已经降到最低，所以没有「太贵所以不查」的借口。唯一的新借口是 `try/catch`：核对了、失败了、被吞了，等于没核对。
**做法**：核对结果进返回值，原始输出逐字保留。

### 5. 信号时相

**规则**：每个信号先归类——**事前**（在动作之前到）还是**事后**（在事情已经发生之后才到）。事后信号不能当作「我可以先等着」的理由。
**PTC 落点**：`timeoutMs` 杀进程、`ToolCallError` reject、`maxOutputBytes` 截断，全部是**事后**信号——它们到来时程序已经跑了一半或结果已经不完整。外部杀进程时程序内的 `catch` 不一定跑，所以失败标记必须在长步骤**之前**落盘。
**做法**：每段程序开跑前写 `state/<seg>.inflight.json`，跑完写 `state/<seg>.done.json`。

### 6. 预算不设限

**规则**：不给模型加人为的稀缺。「省着点」「别浪费」这类话会让它用**减少核对**来省，而不是用减少浪费来省。
**PTC 落点**：写真实数字——`timeoutMs` 默认 `120000`、上限 `600000`；`maxOutputBytes` `67108864`（64 MiB）；`maxMessageBytes` `134217728`（128 MiB）；`maxPendingCalls` `128`；`maxOldGenerationSizeMb` `512`；`graceMs` `3000`。触顶后的行为写清楚：停止，并在报告里写「因 X 上限未完成 Y」，不把未验证项写成已验证。
**校准**：被证实的是「核对一旦有成本就少做」（盲盒 `v7-paid-staff`：去问的局数 **8/8 → 5/8**，中位数 **2 → 1**）；「囤积会刷新的额度」**已被证伪**（利用率 **0.98–1.00**，没有空转周期）。

### 7. 可证伪收工

**规则**：说「做完了」必须附带**一个本来可以失败、但没有失败的检验**，以及它的**真实输出**。
**PTC 落点**：`return` **原始证据**（退出码 + 原始输出片段），不是程序自己算出的 `pass: true`。程序只负责把证据搬进对话，判决写在对话里。
**做法**：按 `templates/run-report.md` 交付，§2 引用 `templates/program-log.md` 的程序账本。

## 七个 PTC 专属缺陷

P1–P7 的完整定义（症状 / 机制 / 硬规则 / 反例 / 正例 / 自检）只在 `references/ptc-hazards.md`。这里只给症状、硬规则和锚点。

### P1 · 吞异常

**症状**：`catch {}` 或 `catch (e) { return { ok: true } }` 把 `ToolCallError` 变成通过。
**硬规则**：失败要么抛出，要么把原始错误（`toolName` + `message`）原样放进返回值并标 `fail`。`catch` 块里不许空，不许只 `return` 成功。
**锚点**：`references/ptc-hazards.md#p1--吞异常`

### P2 · 自证判决

**症状**：程序自己算出 `allPass: true` 并只返回它，没有原始输出，无法被推翻。
**硬规则**：程序只返回 `[{ cmd, exit, stdout }]` 形态的原始证据；判决不许由程序算出来交给对话。
**锚点**：`references/ptc-hazards.md#p2--自证判决`

### P3 · 摘要代替证据

**症状**：返回「3 个文件已改，都正常」，而不是 diff / 退出码 / 原始输出。
**硬规则**：返回里必须有至少一条逐字原始片段，摘要只能附在证据后面。
**锚点**：`references/ptc-hazards.md#p3--摘要代替证据`

### P4 · 隐式账本

**症状**：把计数、预算、进度存在程序变量（尤其 `persistent-in-memory`）里当状态。
**硬规则**：跨程序的量一律落盘并维护 `_rev`；程序变量只用于本次计算。
**锚点**：`references/ptc-hazards.md#p4--隐式账本`

### P5 · 捆绑不可逆

**症状**：一个 program 里塞多个破坏性动作，或破坏性动作没写进 `description`。
**硬规则**：一个 program 最多一个不可逆动作，且 `description` 点名具名实体。
**锚点**：`references/ptc-hazards.md#p5--捆绑不可逆`

### P6 · 截断即完成

**症状**：被 `timeoutMs` 杀掉或被 `maxOutputBytes` 截断，仍按「跑完了」汇报。
**硬规则**：长流程分段提交；每段开跑前落 `inflight`、跑完落 `done`；汇报前先读标记，存在 `inflight` 无 `done` 即判 `partial`。
**锚点**：`references/ptc-hazards.md#p6--截断即完成`

### P7 · 并发读改写

**症状**：`Promise.all` 里对同一个状态文件做读-改-写，`_rev` 失效。
**硬规则**：同一路径的读-改-写一律串行 `await`；并发只给纯读。
**锚点**：`references/ptc-hazards.md#p7--并发读改写`

## 三档齿轮

按**不可逆性 × 规模**选档：

| 档 | 什么时候 | 启用 |
|---|---|---|
| 轻 | 只读、秒回滚、≤3 步、单 program | 契约 4 + 7，只守 P2/P3 |
| 标准 | 有写入、有计数、≥4 步或多 program | 契约 1–4 + 7，守 P1–P4 |
| 严格 | 不可逆、外部可见、长流程、AIOps | 全部七条 + 全部 P1–P7 + 决策块逐条留档 |

拿不准就上一档。

## 开工五步

1. **判档** —— 按上表选齿轮。
2. **签契约** —— 填 `templates/task-brief.md`，含新增的「程序预算与并发」节。歧义决议表没填满，不许开工。
3. **摆状态** —— 建 `state/`，写第一版 `state/run.json`（含 `_rev`），确定「谁是权威」。
4. **写程序** —— 一程序一意图、先读状态、证据返回、失败不吞。硬要求：
   - 一个 `run_code` 只表达一个意图；提问程序只返回回答，不在同一个程序里动手。
   - 程序开头读 `state/run.json`，不从记忆推导；写前再读一次比对 `_rev`，变了就作废本次增量。
   - `return` 里放 `[{ cmd, exit, stdout }]` 原始片段，不放程序自己算出的 `pass: true`。
   - `catch` 里把 `toolName` + `message` 写进返回值并标 `fail`，或直接 `throw`；不许空捕获。
   - 一个程序最多一个不可逆动作，`description` 点名；含提问或长步骤时显式设 `timeoutMs`（上限 `600000`），超长流程分段提交。
5. **收工** —— 跑 `python scripts/ptc_lint.py <产物> --kind report`（严格档加 `--strict`），再按 `templates/run-report.md` 交付。

## 禁用清单

写进任何提示词或程序前扫一遍，出现即删：

- `尽量节省` / `不要浪费` / `高效一点` / `用最少的调用` / `步数有限` / `token 有限`
- `先查一次状态` / `记住当前值` / `记住余额`
- `自己数` / `跑 10 次然后统计`（改成程序里的 `for`）
- `如果有风险就注意`（不点名的风险等于没有风险）
- `你觉得没问题就提交`（把判断权交还给一个不会自查的判断器）
- PTC 专属：`catch {}` 空捕获
- PTC 专属：`return { ok: true }` 无证据
- PTC 专属：`try { … } catch (e) { /* ignore */ }`
- PTC 专属：把变量当状态
- PTC 专属：一个程序里做两个不可逆动作
- PTC 专属：被截断后说「已完成」

## 自检

交付前逐条打勾：

- [ ] 契约 1：提问程序显式设了 `timeoutMs`（≤ `600000`），且只返回回答不动手
- [ ] 契约 2 / P4：没有任何跨程序的量只存在于程序变量里；账本在 `state/*.json` 且 `_rev` 递增
- [ ] 契约 3 / P5：每个不可逆动作都在 `description` 里点名，且一个 program 只有一个
- [ ] 契约 4：每个「不可行 / 没有选项」的结论前面都有一次真实核对
- [ ] 契约 5 / P6：每个被引用的保护机制都标了事前 / 事后；长流程分段提交，`inflight` / `done` 标记齐全
- [ ] 契约 6：提示词里没有制造稀缺感的词；硬上限写的是真实数字
- [ ] 契约 7 / P2 / P3：`return` 里是 `[{ cmd, exit, stdout }]` 原始证据，不是程序算出的 `pass: true`
- [ ] P1：每个 `catch` 块要么 `throw`，要么把 `toolName` + `message` 写进返回值并标 `fail`
- [ ] P7：`Promise.all` 里没有写调用；同一路径的读-改-写串行 `await`
- [ ] 收工前跑过 `python scripts/ptc_lint.py`，`--strict` 档无 FAIL

## 延伸

- `references/ptc-hazards.md` —— P1–P7 全文，唯一权威定义处。
- `references/harness-patterns.md` —— `run_code` 代码范式：循环 / 状态 / 证据返回 / 并发 / 提问 / 超时。
- `references/evidence-map.md` —— 七契约实测数字（继承标准版）+ PTC 侧的机制性证据。
- `references/consequence-gate.md` —— 后果门模板 + 两个 worked example（改成 program 视角）。
- `references/prompt-compiler.md` —— 把需求编译成「PTC 版提示词」的步骤。
- `templates/` —— task-brief / decision-log / run-report / program-log。
- `scripts/ptc_lint.py` —— 校验器，支持 `--kind brief|report|decision|program [--strict]`；`python scripts/selftest.py` 自测。

## 边界

- 只读、一次性的小任务上全套是浪费——用轻档。
- 如果**框架本身**已经强制了计数和核对（harness 注入），契约 2/4 只保留「确认注入存在」。
- 这个模式治的是**流程缺陷**，不治能力上限。它让模型不犯已知的错，不会让它变聪明。
- 如果框架不支持 PTC（只有原生工具调用），用标准版而不是本版：这里每一条落点都依赖 `run_code` 的 `return`、`timeoutMs`、`sandbox_permissions` 语义。
