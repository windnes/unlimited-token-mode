# PTC 专属缺陷：P1–P7

**怎么用这份文件**：这是 P1–P7 的唯一权威定义处，别处只放索引。写 `run_code` 之前扫一眼对应缺陷的「硬规则」；写完程序用「自检」逐条对；报告被判 FAIL 时回到「机制」看它为什么必然复现。
所有反例与正例都是 `run_code` 的 `code` 参数内容——async TS 函数体，只可用可擦除语法（无 `enum`、无 namespace、无 `import`），工具一律走 `await tools.<name>(args)`。
编号与名称固定，引用时逐字写：P1 吞异常、P2 自证判决、P3 摘要代替证据、P4 隐式账本、P5 捆绑不可逆、P6 截断即完成、P7 并发读改写。

---

## P1 · 吞异常

### 症状

程序跑完了，`return` 里没有错误，于是对话里得到「全部通过」。实际上 `tools.bash` reject 了 `ToolCallError`，被 `catch` 吃掉，失败的步骤从来没执行完。

### 机制

`try/catch` 在普通工程里是正确习惯：程序要活下去，别让一次失败终止整批。但在 PTC 里这个习惯把两件事混成一件——**工具失败**变成了**程序成功**。`run_code` 只把 `return` 与 `console.log` 送进对话，程序内部捕获到的 `ToolCallError` 如果不写进返回值，模型自己也看不见；它看到的是一个正常返回的对象，于是按「跑完了」继续。标准版里失败至少还是一次可见的红色工具结果，PTC 把它挪进了模型的视野盲区。

### 硬规则

失败只有两条出路：**要么抛出**（让 `run_code` 整体失败），**要么把原始错误放进返回值并标记 `fail`**。`catch` 块不许为空，不许只 `return` 一个成功对象。原始错误必须保留 `toolName` 与 `message` 原文，不许改写成「执行遇到问题」。

### 反例

```ts
// 反例：空捕获 + 无条件 return 成功，ToolCallError 被洗白
const touched: string[] = [];
try {
  await tools.bash({ command: "pnpm -r build", description: "构建全部工作区包" });
  touched.push("build");
  await tools.bash({ command: "pnpm test", description: "运行全部单元测试" });
  touched.push("test");
} catch {}
return { ok: true, steps: touched.length };
```

### 正例

```ts
// 正例 A：不吞，直接抛——让 run_code 以失败结束
await tools.bash({ command: "pnpm -r build", description: "构建全部工作区包" });
await tools.bash({ command: "pnpm test", description: "运行全部单元测试" });
return { ok: true, note: "两条命令都没有 reject" };

// 正例 B：必须继续跑时，把原始错误装进返回值并标 fail
const ledger: Array<{ cmd: string; exit: number; stdout: string }> = [];
for (const cmd of ["pnpm -r build", "pnpm test"]) {
  try {
    const r = await tools.bash({ command: cmd, description: "按序执行构建与测试" });
    ledger.push({ cmd, exit: 0, stdout: String(r) });
  } catch (e) {
    const err = e as { toolName?: string; message?: string };
    ledger.push({ cmd, exit: -1, stdout: `${err.toolName ?? "unknown"}: ${err.message ?? String(e)}` });
  }
}
const failed = ledger.filter((x) => x.exit !== 0);
return { ok: failed.length === 0, fail: failed.length, ledger };
```

### 自检

- [ ] 每个 `catch` 块里要么 `throw`，要么往返回值写一条 `exit: -1` 且带 `toolName` / `message`
- [ ] 返回值的 `ok` 由证据行算出，不是常量
- [ ] 没有任何 `catch {}`、`catch (e) {}`、`catch (e) { /* ignore */ }`

---

## P2 · 自证判决

### 症状

`return { allPass: true }`。程序自己算出了结论，对话里只拿到这个结论，没有任何能推翻它的原始输出。

### 机制

PTC 把「跑命令 → 读输出 → 判断」压进同一个程序，判断离证据只有一步，于是很自然地在程序里 `filter().every()` 出一个布尔值再返回它。问题在于：这个布尔值和证据一起被丢弃，只有布尔值进入对话。判决一旦由被测方自己产出，它就不可证伪——模型无法回答「如果实现错了，这条证据会怎么变红」，因为变红的信息已经在程序里被消化掉了。标准版的模型至少要把工具输出抄进回复；PTC 里它抄的是自己算的结论。

### 硬规则

