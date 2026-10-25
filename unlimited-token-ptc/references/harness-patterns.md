# PTC 运行时模式：把契约 2、4、6、7 落到 run_code 层

标准版的写法是把循环**外置**：让 bash / node / python 去数，模型只写驱动脚本、读聚合结果。
PTC 版把这件事整个翻过来了——循环**本来就在程序里**（`for`、`while`、`Promise.all` 都是代码，
不是模型的心算），所以「循环外置」不再是问题。新的问题换了个位置：

> **循环在程序里，账本却还在变量里。**
> 变量不是状态：`per-call` 下它每次调用都消失，`persistent-in-memory` 下它活着但模型看不见、
> 没有 `_rev`、没有并发保护。**程序的 `return` 才是唯一出口**——只有 `return` 与 `console.log`
> 的内容进入对话，其余中间结果全部丢弃。

于是契约 2、4、6、7 在 PTC 里的落点从「把循环拿出去」变成「**把证据拿出来**」：
循环留在代码里，状态落到磁盘上，判决依据用 `return` 原样送进对话。

本文件所有代码都是 `run_code` 的 `code` 参数内容：async TS 函数体、可擦除语法、无 `enum`、
无 namespace、无 `import`。工具一律通过程序内 SDK 绑定调用：`await tools.<name>(args)`；
参数名按运行时绑定，本文件统一写 `path` / `command` / `content`。

---

## 1. 一程序一意图

### 反例：一个程序干四件事

```ts
// 反例 —— 读状态 + 改文件 + 跑测试 + 提交，全塞进一个程序
const st = JSON.parse(String(await tools.read({ path: "state/run.json" })));
await tools.write({ path: "src/a.ts", content: patched });
await tools.bash({ command: "npm test", description: "运行测试套件" });
await tools.bash({ command: "git commit -am 'wip'", description: "提交当前改动" });
return { ok: true };
```

四件事挤在一个程序里，一次失败就全部失效，而且失败点无法定位：测试挂了？提交挂了？
还是 `read` 读到的 `state/run.json` 根本不合法？更要紧的是**缺陷 P5 捆绑不可逆**——
`sandbox_permissions` 是按**整个程序**授予的，最后那行 `git commit` 的破坏性被前三行的良性命中掩掉了，
审查者看到的是一段「读状态、改文件、跑测试」的代码。

### 正例：拆成三个程序

```ts
// 程序 P1 —— run_code description: "读取运行状态并返回当前阶段"
const st = JSON.parse(String(await tools.read({ path: "state/run.json" })));
return { _rev: st._rev, phase: st.phase, open_items: st.open_items };
```

```ts
// 程序 P2 —— run_code description: "写入补丁并返回文件差异"
await tools.write({ path: "src/a.ts", content: patched });
const r = await tools.bash({ command: "git diff -- src/a.ts", description: "查看补丁差异" });
return [{ cmd: "git diff -- src/a.ts", exit: 0, stdout: String(r?.stdout ?? "") }];
```

```ts
// 程序 P3 —— run_code description: "运行测试并返回退出码与输出"
try {
  const r = await tools.bash({ command: "npm test", description: "运行测试套件" });
  return [{ cmd: "npm test", exit: 0, stdout: String(r?.stdout ?? "") }];
} catch (e) {
  return [{ cmd: "npm test", exit: 1, stdout: String(e?.message ?? e) }];
}
```

提交是第三个程序之后的第四个程序，且它的 `description` 里必须出现 `commit`，
`程序内不可逆动作数` 必须是 1（见 `consequence-gate.md`）。

### 判断标准

> **这个程序的 `description` 能不能写成一句 5–10 词的话？**

写不出来的三种典型症状：

| 症状 | 例子 | 处理 |
|---|---|---|
| `description` 里出现「并」「然后」「顺便」 | `"读状态并改文件并跑测试"` | 拆 |
| 一句话里有两个动词短语，宾语不同 | `"运行测试和提交改动"` | 拆 |
| 程序里出现第二个不可逆动作 | 两次 `git push`、一次 `rm` 加一次 `deploy` | 必须拆（缺陷 P5） |

