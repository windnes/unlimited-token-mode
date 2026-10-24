# 运行时模式：把契约 2、4、6 落到工具层

本文件解决一件事：**不要把契约写成提示词里的"请记得"**。契约 2（状态外置）、4（核对免费）、6（预算不设限）只有在运行时被**结构性强制**时才可靠。下面每个模式都给出可直接复制的代码。

---

## 1. 循环外置

### 问题

让模型"在脑子里循环 10 次、每次累加一个计数"是可靠的失败点。实测（`budget-trace` / `budget-ledger` / vector-db 三个基准）：

- 基线跑里主动查预算状态的次数：**0 次**（`budget-trace` 6/6、`budget-ledger` 3/3、vector-db 0/0/0）。
- **5/6** 局恰好用满第 **40** 次调用就提交——零余量收尾，说明账本是"刚好够用"的估摸，不是算出来的。
- 长会话里出现可对照的漂移原话：已用 42/剩 8 时写「剩余调用 12」；已用 46/剩 4 时写「还有约 9 次」；旧版已用 49/剩 1 时写「还有约 13 次」。
- 校准：所谓"按轮次而非调用计数"的错误在 **6–9 轮的短会话里没有复现**（估计误差 0），漂移只在 **40+ 轮**的长会话出现。

**在思考里维护计数器 = 用最不可靠的存储保存最需要可靠的数据。**

### 规则

> 任何"重复 N 次"的意图，必须变成**磁盘上的一行一行**，由 bash / node / python 驱动，不由模型驱动。

模型的职责降级为：写驱动脚本 → 读聚合结果 → 解释。模型不参与计数。

### 可运行 bash 示例

```bash
#!/usr/bin/env bash
# loop.sh —— 跑 10 次，每次一行 JSON 落盘，最后打印聚合
set -uo pipefail
STATE="state"; mkdir -p "$STATE"
LOG="$STATE/iter.jsonl"; : > "$LOG"          # 清空，避免上一轮的残留

for i in $(seq 1 10); do
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # 把真正要做的事放在这里；下面用 curl 举例，失败不中断循环
  if out=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "https://example.com/ping?i=$i" 2>&1); then
    ok=true; code="$out"; err=""
  else
    ok=false; code=""; err="${out//\"/\'}"
  fi
  printf '{"i":%d,"ts":"%s","ok":%s,"code":"%s","err":"%s"}\n' \
    "$i" "$ts" "$ok" "$code" "$err" >> "$LOG"
done

# 聚合：只用 awk 数文件，不靠记忆
awk -F'"' '
  /"ok":true/  {pass++}
  /"ok":false/ {fail++}
  END {printf "{\"total\":%d,\"pass\":%d,\"fail\":%d}\n", pass+fail, pass+0, fail+0}
' "$LOG" > "$STATE/aggregate.json"
cat "$STATE/aggregate.json"
```

要点：
- `set -uo pipefail` 但**不加 `-e`**：单次失败要记录，不该终止整轮。
- 每轮一行 JSON（`.jsonl`）——追加写，崩溃也能留下已完成的证据。
- 聚合**从文件重新算**，绝不复用循环内变量。这样聚合与明细天然一致。

### 可运行 node 示例

```js
// loop.mjs —— node loop.mjs
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";

const STATE = "state";
mkdirSync(STATE, { recursive: true });
const logPath = `${STATE}/iter.jsonl`;
writeFileSync(logPath, "");                      // 清空

const rows = [];
for (let i = 1; i <= 10; i++) {
  const row = { i, ts: new Date().toISOString(), ok: false, code: "", err: "" };
  try {
    const r = await fetch(`https://example.com/ping?i=${i}`, {
      signal: AbortSignal.timeout(5000),
    });
    row.ok = r.ok; row.code = String(r.status);
  } catch (e) {
    row.err = String(e?.message ?? e).replace(/"/g, "'");
  }
  rows.push(row);
  appendFileSync(logPath, JSON.stringify(row) + "\n");   // 立即落盘
}

// 聚合从 rows（= 已落盘内容的镜像）重算
const agg = { total: rows.length, pass: rows.filter(r => r.ok).length };
agg.fail = agg.total - agg.pass;
writeFileSync(`${STATE}/aggregate.json`, JSON.stringify(agg) + "\n");
console.log(JSON.stringify(agg));
```

### 自检

- 循环里**没有**任何"我数到第几个了"的自然语言。
- 断点续跑只需读 `iter.jsonl` 的最后一行的 `i`。
- 若必须重试，重试逻辑写在脚本的 `while` 里，不写在思考里。

---

## 2. 状态文件

### 约定

```
state/
  run.json          # 单值状态：阶段、预算用量、_rev、updated_at
  events.jsonl      # 追加型事件日志，只增不改
  aggregate.json    # 由脚本生成，人/模型只读