程序只**搬运证据**，不做判决。返回值形态固定为 `[{ cmd, exit, stdout }]`，`exit` 是整数，`stdout` 是原始片段。`pass` / `fail` 写在对话里，依据是这些证据行；程序里出现 `allPass`、`pass: true`、`ok: true` 作为唯一返回值即违规。

### 反例

```ts
// 反例：程序自己判决，只返回结论
const files = ["src/a.ts", "src/b.ts", "src/c.ts"];
let good = 0;
for (const f of files) {
  const r = await tools.bash({ command: `pnpm exec tsc --noEmit ${f}`, description: "类型检查单个文件" });
  if (String(r).includes("error")) good += 1;
}
return { allPass: good === files.length };
```

### 正例

```ts
// 正例：只搬原始证据，判决留给对话
const files = ["src/a.ts", "src/b.ts", "src/c.ts"];
const evidence: Array<{ cmd: string; exit: number; stdout: string }> = [];
for (const f of files) {
  const cmd = `pnpm exec tsc --noEmit ${f}`;
  try {
    const r = await tools.bash({ command: cmd, description: "类型检查单个文件" });
    const out = String(r);
    evidence.push({ cmd, exit: 0, stdout: out.slice(0, 400) });
  } catch (e) {
    const err = e as { toolName?: string; message?: string };
    evidence.push({ cmd, exit: -1, stdout: `${err.toolName ?? "unknown"}: ${err.message ?? String(e)}`.slice(0, 400) });
  }
}
return { evidence, truncatedAt: 400 };
```

### 自检

- [ ] 返回值里没有 `allPass` / `pass: true` / `ok: true` 这类结论字段
- [ ] 每条证据都有 `cmd`、整数 `exit`、非空 `stdout`
- [ ] 对话里能写出「如果实现错了，哪一行 `exit` 会变成非 0」

---

## P3 · 摘要代替证据

### 症状

`return { summary: "3 个文件已改，都正常" }`。程序确实干活了，但对话里只有一段概括，没有 diff、没有退出码、没有原始输出片段。

### 机制

`run_code` 的输出只有一个 `result` 字段，而中间过程全部丢弃，所以「把结果压缩一下再返回」在 PTC 里几乎是被鼓励的默认写法：输出越小越好读。但压缩是不可逆的——一旦只返回摘要，模型就失去了核对的能力，它只能复述摘要。标准版里它至少还能回头翻工具输出；PTC 里那些输出已经被 VM 回收。摘要本身还带着自己的措辞，第二段程序读到的是「都正常」，而不是文件实际内容。

### 硬规则

返回里必须有**至少一条逐字原始片段**：diff 的 `@@` 头、命令的 `exit=` 行、错误的第一行。摘要只能附在证据**后面**，且不许替代证据。改文件时返回 `git diff --stat` 加至少一段真实 diff；跑命令时返回 `exit` 加前若干行 stdout。

### 反例

```ts
// 反例：只返回摘要，diff 被程序吞掉
const targets = ["src/a.ts", "src/b.ts", "src/c.ts"];
for (const f of targets) {
  await tools.edit({ file_path: f, old_string: "TODO", new_string: "DONE" });
}
return { summary: "3 个文件已改，都正常" };
```

### 正例

```ts
// 正例：摘要 + 逐字证据一起返回
const targets = ["src/a.ts", "src/b.ts", "src/c.ts"];
const evidence: Array<{ cmd: string; exit: number; stdout: string }> = [];
for (const f of targets) {
  await tools.edit({ file_path: f, old_string: "TODO", new_string: "DONE" });
}
try {
  const stat = await tools.bash({ command: "git diff --stat", description: "查看改动文件统计" });
  evidence.push({ cmd: "git diff --stat", exit: 0, stdout: String(stat).slice(0, 800) });
  const diff = await tools.bash({ command: "git diff -- src/a.ts", description: "查看首个文件的原始差异" });
  evidence.push({ cmd: "git diff -- src/a.ts", exit: 0, stdout: String(diff).slice(0, 2000) });
} catch (e) {
  const err = e as { toolName?: string; message?: string };
  evidence.push({ cmd: "git diff", exit: -1, stdout: `${err.toolName ?? "unknown"}: ${err.message ?? String(e)}` });
}
return { touched: targets, evidence, note: "diff 已截断到 2000 字符，未改写内容" };
```

### 自检