`description` 是**主动语态 5–10 词**（中文按 8–40 字）。这句话不是装饰：
它和 `sandbox_permissions`、`timeoutMs` 一起，构成审查者能看到的**全部**信息。
写得含糊（`"cleanup"`、`"整理一下"`）等于把审查关掉。

### 自检

- 程序里只有一个「目标」，其余全是它的前置读取或后置取证。
- `description` 里没有任何连接词。
- 出现第二个写操作时，停下来问：这是同一个意图的两步，还是两个意图？

---

## 2. 状态在磁盘，不在变量

### 反例：把计数存在变量里

```ts
// 反例 —— 变量当账本
let checks = 0;
for (const f of files) {
  const r = await tools.bash({ command: `./check.sh ${f}`, description: "核对单个文件" });
  checks++;
}
return { checked: checks, allOk: true };
```

这个程序里 `checks` 活得很好——循环跑完它就在。但它是**隐式账本**（缺陷 P4）：

- `per-call` 下，程序一结束 `checks` 就消失；下一次 `run_code` 从 0 开始，没有任何地方写着「已经查了 12 个」。
- `persistent-in-memory` 下它活着，但**模型看不见它**——只有 `return` 或 `console.log` 才进对话，
  所以「现在花了多少」这个问题，答案不在上下文里，在某个摸不到的 VM 变量里。
- 它没有 `_rev`，所以第二个 agent（或你自己的上一次调用）改了磁盘上的计数，你无从发现。
- 它没有并发保护，`Promise.all` 里两个分支同时 `checks++` 会互相覆盖（缺陷 P7 并发读改写）。

### 正例：读 → 改 → 写 → `_rev + 1`

```ts
// 程序 P4 —— run_code description: "登记一次核对并返回新版本号"
const readJson = async (path: string) => JSON.parse(String(await tools.read({ path })));

const st = await readJson("state/run.json");
const seenRev = st._rev;

st.checks_spent = (st.checks_spent ?? 0) + 1;
st._rev = seenRev + 1;
st.updated_at = new Date().toISOString();

// 写前再读一次：磁盘上的 _rev 变了，说明有人先改过 → 重读重算，不覆盖
const again = await readJson("state/run.json");
if (again._rev !== seenRev) {
  return { ok: false, reason: "stale_rev", seen: seenRev, on_disk: again._rev };
}

await tools.write({ path: "state/run.json", content: JSON.stringify(st, null, 2) + "\n" });
return { ok: true, _rev_before: seenRev, _rev_after: st._rev, checks_spent: st.checks_spent };
```

要点：

- 返回值里带 `_rev_before` / `_rev_after`，下一次调用能对上号；这是**唯一**能跨程序传递的账本形态。
- 陈旧 `_rev` 的处理是**重读重算**，不是「合并」——合并需要语义，而你手里只有旧快照。
- 追加型事件（不可变动作）写 `state/events.jsonl`，一行一个；单值状态写 `state/run.json`。
  两者都不靠变量。

### 为什么不能换成 `persistent-in-memory`

有人会想：既然是「变量每次消失」导致的麻烦，那把模式切成 `persistent-in-memory` 不就行了？
不行。两种模式的差别只在**什么时候丢**，不在**能不能当账本**：

| | `per-call`（默认） | `persistent-in-memory` |
|---|---|---|
| 变量跨 `run_code` 存活 | 否，每次是全新 VM | 是，同一进程内 |
| 变量对模型可见 | 否（只有 `return` / `console.log` 可见） | 否（同上） |
| 有 `_rev` 版本号 | 无 | 无 |
| 有并发保护 | 无 | 无 |
| 进程被杀（`timeoutMs`）后还在 | 不适用 | 不一定，随进程一起丢 |
| 能否当账本 | **不能** | **不能** |

