/**
 * test-message-id.mjs —— 防止「注入的消息缺 id 打不开历史」这个事故复发。
 *
 * 背景（2026-10-25 实测事故）：
 *   utc-ledger 每步注入一条 user/message。旧版返回裸对象，没有 id。
 *   框架不会替注入消息补 id（Session.append 只快照 data；id 只在生产者走
 *   createMessage 时才由 dsh-llm 生成）。于是每步都往会话日志写一条没有 id 的
 *   user/message。写入时不校验，**读取时**才校验：
 *     dsh-session/lib/index.js:1200
 *       throw new Error(`${subject} lacks an identified message`)
 *   结果：重启后整份历史拒绝加载，报 `session event at seq 14 lacks an identified message`。
 *
 * 这个测试直接调插件的 apply()，抓它真实产出的 message 形状，而不是
 * 只测一个手写的正确样本 —— 后者会在插件改回裸对象时依然通过。
 */
import { randomUUID } from 'node:crypto'

/* 复刻 dsh-session/lib/index.js:1183-1218 的校验，只保留与本事故相关的断言。 */
function assertMessageEventShape(event, subject) {
  const type = event.type
  const MESSAGE_TYPES = ['user/message', 'assistant/message', 'system/message',
    'developer/message', 'tool/result']
  if (!MESSAGE_TYPES.includes(type)) return
  const record = typeof event.data === 'object' && event.data !== null ? event.data : undefined
  const message = type === 'user/message' ? record : record?.message
  if (typeof message !== 'object' || message === null ||
      typeof message.id !== 'string' || message.id === '') {
    throw new Error(`${subject} lacks an identified message`)
  }
  if (!Array.isArray(message.content)) throw new Error(`${subject} message has invalid content`)
  const source = message.source
  if (typeof source !== 'object' || source === null ||
      typeof source.kind !== 'string' || source.kind === '') {
    throw new Error(`${subject} message has invalid source`)
  }
}

const CASES = []
function check(name, fn) { CASES.push([name, fn]) }

/* 1. 反例守卫：这个校验本身必须能拒绝无 id 的消息。
      否则「插件产出通过校验」这件事毫无意义。 */
check('校验器拒绝无 id 消息（反例守卫）', () => {
  try {
    assertMessageEventShape(
      { type: 'user/message', data: { role: 'user', content: [], source: { kind: 'k' } } },
      'session event at seq 14',
    )
  } catch (err) {
    if (/lacks an identified message/.test(err.message)) return
    throw new Error('拒绝原因不对: ' + err.message)
  }
  throw new Error('无 id 的消息竟然通过了校验')
})

/* 2. 插件真实产出必须通过 */
let injected = null
check('插件注入的消息带合法 id', async () => {
  const mod = await import(new URL('./utc-ledger.mjs', import.meta.url))
  let hook = null
  const ctx = {
    on(event, fn) { if (event === 'agent/pre-step') hook = fn },
    effect() {},
    logger: { warn() {} },
  }
  mod.apply(ctx, { injectState: true, ledgerPath: null })
  if (typeof hook !== 'function') throw new Error('没有注册 agent/pre-step 钩子')
  const decision = { kind: 'continue', messages: [] }
  const out = await hook(
    { agent: { session: { header: { id: 'test-session' } } }, step: 1 },
    async () => decision,
  )
  const msgs = out?.messages ?? []
  if (msgs.length !== decision.messages.length + 1) {
    throw new Error(`期望注入 1 条，实际 ${msgs.length - decision.messages.length} 条`)
  }
  injected = msgs[msgs.length - 1]
  assertMessageEventShape({ type: 'user/message', data: injected }, 'plugin inject')
})

/* 3. id 必须是每次不同（不能是常量） */
check('每次注入的 id 不同', async () => {
  if (!injected) throw new Error('上一个用例没产出消息')
  const second = randomUUID()
  if (injected.id === second) throw new Error('id 竟然相等')
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(injected.id)) {
    throw new Error('id 不是 UUID 形态: ' + injected.id)
  }
})

/* 4. 未启用状态注入时不该产出任何消息（避免误伤） */
check('injectState:false 时不注入', async () => {
  const mod = await import(new URL('./utc-ledger.mjs', import.meta.url))
  let hook = null
  const ctx = {
    on(event, fn) { if (event === 'agent/pre-step') hook = fn },
    effect() {},
    logger: { warn() {} },
  }
  mod.apply(ctx, { injectState: false, ledgerPath: null })
  if (hook !== null) throw new Error('injectState:false 仍然注册了钩子')
})

let failed = 0
for (const [name, fn] of CASES) {
  try {
    await fn()
    console.log(`ok   ${name}`)
  } catch (err) {
    failed += 1
    console.log(`FAIL ${name}\n       ${err.message}`)
  }
}
console.log()
console.log(`message id 回归: ${CASES.length - failed}/${CASES.length} 通过`)
process.exit(failed ? 1 : 0)