- [ ] 返回值里有至少一条逐字原始片段（`@@`、`exit=`、错误首行）
- [ ] 摘要排在证据之后，没有替代任何一条证据
- [ ] 截断处标了 `truncatedAt` 或等价说明，且截断的是尾部不是中间

---

## P4 · 隐式账本

### 症状

计数、预算、进度、待办队列存在程序变量里当状态。`per-call` 下下一段程序从 0 重新数；`persistent-in-memory` 下变量还活着，但不可见、无 `_rev`、无并发保护，模型拿它当权威。

### 机制

PTC 让循环变得自然：`for` 写在程序里，`i` 就在手边，累加一行就完成，看起来比落盘省事得多。但程序变量是**执行期的**，不是**任务期的**：`per-call` 每次 `run_code` 是全新 VM，变量随调用消失；`persistent-in-memory` 里变量跨调用存活，却对模型不可见——它看不到值，只能凭上一段程序的返回值猜，而返回值是它自己写的摘要。于是账本退化成「上一段程序说自己数到几」，与标准版「凭记忆报余额」是同一个缺陷，只是换了个更体面的存放处。

### 硬规则

跨程序的量一律落盘：`state/*.json`，读 → 改 → 写 → `_rev+1`。程序变量只用于**本次计算**，不许承载任何跨程序语义。写前再读一次比对 `_rev`，磁盘上的 `_rev` 与读到的不同就作废本次增量并重读。

### 反例

```ts
// 反例：进度存在程序变量里，下一段程序从 0 重新数
let done = 0;
for (const f of ["src/a.ts", "src/b.ts", "src/c.ts"]) {
  await tools.edit({ file_path: f, old_string: "TODO", new_string: "DONE" });
  done += 1;
}
return { done, note: "下一段 run_code 看不到 done" };
```

### 正例

```ts
// 正例：账本落盘，_rev 递增；写前再读一次防覆盖
const path = "state/run.json";
const before = JSON.parse(String(await tools.read({ file_path: path }))) as {
  _rev: number;
  files_done: string[];
};
const revRead = before._rev;
const nextFiles = [...before.files_done, "src/a.ts"];
for (const f of ["src/a.ts"]) {
  await tools.edit({ file_path: f, old_string: "TODO", new_string: "DONE" });
}
const fresh = JSON.parse(String(await tools.read({ file_path: path }))) as { _rev: number };
if (fresh._rev !== revRead) {
  return { applied: false, reason: "stale-rev", revRead, revOnDisk: fresh._rev };
}
const next = { _rev: revRead + 1, files_done: nextFiles, updated_at: new Date().toISOString() };
await tools.write({ file_path: path, content: JSON.stringify(next, null, 2) + "\n" });
return { applied: true, revBefore: revRead, revAfter: next._rev, evidence: [{ cmd: `write ${path}`, exit: 0, stdout: JSON.stringify(next) }] };
```

### 自检

- [ ] 每个跨程序计数量都在 `state/*.json` 里，且本次程序改过它
- [ ] 返回值里带 `revBefore` / `revAfter`，差值恰好是本次改动数
- [ ] 没有用 `persistent-in-memory` 的变量当权威状态

---

## P5 · 捆绑不可逆

### 症状

一个 `run_code` 里塞了多个破坏性动作（删目录 + 强推 + 对外发消息），或者破坏性动作根本没写进 `description`，`description` 只写「更新配置」。

### 机制

PTC 把多步合并成一个程序的成本降到几乎为零，于是「顺手做完」变成默认写法：一次 `run_code` 里既读又写又发。`description` 是人在对话里唯一能看见的入口，程序内部却是一整段黑箱。`sandbox_permissions` 是按**整个程序**授予的——把破坏性步骤夹在良性步骤里，等于用良性步骤的名义取得了破坏性步骤的权限，而且这个提权没有任何单独一次调用可以被拦下。标准版里每个不可逆动作都是一次独立工具调用，看得见、拦得住；PTC 里它们共享同一个授权。

### 硬规则

一个 program 最多**一个**不可逆动作，且必须在 `description` 里点名具名实体（「删除 state/ 目录」「强推 main 分支」「向 #deploy 发消息」）。不可逆动作之前的核对放进**前一个**程序，本程序只执行。任何 `sandbox_permissions` 提升都单独成一个程序，且 `justification` 说明为什么需要。

### 反例