结论：「选 `persistent-in-memory` 就省掉状态文件」正好是缺陷 P4 隐式账本。
变量在两种模式下都**不可见**——只有 `return` 与 `console.log` 进对话，所以「现在花了多少」
这个问题的答案永远不在上下文里，除非你把它写进 `state/run.json` 再 `return` 出来。

### 自检

- 程序里没有任何 `let` 计数器的值被 `return` 当作「花了多少」——那个数字来自磁盘。
- 每个写操作都带 `_rev` 自增，且返回值里有前后版本号。
- 读完到写回之间，没有任何 `await` 之外的「记忆型」假设。

---

## 3. 证据返回

### 反例：程序自己下判决

```ts
// 反例 —— 程序算出结论，只返回结论
return { ok: true };
```

`{ ok: true }` 无法被推翻：读它的人看不到退出码、看不到原始输出、看不到被截断的尾部。
这是缺陷 P2 自证判决与缺陷 P3 摘要代替证据的合体——**判决由程序给出，证据却没出程序**。

### 正例：返回原始证据

```ts
// 程序 P5 —— run_code description: "运行测试并原样返回证据"
// 截断只切中间，保留头与尾；截断事实本身必须写进返回值
const clip = (s: string) => {
  const head = 2000;
  const tail = 2000;
  const text = String(s ?? "");
  if (text.length <= head + tail) return { text, truncated: false };
  return { text: text.slice(0, head) + "\n…<中略>…\n" + text.slice(-tail), truncated: true };
};

const run = async (command: string) => {
  try {
    const r = await tools.bash({ command, description: "执行命令并采集证据" });
    const c = clip(r?.stdout);
    return { cmd: command, exit: 0, stdout: c.text, truncated: c.truncated };
  } catch (e) {
    const c = clip(e?.message ?? e);
    return { cmd: command, exit: 1, stdout: c.text, truncated: c.truncated };
  }
};

return [await run("npm test"), await run("npm run lint")];
```

证据形状（字段名固定，报告里逐字沿用）：

| 字段 | 含义 | 缺失后果 |
|---|---|---|
| `cmd` | 原样命令行或 `tools.<name>(...)` 调用 | 无法复现，无法判断你跑的是不是这件事 |
| `exit` | 整数退出码 | 无法区分「通过」与「没跑到」 |
| `stdout` | 原始输出片段，**可截断但不可改写** | 只能信你的措辞 |
| `truncated` | 是否截断 | 读者会把截断后的安静当成「没有报错」 |

**保留头尾的理由**：报错栈的头几行是错误类型，尾部几行是失败断言与计数（`Tests: 3 failed`）；
只留头会丢掉结论，只留尾会丢掉因果。中间那段重复的进度条可以丢。

### 自检

- 返回值里没有任何 `ok` / `pass` / `allPass` 布尔字段单独出现——它必须与 `exit` 或原始输出同框。
- 每个 `exit` 都能对应到一条 `cmd`。
- 被截断的输出带 `truncated: true`，报告里也照写。

---

## 4. 失败不吞

### 反例：空 catch

```ts
// 反例 —— 失败被吃掉，程序继续宣称成功
try {
  await tools.bash({ command: "npm run migrate", description: "执行数据库迁移" });
} catch {}
return { ok: true };
```

失败的工具调用 reject `ToolCallError`（带 `toolName` 与 `message`）。空 `catch {}` 把它变成
一次安静的跳过，然后 `return { ok: true }` 把它变成一次「通过」——这就是缺陷 P1 吞异常。
比空 catch 更坏的是 `catch (e) { return { ok: true } }`：它连「发生过异常」这个事实都抹掉了。

### 正例 A：把失败也当证据返回

