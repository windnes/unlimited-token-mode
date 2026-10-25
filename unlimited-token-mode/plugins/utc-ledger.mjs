/**
 * utc-ledger —— 无限token模式 v2 的运行时证据账本 + 状态注入器。
 *
 * 为什么需要它（v1 的根因）：
 *   v1 把「我核对过的量」「真实输出」交给模型自己写进 markdown，lint 只能做字符串交集。
 *   于是「抄一遍字段 + 编一句我确认过了」就是合法通过。
 *   证据的提供者与校验者是同一个不可信来源 —— 这是结构缺陷，靠把规则写得更细治不好。
 *
 * 本插件做两件事：
 *   1) 旁听每一次 tool/call 与 tool/result，把真实的命令与真实的输出追加到 ledger.jsonl。
 *      模型无法写这个文件（它不知道该写什么、也不知道会被比对）。
 *      lint 拿到 --ledger 后，任何声称的"核对/输出"必须在账本里找得到对应调用，
 *      且声称输出必须是真实输出的连续子串 → 编造 = 找不到调用；改写 = 子串失配。
 *   2) 每步 pre-step 注入当前状态（已用调用数、不可逆动作计数、未过门的不可逆动作）。
 *      这是契约 2「状态外置」的运行时实现 —— v1 只在文档里要求模型自己外置。
 *
 * 装载（cordis preset 的 plugins 里）：
 *   - id: utc-ledger
 *     name: './utc-ledger.mjs'
 *     config: { ledgerPath: '.utc/ledger.jsonl', injectState: true }
 */

import fs from 'node:fs'
import path from 'node:path'

export const name = 'utc-ledger'
// 不声明 inject：本插件只旁听事件与注入消息，不消费任何服务。
// 原先写成 inject = ['tools'] 是个隐患——在只有 run_code 的 PTC 宿主里
// 没有 tools 服务时插件会一直 pending，账本压根不会生成，
// 于是所有 --ledger 校验都因"找不到调用"而失败。
export const inject = []

/**
 * 不导出 Config：本插件被加载时不在 DSH 的 node_modules 解析路径上，
 * import '@deepseek-ai/schemastery' 会 ERR_MODULE_NOT_FOUND；
 * 而导出裸对象又会被 cordis 当成 schema 调 .validate 而崩。
 * 因此配置在 apply 里手工归一化，非法值回落到默认值。
 */

const DEFAULT_IRREVERSIBLE = [
  'rm -rf', 'rm -r ', 'Remove-Item', 'rmdir /s', 'git push', 'git reset --hard',
  'git clean', 'DROP TABLE', 'DROP DATABASE', 'TRUNCATE', 'DELETE FROM',
  'ALTER TABLE', 'kubectl delete', 'docker rm', 'npm publish', 'pnpm publish',
  'systemctl stop', 'systemctl disable', 'shutdown', 'Stop-Process',
  'format ', 'diskpart', '> /dev/sd', 'Set-ItemProperty', 'reg add', 'reg delete',
]

/**
 * 从一段"可能是代码、也可能是散文"的文本里剥掉不该参与关键字匹配的部分。
 *
 * 纯 includes() 匹配会把「描述这个误报的散文」本身记成不可逆动作——
 * 契约书正文里写「绝不 git push」、报告表格里写「git push、rm -rf 等关键字」，
 * 都会被记成 irreversible:true，然后计数还滚雪球（每描述一次就多一条）。
 */