```ts
// 反例：一个程序里三个破坏性动作，description 只写「更新配置」
await tools.bash({ command: "rm -rf state", description: "清理临时目录" });
await tools.bash({ command: "git push --force origin main", description: "推送" });
await tools.send_message({ target: "lead", message: "已上线" });
return { ok: true };
```

### 正例

```ts
// 正例：本程序只做一件不可逆的事，description 点名实体；核对放在前一个程序
const decision = await tools.read({ file_path: "state/decision-P5.md" });
const named = String(decision).includes("删除 state/ 目录");
if (!named) {
  return { executed: false, reason: "缺少点名实体的决策块", evidence: [{ cmd: "read state/decision-P5.md", exit: 0, stdout: String(decision).slice(0, 300) }] };
}
const evidence: Array<{ cmd: string; exit: number; stdout: string }> = [];
try {
  const r = await tools.bash({ command: "rm -rf state", description: "删除 state 目录" });
  evidence.push({ cmd: "rm -rf state", exit: 0, stdout: String(r).slice(0, 400) });
} catch (e) {
  const err = e as { toolName?: string; message?: string };
  evidence.push({ cmd: "rm -rf state", exit: -1, stdout: `${err.toolName ?? "unknown"}: ${err.message ?? String(e)}` });
}
return { executed: true, irreversible: "删除 state/ 目录", evidence };
```

### 自检

- [ ] 本程序的 `description` 点名了实体与动作，5–10 个词
- [ ] 本程序内不可逆动作数 ≤ 1
- [ ] 有 `sandbox_permissions` 时，它没有和良性步骤混在同一个程序
- [ ] 对应的 `## 决策` 块存在，且写了二阶后果

---

## P6 · 截断即完成

### 症状

`run_code` 被 `timeoutMs` 杀掉，或输出被 `maxOutputBytes`（`67108864`，64 MiB）截断，对话里没有 `return`，于是模型把「没有输出」读成「没消息就是好消息」，汇报「已完成」。

### 机制

`timeoutMs`（默认 `120000`，上限 `600000`）和 `maxOutputBytes` 都是**事后**信号：它们到来时程序已经跑了一半，或者结果已经不完整。更麻烦的是外部杀进程时程序里的 `catch` 不一定跑——VM 被终止，收尾代码没有机会执行，所以「在 `catch` 里记一笔超时」这条路不可靠。PTC 里这个缺陷比标准版更隐蔽：标准版至少能看到一次超时的工具结果，PTC 里被杀的调用可能只留下一个空结果或一句错误，而模型手上还有一段看起来完整的程序文本，很容易按文本而不是按证据汇报。

### 硬规则

长流程**分段提交**，每段一个 `run_code`，单段预留余量（`timeoutMs` 上限 `600000`，单段目标远低于它）。每段开跑**之前**写 `state/segN.inflight.json`，跑完写 `state/segN.done.json`——因为被外部杀掉时程序内的 `catch` 不跑，失败标记只能在开跑前落。程序内另记 `timedOut`（用 elapsed 与 `timeoutMs` 比较）与 `truncated`（输出长度触顶）。汇报前先读标记：存在 `inflight` 而没有 `done` 的段，判决只能是 `partial`。

### 反例

```ts
// 反例：一个程序跑完整流程，被杀之后按「跑完了」汇报
for (let i = 1; i <= 40; i++) {
  await tools.bash({ command: `node step-${i}.mjs`, description: "执行流水线第 N 步" });
}
return { done: true, steps: 40 };
```

### 正例

```ts
// 正例：开跑前落 inflight，分段提交，程序自己记 timedOut
const seg = 3;
const timeoutMs = 120000;
const startedAt = Date.now();
await tools.write({
  file_path: `state/seg${seg}.inflight.json`,
  content: JSON.stringify({ seg, startedAt: new Date(startedAt).toISOString(), timeoutMs }),
});

const ledger: Array<{ cmd: string; exit: number; stdout: string }> = [];
let timedOut = false;
for (const cmd of ["node step-a.mjs", "node step-b.mjs"]) {
  const t0 = Date.now();
  try {
    const r = await tools.bash({ command: cmd, description: "执行本段流水线步骤" });
    const out = String(r);
    const truncated = out.length > 4000;
    ledger.push({ cmd, exit: 0, stdout: out.slice(0, 4000) });
    if (truncated) {
      ledger.push({ cmd: `${cmd} (output)`, exit: 0, stdout: "TRUNCATED at 4000 chars" });
    }
  } catch (e) {
    const err = e as { toolName?: string; message?: string };
    if (Date.now() - t0 >= timeoutMs) timedOut = true;
    ledger.push({ cmd, exit: -1, stdout: `${err.toolName ?? "unknown"}: ${err.message ?? String(e)}` });
  }
  if (timedOut) break;
}

await tools.write({
  file_path: `state/seg${seg}.done.json`,
  content: JSON.stringify({ seg, timedOut, elapsedMs: Date.now() - startedAt, finishedAt: new Date().toISOString() }),
});
return { seg, timedOut, ledger, nextSeg: timedOut ? null : seg + 1 };
```