```ts
// 程序 P6 —— run_code description: "执行迁移并返回退出码与错误"
try {
  const r = await tools.bash({ command: "npm run migrate", description: "执行数据库迁移" });
  return [{ cmd: "npm run migrate", exit: 0, stdout: String(r?.stdout ?? ""), truncated: false }];
} catch (e) {
  // ToolCallError 的两个字段都要带出来：只写「失败」等于没写
  return [
    {
      cmd: "npm run migrate",
      exit: 1,
      tool: String(e?.toolName ?? "bash"),
      message: String(e?.message ?? e),
      truncated: false,
    },
  ];
}
```

### 正例 B：直接 `throw`

```ts
// 程序 P7 —— run_code description: "执行迁移，失败即中止"
// 不 catch：让整个 run_code 失败，失败本身就是证据
const r = await tools.bash({ command: "npm run migrate", description: "执行数据库迁移" });
return [{ cmd: "npm run migrate", exit: 0, stdout: String(r?.stdout ?? ""), truncated: false }];
```

两种都合法。**唯一禁止的是「catch 之后继续宣称成功」**。判断标准一句话：

> `catch` 块里要么 `throw`，要么 `return` 一个带 `exit: 1` 的对象。
> 两者都没有，就是吞异常。

### 自检

- 全文搜 `catch`：每一处后面 3 行内都有 `throw` 或 `return {`（含 `exit: 1`）。
- 没有任何 `catch` 块是空的。
- `ToolCallError` 的 `toolName` 与 `message` 至少有一个进了返回值。

---

## 5. 并发只用于纯读

### 反例：`Promise.all` 里读-改-写同一个文件

```ts
// 反例 —— 三个分支同时改 state/run.json
await Promise.all([
  (async () => {
    const s = JSON.parse(String(await tools.read({ path: "state/run.json" })));
    s.checks_spent += 1;
    await tools.write({ path: "state/run.json", content: JSON.stringify(s) });
  })(),
  (async () => {
    const s = JSON.parse(String(await tools.read({ path: "state/run.json" })));
    s.checks_spent += 1;
    await tools.write({ path: "state/run.json", content: JSON.stringify(s) });
  })(),
]);
return { ok: true };
```

两个分支读到同一份 `_rev`，各自 +1 再写回，结果只 +1。`_rev` 从并发检测器退化成装饰品（缺陷 P7 并发读改写）。
更隐蔽的版本是「并发跑测试 + 并发改文件」，测试读到半截文件，报错看起来像实现有 bug。

### 正例：并发只跑只读查询，写操作串行

```ts
// 程序 P8 —— run_code description: "并发读取三处状态后串行登记"
const [runRaw, gitOut, hits] = await Promise.all([
  tools.read({ path: "state/run.json" }),
  tools.bash({ command: "git status --porcelain", description: "读取工作区状态" }),
  tools.grep({ pattern: "TODO", include: "*.ts" }),
]);

const st = JSON.parse(String(runRaw));
st.checks_spent = (st.checks_spent ?? 0) + 1;
st._rev = st._rev + 1;
st.updated_at = new Date().toISOString();

// 写操作一次一个 await，按提交顺序执行
await tools.write({ path: "state/run.json", content: JSON.stringify(st, null, 2) + "\n" });

return { dirty: String(gitOut?.stdout ?? "").split("\n").filter(Boolean).length, todo: hits, _rev: st._rev };
```

规则三条：

1. `Promise.all` 里只放**只读**调用：`read` / `grep` / `glob` / 只读 `bash`（`git status`、`ls`、`--dry-run`）。
2. 任何写调用（`write` / `edit` / 带副作用的 `bash` / `present` / `send_message`）单独 `await`，一次一个。
3. 需要「并发地做 N 件写事」时，把它们排进数组，用 `for` 串行跑——并发带来的是覆盖，不是速度。

写调用在运行时侧按提交顺序执行；在程序里显式串行，是为了让**你的代码**与这个顺序一致。

### 自检

- `Promise.all` 的每一项都是只读调用。
- 数组里没有两个分支碰同一个路径。
- 所有 `tools.write` / `tools.edit` 都在 `Promise.all` 之外。