function stripNonExecutable(text) {
  const lines = String(text ?? '').split('\n')
  const kept = []
  let inBlockComment = false
  for (const raw of lines) {
    let line = raw
    // /* */ 块注释
    if (inBlockComment) {
      const end = line.indexOf('*/')
      if (end < 0) continue
      line = line.slice(end + 2)
      inBlockComment = false
    }
    line = line.replace(/\/\*[\s\S]*?\*\//g, ' ')
    const open = line.indexOf('/*')
    if (open >= 0) { inBlockComment = true; line = line.slice(0, open) }
    // 行注释
    line = line.replace(/(^|\s)\/\/.*$/, ' ').replace(/^\s*#.*$/, ' ')
    const t = line.trim()
    if (!t) continue
    // markdown 表格行：表格里写的是文档，不是命令
    if (t.startsWith('|')) continue
    // 中文散文行：含有成段 CJK 的行是叙述，不是命令行
    const cjk = (t.match(/[\u4e00-\u9fff]/g) || []).length
    if (cjk >= 6) continue
    kept.push(t)
  }
  return kept.join('\n')
}

/** 一行里得有执行痕迹，才算"这条命令会被执行"。 */
const EXEC_TRACE_RE = /(^|[\s;&|(])(?:await\s+)?(?:sudo\s+)?(?:bash|sh|zsh|pwsh|powershell|cmd|python3?|node|npm|pnpm|yarn|git|docker|kubectl|rm|mv|cp|del|rmdir|reg|systemctl|format|diskpart|Stop-Process|Remove-Item|Set-ItemProperty|Invoke-Expression)\b|\$\s|\bexec\b/

/**
 * 判断一次 tool call 是不是真的在做不可逆动作。
 *
 * 要求同时满足：关键字出现在**可执行文本**里（已剥注释 / 表格 / 散文），
 * 且**同一行**里有执行痕迹。只满足其一的多半是文档在谈论这个关键字。
 */
export function detectsIrreversible(command, target, patterns) {
  const text = stripNonExecutable(`${command}\n${target}`)
  if (!text) return false
  for (const line of text.split('\n')) {
    if (!EXEC_TRACE_RE.test(line)) continue
    for (const pat of patterns) {
      if (line.includes(pat)) return true
    }
  }
  return false
}

/** 单行化，避免把换行塞进 jsonl 以外的地方出错。 */
function clip(value, max) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '')
  const one = String(text).replace(/\r/g, '')
  return one.length > max ? one.slice(0, max) + `…[+${one.length - max}]` : one
}

/** 从 tool call 的参数里抽出"将要执行的命令"和"目标路径"。 */
function describeCall(toolName, args) {
  if (!args || typeof args !== 'object') return { command: '', target: '' }
  const command =
    args.command ?? args.script ?? args.code ?? args.cmd ?? args.query ??
    (typeof args === 'string' ? args : '')
  const target = args.file_path ?? args.path ?? args.pattern ?? args.filePath ?? args.cwd ?? ''
  return {
    command: command === undefined ? '' : clip(command, 4000),
    target: String(target ?? ''),
  }
}

/** 从 tool result 里抽纯文本输出与错误标志。 */
function describeResult(message) {
  if (!message) return { output: '', isError: false }
  const content = Array.isArray(message.content) ? message.content : []
  const parts = []
  for (const item of content) {
    if (!item) continue
    if (item.type === 'text' && typeof item.text === 'string') parts.push(item.text)
    else if (typeof item.text === 'string') parts.push(item.text)
    else if (item.type === 'image') parts.push('[image]')
  }
  return { output: clip(parts.join('\n'), 20000), isError: message.isError === true }
}

/** 从输出里嗅探退出码：多数工具会在末尾打印 `[exit code: N]`。 */
function sniffExit(output, isError) {
  if (!output) return isError ? 1 : null
  const m = /\[exit code:\s*(-?\d+)\]/.exec(output)
  if (m) return Number(m[1])
  if (/\bexit(?:ed)? (?:with )?(?:code )?(-?\d+)/i.test(output)) {
    return Number(/\bexit(?:ed)? (?:with )?(?:code )?(-?\d+)/i.exec(output)[1])
  }
  return isError ? 1 : null
}

export function apply(ctx, config = {}) {
  const ledgerPath = path.resolve(config.ledgerPath ?? path.join('.utc', 'ledger.jsonl'))
  const injectState = config.injectState !== false
  const stateIntervalSteps = Number(config.stateIntervalSteps ?? 1)
  const irreversible = [...DEFAULT_IRREVERSIBLE, ...(config.irreversiblePatterns ?? [])]

  /** callId -> 调用记录；只在内存里，用于把 call 与 result 配对。 */
  const pending = new Map()
  /** 每个 session 的累计计数（会话内进程内有效；权威数字仍以 ledger 文件为准）。 */
  const perSession = new Map()
  let seq = 0
  let fh = null

  function ensureDir() {
    const dir = path.dirname(ledgerPath)
    try { fs.mkdirSync(dir, { recursive: true }) } catch {}
  }

  function append(record) {
    ensureDir()
    if (fh === null) {
      try { fh = fs.createWriteStream(ledgerPath, { flags: 'a' }) } catch { fh = undefined }
    }
    const line = JSON.stringify(record) + '\n'
    try {
      if (fh && fh !== undefined) fh.write(line)
      else fs.appendFileSync(ledgerPath, line)
    } catch {
      /* 账本写不进去不应该弄死会话；lint 侧会因为缺记录而判失败，这是有意的偏向 */
    }
  }

  function touchSession(sessionId) {
    let s = perSession.get(sessionId)
    if (!s) {
      s = { calls: 0, irreversibleSeen: 0, irreversibleUngated: 0, startedAt: new Date().toISOString() }
      perSession.set(sessionId, s)
    }
    return s
  }

  ctx.on('session/event', (session, event) => {
    const sessionId = session?.header?.id ?? 'unknown'

    if (event.type === 'tool/call') {
      const toolName = event.data?.name ?? 'unknown'
      const rawArgs = event.data?.arguments
      let parsed = null
      if (typeof rawArgs === 'string') {
        try { parsed = JSON.parse(rawArgs) } catch { parsed = { command: rawArgs } }
      } else if (rawArgs && typeof rawArgs === 'object') {
        parsed = rawArgs
      }
      const { command, target } = describeCall(toolName, parsed)
      const callId = event.data?.callId ?? `anon-${++seq}`
      const s = touchSession(sessionId)
      s.calls += 1

      const isIrreversible = detectsIrreversible(command, target, irreversible)
      if (isIrreversible) {
        s.irreversibleSeen += 1
        s.irreversibleUngated += 1
      }

      pending.set(callId, { toolName, command, target, isIrreversible, sessionId })
      append({
        kind: 'call', t: new Date().toISOString(), seq: ++seq, session: sessionId,
        callId, tool: toolName, command, target, irreversible: isIrreversible,
      })
      return
    }

    if (event.type === 'tool/result') {
      const callId = event.data?.toolCallId ?? event.data?.message?.toolCallId ?? ''
      const meta = pending.get(callId) ?? { toolName: 'unknown', command: '', target: '', isIrreversible: false }
      pending.delete(callId)
      const message = event.data?.message
      const { output, isError } = describeResult(message)
      const exit = sniffExit(output, isError)
      append({
        kind: 'result', t: new Date().toISOString(), seq: ++seq, session: sessionId,
        callId, tool: meta.toolName, command: meta.command, target: meta.target,
        exit, error: isError, output,
      })
      return
    }

    /* 不可逆动作如果被"拒绝/失败"，视为没有真正落地，把未过门计数退回去 */
    if (event.type === 'tool/result') return
  })

  /* ---- 每步注入状态：契约 2 的运行时实现 ---- */
  if (injectState) {
    ctx.on('agent/pre-step', async (payload, next) => {
      const decision = await next()
      if (decision.kind === 'reject') return decision
      const { agent, step } = payload
      if (step > 1 && step % stateIntervalSteps !== 0) return decision
      const sessionId = agent?.session?.header?.id ?? 'unknown'
      const s = touchSession(sessionId)

      let real = { calls: 0, errors: 0 }
      try {
        if (fs.existsSync(ledgerPath)) {
          const text = fs.readFileSync(ledgerPath, 'utf8')
          for (const line of text.split('\n')) {
            if (!line.trim()) continue
            let obj
            try { obj = JSON.parse(line) } catch { continue }
            if (obj.kind !== 'result') continue
            if (obj.session !== sessionId) continue
            real.calls += 1
            if (obj.exit !== null && obj.exit !== 0) real.errors += 1
          }
        }
      } catch { /* 读不到就只用内存计数 */ }

      const lines = [
        '[无限token模式 v2 · 运行时状态 · 由 utc-ledger 注入，不是你写的]',
        `- 本会话已落账的工具调用: ${real.calls} 条（账本 ${ledgerPath}）`,
        `- 其中非零退出/报错: ${real.errors} 条`,
        `- 本会话已出现的不可逆动作: ${s.irreversibleSeen} 次`,
        '- 账本是 append-only 的，模型无法写。你说「我核对过了」时，lint 会去账本里找对应的调用与真实输出。',
        '- 说「做完了」之前，请在 run-report 的 §2 直接用账本里的真实输出，不要改写。',
      ]
      if (s.irreversibleSeen > 0) {
        lines.push(`- 注意: 已出现不可逆动作，请确认每个不可逆动作前都有决策块（unlimited_lint.py --kind decision）。`)
      }
      const text = lines.join('\n')
      return {
        ...decision,
        messages: [
          ...decision.messages,
          {
            role: 'user',
            content: [{ type: 'text', text }],
            source: { kind: name, form: 'snapshot', sections: [{ name, text }] },
          },
        ],
      }
    }, { prepend: true })
  }

  ctx.effect(() => () => {
    try { fh?.end?.() } catch {}
  })
}