### 自检

- [ ] 本段开跑前写了 `inflight`，跑完写了 `done`
- [ ] 返回值里有 `timedOut` 与 `elapsedMs`，不是常量
- [ ] 输出截断处显式标了 `TRUNCATED`
- [ ] 存在 `inflight` 无 `done` 的段时，报告判决写的是 `partial` 不是 `pass`

---

## P7 · 并发读改写

### 症状

`Promise.all` 里三个分支同时对 `state/run.json` 做读-改-写，三个分支都读到 `_rev: 7`，都写回 `_rev: 8`，其中两次改动丢失，`_rev` 不再反映真实改动数。

### 机制

PTC 明确允许只读调用 `Promise.all` 并发，`maxPendingCalls` 还是 `128`——并发在 PTC 里是廉价且被鼓励的。写调用要求单独串行、按提交顺序执行，但这条约束写在运行时文档里，不写在类型系统里：程序里 `Promise.all([...])` 一包，读-改-写在三个分支里各跑一遍，运行时不会替模型拒绝。读与写是两次独立调用，中间没有任何原子性保证，`_rev` 只是约定，不是锁。标准版里模型一次只能发一个工具调用，这类交错很难发生；PTC 让它变成一行代码。

### 硬规则

同一路径的读-改-写一律串行 `await`，一个接一个，不在 `Promise.all` 里。并发只给**纯读**（`read` / `grep` / `glob` / 只读 `bash`）。写入前比对 `_rev`，不一致就作废重来，不做「合并」。

### 反例

```ts
// 反例：Promise.all 里三个分支对同一个文件读-改-写，_rev 互相覆盖
await Promise.all(
  [1, 2, 3].map(async (i) => {
    const cur = JSON.parse(String(await tools.read({ file_path: "state/run.json" }))) as {
      _rev: number;
      checks_spent: number;
    };
    cur.checks_spent = cur.checks_spent + 1;
    cur._rev = cur._rev + 1;
    await tools.write({ file_path: "state/run.json", content: JSON.stringify(cur) });
    return { i, rev: cur._rev };
  }),
);
return { ok: true };
```

### 正例

```ts
// 正例：并发只用于纯读；读-改-写串行 await
const paths = ["state/run.json", "state/aggregate.json", "state/events.jsonl"];
const raws = await Promise.all(paths.map((p) => tools.read({ file_path: p })));
const run = JSON.parse(String(raws[0])) as { _rev: number; checks_spent: number; updated_at: string };

// 写路径：一个接一个，每步前重读比对 _rev
const revRead = run._rev;
const nextRun = { ...run, checks_spent: run.checks_spent + 1, _rev: revRead + 1, updated_at: new Date().toISOString() };
const fresh = JSON.parse(String(await tools.read({ file_path: "state/run.json" }))) as { _rev: number };
if (fresh._rev !== revRead) {
  return { applied: false, reason: "stale-rev", revRead, revOnDisk: fresh._rev };
}
await tools.write({ file_path: "state/run.json", content: JSON.stringify(nextRun, null, 2) + "\n" });
await tools.write({ file_path: "state/aggregate.json", content: JSON.stringify({ rev: nextRun._rev }) + "\n" });

return {
  applied: true,
  revBefore: revRead,
  revAfter: nextRun._rev,
  evidence: [{ cmd: "write state/run.json", exit: 0, stdout: JSON.stringify(nextRun).slice(0, 300) }],
};
```

### 自检

- [ ] `Promise.all` 里只有纯读调用，没有任何 `write` / `edit` / 写型 `bash`
- [ ] 同一路径的读-改-写是串行 `await`
- [ ] 写前比对过 `_rev`，不一致时返回 `applied: false` 而不是覆盖
- [ ] 返回值里 `revAfter - revBefore` 等于本次真实改动数