---

## 6. 提问与超时

### 为什么必须显式设 `timeoutMs`

`ask_user_question` 在 PTC 里也是程序内调用：`await tools.ask_user_question(...)`。
人在另一端看屏幕、思考、打字，慢于 `timeoutMs`（默认 `120000`）就会把**整个程序杀掉**，
回答直接丢失。丢失之后，模型手上只剩自己的默认读法，于是按默认读法继续动手——
**这正是契约 1 要治的那个失败**（歧义被静默消解）。

所以：任何含提问的程序必须显式放宽 `timeoutMs`（建议 `600000`，即 `run_code` 的上限），
并且这个程序**只返回回答，不在同一个程序里据此动手**。

### 反例：一边问一边动手

```ts
// 反例 —— 同一个程序里问完就改
const a = await tools.ask_user_question({ questions: [{ id: "x", question: "要删吗？" }] });
if (String(a).includes("删")) {
  await tools.bash({ command: "rm -rf build", description: "删除构建目录" });
}
return { ok: true };
```

两个问题：回答丢失时程序已经跑完一半（缺陷 P6 截断即完成），且不可逆动作夹在提问之后、
`description` 里只写了「确认」——`sandbox_permissions` 却是整个程序的（缺陷 P5 捆绑不可逆）。

### 正例：只问，只返回

```ts
// 程序 P9 —— run_code description: "向用户确认退货运费归属"
// 调用 run_code 时带 timeoutMs: 600000 —— 默认 120000 不够人回答
const a = await tools.ask_user_question({
  questions: [
    {
      id: "return-shipping",
      header: "确认口径",
      question: "退货运费从哪个账户扣？",
      options: [
        { label: "平台承担（推荐）", description: "与现有结算口径一致" },
        { label: "商家承担", description: "需要改结算规则" },
      ],
    },
  ],
});

// 只返回回答。判断与动手在下一个程序里，且下一个程序先读状态再决定
return { answers: a };
```

### 自检

- 含 `ask_user_question` 的程序，`timeoutMs` 是显式写的 `600000`。
- 程序里除提问外没有任何写操作。
- 返回值里有完整回答，没有「用户表示……」这类转述。

---

## 7. 截断与超时是事后信号

`timeoutMs` 杀进程、`maxOutputBytes` 截断（`67108864`，64 MiB）、`ToolCallError` 抛错，
三者的共同点是：**它们到来时，程序已经跑了一半，或者结果已经不完整**。
它们不能当作事前保护，只能当作事后标记。做法三条。

### 7.1 分段提交：每段先落盘，再继续

```ts
// 程序 P10 —— run_code description: "分段运行三组检查并逐段落盘证据"
// 截断只切中间、保留头尾，并把「切过」这个事实写进返回值
const clip = (s: string) => {
  const text = String(s ?? "");
  const head = 2000;
  const tail = 2000;
  if (text.length <= head + tail) return { text, truncated: false };
  return { text: text.slice(0, head) + "\n…<中略>…\n" + text.slice(-tail), truncated: true };
};

const stages = ["typecheck", "unit", "e2e"];
const evidence = [];

for (const stage of stages) {
  let row = { stage, exit: 1, stdout: "", truncated: false };
  try {
    const r = await tools.bash({ command: `./scripts/${stage}.sh`, description: `运行 ${stage} 检查` });
    const c = clip(r?.stdout);
    row = { stage, exit: 0, stdout: c.text, truncated: c.truncated };
  } catch (e) {
    const c = clip(e?.message ?? e);
    row = { stage, exit: 1, stdout: c.text, truncated: c.truncated };
  }
  // 先落盘：这一段就算后面被超时杀掉，证据也留下来了
  await tools.write({ path: `state/evidence/${stage}.json`, content: JSON.stringify(row) + "\n" });
  evidence.push(row);
}

return { evidence, stages_done: stages.length };
```