```

`state/run.json` 最小形状：

```json
{
  "_rev": 7,
  "updated_at": "2026-10-24T14:22:05Z",
  "phase": "verify",
  "checks_spent": 3,
  "checks_limit": 20,
  "open_items": ["case-4"]
}
```

### 规则

> **每一步的开头重新读文件；永远不从记忆推导状态。**

`_rev` 是并发/陈旧检测器：你手里的是 `_rev=7`，磁盘上是 `_rev=9`，说明有人（或另一个 agent、或你自己的上一次工具调用）已经改过 —— **重读，不要覆盖**。

### 读-改-写配方

```bash
# 1) 读
cur=$(cat state/run.json)
rev=$(printf '%s' "$cur" | python -c 'import json,sys;print(json.load(sys.stdin)["_rev"])')

# 2) 改（只在本地变量里改，不做跨步骤保留）
new=$(printf '%s' "$cur" | python -c '
import json,sys,datetime
d=json.load(sys.stdin)
d["checks_spent"]=d.get("checks_spent",0)+1
d["_rev"]=d["_rev"]+1
d["updated_at"]=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps(d,ensure_ascii=False))')

# 3) 写（原子替换，避免半截文件）
printf '%s\n' "$new" > state/run.json.tmp && mv state/run.json.tmp state/run.json
```

**陈旧 `_rev` 的处理**：写入前再读一次，`rev_on_disk != rev_i_read` → 丢弃本次计算结果，回到第 1 步。不要"合并"——合并需要语义，而你此刻只有旧快照。

### 事件日志

不可变的动作写 `events.jsonl`，一行一个：

```json
{"ts":"2026-10-24T14:20:11Z","event":"check","tool":"dry-run","target":"deploy.yaml","result":"ok"}
{"ts":"2026-10-24T14:21:40Z","event":"gate","rule":"R-03","decision":"don't-act","why":"二阶成本 > 收益"}
```

日志用于事后归因（契约 7 的"可证伪收工"要引用真实输出）。**日志不做状态推导**，推导一律看 `run.json` / `aggregate.json`。

---

## 3. 环境注入

### 反模式

> "开始前请先查一次状态，然后记住它。"

这句话等于让模型**用上下文当数据库**。上下文会被压缩、会被摘要、会被它自己的措辞污染。写进提示词的"先查一次"，第 3 轮就退化成"我以为我查过了"。

### 正解 A：driver 前置状态块

由外层程序在**每一轮**模型调用前注入状态，模型没有"选择是否读"的自由。

```python
# driver.py —— 伪代码
state_path = "state/run.json"

def collect() -> str:
    run = json.loads(read(state_path))
    agg = json.loads(read("state/aggregate.json")) if exists("state/aggregate.json") else {}
    return f"_rev={run['_rev']} phase={run['phase']} " \
           f"checks={run['checks_spent']}/{run['checks_limit']} " \
           f"agg={agg} open={run['open_items']}"

turn = 0
while not done(state_path):
    turn += 1
    state = collect()                      # 每轮现取，绝不缓存
    msg = f"[STATE t={turn} @ {now_iso()}]\n{state}\n\n{task}"
    reply = model(msg)
    apply_tool_calls(reply)                # 工具调用由 driver 执行并落盘
```

模型侧看到的每一轮都以 `[STATE ...]` 开头，因此"我当前在第几步、花了多少核对预算"是**输入的一部分**，不是需要它记住的东西。

### 正解 B：pre-step hook

框架支持钩子时，在 `pre_model_turn` 里追加一条 user 消息：

```python
def pre_model_turn(ctx):
    ctx.messages.append({
        "role": "user",
        "content": f"[STATE t={ctx.turn}]\n{collect()}\n（这是权威状态；与你的记忆冲突时以此为准）"
    })
```

### 兜底：改不了 harness

无法修改运行时（第三方 agent、只读环境）时，退化为**结构性首调用**：

> 每一轮迭代的**第一个强制工具调用**必须是状态读取；该次读取的结果**只对该轮有效**。

写进 brief 的措辞不是"先查一次状态"，而是：

```
每轮第一条工具调用固定为: cat state/run.json
该次输出是该轮唯一权威状态；不得复用上一轮读数。
```

配一个可机械检查的判据：轨迹里每个 assistant turn 的第一个 tool_call 的 name+args 是否匹配 `read state/run.json`。不匹配即违规 —— 这比"记得查状态"可验证得多。

---

## 4. 配额与核对计价

### 问题

核对工具（dry-run、`--check`、preview、schema validate）有时带配额或计费。带配额的东西会诱发"省着用"，而省的时机恰好是**最不该省**的时机（不可逆动作前）。

### 规则

> 配额用量记在**状态文件**里，不记在思考里；配额优先花在**不可逆决策点**上。

```bash
# 每次核对后立即记账（与 §2 的读-改-写同一配方）
python - <<'PY'
import json, datetime, pathlib
p = pathlib.Path("state/run.json"); d = json.loads(p.read_text(encoding="utf-8"))
d["checks_spent"] = d.get("checks_spent", 0) + 1
d["_rev"] += 1
d["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
```

配额告警的正确反应是**把剩余额度重新排序**，不是"接下来都不查了"。

### 计价策略表

| 动作类别 | 例子 | 是否必须核对 | 说明 |
| --- | --- | --- | --- |
| **不可逆** | 删库、强推、发版、转账、覆盖唯一副本、对外发送 | **必须**，且只查一次就够 | 一次 `--dry-run` 的成本 ≪ 一次误删 |
| 可逆且便宜 | 建临时文件、改草稿、跑只读测试 | 随便查，查多少次都不算浪费 | 有配额时优先给上一行让路 |
| 纯读取 | `cat`、`ls`、`grep`、读日志 | 不需要"核对" | 读取本身就是观察，不是动作 |

推论：**"这个工具要花配额，所以先不查了"是禁止的推理**。正确推理是"这个动作不可逆，配额必须花在这里"。

---

## 5. 反抠门

### 禁止出现在提示词里的短语

以下措辞会让模型进入**节能模式**：缩短推理、跳过验证、用"应该没问题"替代检查、把不确定当成确定来收工。

- "尽量节省"
- "不要浪费 token"
- "步数有限" / "只剩下 N 步了"
- "只用最少的工具调用"
- "能一次做完就别分两步"
- "别做多余的事"
- "注意成本" / "控制开销"
- "简单看一下就行"
- "快速验证一下"

原因：这些是**没有优先级的全局压缩信号**。模型无法知道"节省"与"验证"谁优先，于是压缩最先被砍掉的、也是最贵的部分 —— 核对与推演。

**证据分层（引用时别搞混）**：

| 说法 | 状态 | 数字 |
|---|---|---|
| 核对一旦要钱就少做 | **已证实** | 盲盒 `v7-paid-staff`：问一次 1 元，去问的局数 **8/8 → 5/8**，中位数 **2 → 1** |
| 会囤积"每周期刷新"的额度 | **已证伪** | 额度利用率 **0.98–1.00**，没有空转周期；「省试算额度」被仓库自己判为**不是主因** |
| 会"省 token / 少调用工具" | **作者观察，未做对照实验** | 报告里没有直接指标 |

所以本节禁的是**制造稀缺感**，不是"禁止设限"。差别在于：稀缺感让它**用减少核对来省**；明确的数字和代价让它**把核对花在刀刃上**。

仓库对机制的描述值得照抄：它「学会了『预算稀缺时要节省』，但**没有先判断预算是否稀缺**」；
这是「通用的预算节约启发式，**缺少对具体环境的校准**」。
同一个偏好在别处是对的——vector-db 基准里预算是全局 50 次、不刷新，节省**正确**，且是它胜过旧版的主因。

### 如果真的存在硬上限

不要含糊其辞，写清三件事：

1. **确切数字**：`最多 12 次工具调用`、`单次输出 ≤ 8000 token`。
2. **花费优先级**（从高到低）：
   1. 不可逆动作前的核对
   2. 完成判据对应的验证
   3. 状态读取
   4. 探索与试错
   5. 措辞打磨
3. **触顶后的行为**：停止并在报告里写"因 X 上限未完成 Y"，**不得**把未验证项写成已验证。

### 正确措辞对照

| 禁止 | 替代 |
| --- | --- |
| 尽量节省 token | 完成判据全部验证通过前不设预算上限；触顶时按优先级 1→5 花费 |
| 只用最少的工具调用 | 每个不可逆动作前恰好核对一次；可逆动作的核对不计入浪费 |
| 简单看一下就行 | 用 `--dry-run` 跑一次并把真实输出贴进报告 |
| 步数有限，快收工 | 剩余预算 = N 次调用；未验证项必须在报告中逐条列出 |