### 7.2 程序内记录 `timedOut` 标记

```ts
// 程序 P11 —— run_code description: "带超时标记地跑一次长任务"
let timedOut = false;
try {
  await tools.bash({ command: "./scripts/long.sh", description: "运行长任务" });
} catch (e) {
  const msg = String(e?.message ?? e);
  if (msg.includes("timeout") || msg.includes("timed out")) timedOut = true;
  return { ok: false, timedOut, message: msg };
}
return { ok: true, timedOut };
```

**限定**：`timeoutMs` 杀进程时，程序内的 `catch` **可能根本不跑**（进程被直接终止），
所以 `timedOut` 变量只覆盖「catch 跑到了」的那部分情形。
真正的账本仍然是 `state/evidence/` 下**已经落盘的文件**——文件在，说明那段跑完了；
文件不在，说明那段没跑完，无论程序返回了什么。

### 7.3 大输出先落盘，再分段读

```ts
// 程序 P12 —— run_code description: "把大输出落盘并返回行数与尾部"
await tools.bash({
  command: "./scripts/dump.sh > state/raw.log 2>&1",
  description: "把原始输出写入文件",
});
const tail = await tools.bash({ command: "tail -n 40 state/raw.log", description: "读取输出尾部" });
const count = await tools.bash({ command: "wc -l < state/raw.log", description: "统计输出行数" });
return [
  { cmd: "tail -n 40 state/raw.log", exit: 0, stdout: String(tail?.stdout ?? ""), truncated: false },
  { cmd: "wc -l < state/raw.log", exit: 0, stdout: String(count?.stdout ?? "").trim(), truncated: false },
];
```

把 64 MiB 上限留给真正的取证（diff、日志），而不是留给一次 `cat` 大文件。
需要中段内容时按行区间分段读，每次返回带行号范围——**分段读不是省钱，是防截断**。

### 自检

- 长流程里，每一段的证据都落了盘，不依赖程序跑完。
- 返回值里 `timedOut` 与「已落盘证据」分开表达，不混成一句「完成」。
- 没有任何一次 `return` 把被截断的输出当作完整输出。

---

## 8. 自检清单

交付前逐条打勾，任何一条打不上就回去改程序，不要在报告里解释。

1. **一程序一意图**：`description` 是一句主动语态 5–10 词（中文 8–40 字），里面没有「并」「然后」「顺便」。
2. **状态在磁盘**：程序里没有 `let` 计数器被当作账本 `return`；跨程序的量都来自 `state/*.json` 或 `state/*.jsonl`。
3. **`_rev` 闭环**：每个写状态文件的地方都自增 `_rev`，写前重读、不一致就重算不覆盖，返回值带前后版本号。
4. **证据返回**：`return` 里有 `cmd` / `exit` / `stdout` / `truncated`，没有孤立的 `ok: true`。
5. **失败不吞**：每一处 `catch` 都 `throw` 或 `return` 带 `exit: 1` 的对象；没有空 `catch`。
6. **并发只读**：`Promise.all` 里全是只读调用，所有写调用串行 `await`。
7. **提问带超时**：含 `ask_user_question` 的程序显式写 `timeoutMs: 600000`，且只返回回答不动手。
8. **不可逆动作单独成程序**：一个程序最多一个不可逆动作，且它出现在 `description` 里，`程序内不可逆动作数` 为 1。
9. **分段提交**：长流程每段证据先落盘；`timedOut` 与已落盘证据分开表达。
10. **大输出落盘**：没有把可能超 `maxOutputBytes` 的输出直接 `return`，而是先写文件再分段读。

---

## 与另外三份文件的关系

- `ptc-hazards.md`：P1–P7 的权威定义。本文件的每个反例对应其中一条，编号一致。
- `evidence-map.md`：本文件每条硬规则背后的结构事实与实测数字。
- `consequence-gate.md`：程序边界就是门边界，`description` 与 `sandbox_permissions` 的填写规则在那里。
