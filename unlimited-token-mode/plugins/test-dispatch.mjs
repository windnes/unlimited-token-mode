#!/usr/bin/env node
/**
 * utc-ledger 内层 PTC 调用落账 —— 回归测试。
 *
 * v2 的洞：插件只监听 tool/call 与 tool/result，于是 PTC 里
 * \`await tools.ops_bash(...)\` 完全不落账，账本里只剩外层 run_code 记录，
 * 证据档永远停在 source-literal，program 档的 exit 比对永远撞 LEDGER-EXIT-MISMATCH。
 *
 * 这个文件用**合成事件流**驱动 apply()，锁住修正后的语义：
 *   1. ptc-dispatch-start + ptc-dispatch 必须落一条 result，tool 是内层真实工具名
 *   2. exit 必须从内容里的 [exit code: N] 解析出来，不能是 null
 *   3. scope 必须是 inner
 *   4. 内层接管不可逆判定：只有真实命令触发，程序源码里的散文不触发
 *   5. 外层 run_code 自身不再被判成不可逆
 *
 * 用法：node plugins/test-dispatch.mjs   （退出码 0 = 全过）
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { apply } from './utc-ledger.mjs'

let passed = 0
const failed = []

function check(name, ok, detail = '') {
  if (ok) { passed += 1; console.log('ok   ' + name) }
  else { failed.push(name); console.log('FAIL ' + name); if (detail) console.log('     ' + String(detail).slice(0, 600)) }
}

/** 造一个最小 ctx：收集监听器，其余按需打桩。 */
function makeCtx() {
  const handlers = new Map()
  return {
    ctx: {
      on(type, fn) { handlers.set(type, fn) },
      effect() {},
    },
    emit(type, session, event) {
      const fn = handlers.get(type)
      if (!fn) throw new Error('no handler for ' + type)
      return fn(session, event)
    },
    has(type) { return handlers.has(type) },
  }
}

function readLedger(p) {
  if (!fs.existsSync(p)) return []
  return fs.readFileSync(p, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
}

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'utc-dispatch-'))

try {
  // ---------- 用例 1：内层调用必须落账 ----------
  const led1 = path.join(work, 'l1.jsonl')
  const h1 = makeCtx()
  apply(h1.ctx, { ledgerPath: led1, injectState: false })
  check('订阅了 tool/ptc-dispatch-start', h1.has('session/event'))

  const session = { header: { id: 's1' } }
  h1.emit('session/event', session, {
    type: 'tool/ptc-dispatch-start',
    data: {
      rootCallId: 'r1', parentCallId: 'r1', subCallId: 'r1:ptc:1',
      name: 'ops_bash', arguments: { command: 'git status --porcelain', description: 'check' },
    },
  })
  h1.emit('session/event', session, {
    type: 'tool/ptc-dispatch',
    data: {
      rootCallId: 'r1', parentCallId: 'r1', subCallId: 'r1:ptc:1',
      name: 'ops_bash', arguments: { command: 'git status --porcelain', description: 'check' },
      isError: false,
      content: [{ type: 'text', text: 'M a.txt\n?? b.txt\n\n[exit code: 0]' }],
    },
  })

  const rows1 = readLedger(led1)
  const res1 = rows1.filter(r => r.kind === 'result')
  check('内层调用落了一条 result', res1.length === 1, JSON.stringify(rows1).slice(0, 400))
  check('tool 是内层真实工具名 ops_bash', res1[0]?.tool === 'ops_bash', JSON.stringify(res1[0]))
  check('command 是真实命令而非程序源码',
    res1[0]?.command === 'git status --porcelain', JSON.stringify(res1[0]?.command))
  check('scope 是 inner', res1[0]?.scope === 'inner', JSON.stringify(res1[0]))

  // ---------- 用例 2：exit 必须从内容解析 ----------
  check('exit 从 [exit code: 0] 解析为 0', res1[0]?.exit === 0, String(res1[0]?.exit))

  const led2 = path.join(work, 'l2.jsonl')
  const h2 = makeCtx()
  apply(h2.ctx, { ledgerPath: led2, injectState: false })
  h2.emit('session/event', session, {
    type: 'tool/ptc-dispatch-start',
    data: { rootCallId: 'r2', parentCallId: 'r2', subCallId: 'r2:ptc:1', name: 'ops_bash',
            arguments: { command: 'python nope.py' } },
  })
  h2.emit('session/event', session, {
    type: 'tool/ptc-dispatch',
    data: { rootCallId: 'r2', parentCallId: 'r2', subCallId: 'r2:ptc:1', name: 'ops_bash',
            arguments: { command: 'python nope.py' }, isError: false,
            content: [{ type: 'text', text: "can't open file\n[exit code: 2]" }] },
  })
  const res2 = readLedger(led2).filter(r => r.kind === 'result')
  check('非零 exit 被如实记录为 2', res2[0]?.exit === 2, String(res2[0]?.exit))

  // ---------- 用例 3：内层接管不可逆判定 ----------
  const led3 = path.join(work, 'l3.jsonl')
  const h3 = makeCtx()
  apply(h3.ctx, { ledgerPath: led3, injectState: false })
  h3.emit('session/event', session, {
    type: 'tool/ptc-dispatch-start',
    data: { rootCallId: 'r3', parentCallId: 'r3', subCallId: 'r3:ptc:1', name: 'ops_bash',
            arguments: { command: 'echo 这段程序绝不 git push 也不 rm -rf' } },
  })
  h3.emit('session/event', session, {
    type: 'tool/ptc-dispatch',
    data: { rootCallId: 'r3', parentCallId: 'r3', subCallId: 'r3:ptc:1', name: 'ops_bash',
            arguments: { command: 'echo 这段程序绝不 git push 也不 rm -rf' }, isError: false,
            content: [{ type: 'text', text: '这段程序绝不 git push 也不 rm -rf\n[exit code: 0]' }] },
  })
  const res3 = readLedger(led3).filter(r => r.kind === 'result')
  check('散文里的关键字不触发不可逆', res3[0]?.irreversible === false, JSON.stringify(res3[0]?.irreversible))

  const led4 = path.join(work, 'l4.jsonl')
  const h4 = makeCtx()
  apply(h4.ctx, { ledgerPath: led4, injectState: false })
  h4.emit('session/event', session, {
    type: 'tool/ptc-dispatch-start',
    data: { rootCallId: 'r4', parentCallId: 'r4', subCallId: 'r4:ptc:1', name: 'ops_bash',
            arguments: { command: 'git push origin main' } },
  })
  h4.emit('session/event', session, {
    type: 'tool/ptc-dispatch',
    data: { rootCallId: 'r4', parentCallId: 'r4', subCallId: 'r4:ptc:1', name: 'ops_bash',
            arguments: { command: 'git push origin main' }, isError: false,
            content: [{ type: 'text', text: 'a12a644..c882a0c  main -> main\n[exit code: 0]' }] },
  })
  const res4 = readLedger(led4).filter(r => r.kind === 'result')
  check('真实 git push 触发不可逆', res4[0]?.irreversible === true, JSON.stringify(res4[0]?.irreversible))

  // ---------- 用例 4：外层 run_code 自身不再判不可逆 ----------
  const led5 = path.join(work, 'l5.jsonl')
  const h5 = makeCtx()
  apply(h5.ctx, { ledgerPath: led5, injectState: false })
  h5.emit('session/event', session, {
    type: 'tool/call',
    data: { callId: 'c9', name: 'run_code',
            arguments: { code: '// 契约书正文：绝不 git push，也不 rm -rf\nreturn 1' } },
  })
  const call5 = readLedger(led5).filter(r => r.kind === 'call')
  check('外层 run_code 不因源码散文被判不可逆', call5[0]?.irreversible === false,
    JSON.stringify(call5[0]?.irreversible))

  // ---------- 用例 5：配对缺失时仍能落账（不吞事件） ----------
  const led6 = path.join(work, 'l6.jsonl')
  const h6 = makeCtx()
  apply(h6.ctx, { ledgerPath: led6, injectState: false })
  h6.emit('session/event', session, {
    type: 'tool/ptc-dispatch',
    data: { rootCallId: 'r6', parentCallId: 'r6', subCallId: 'r6:ptc:9', name: 'read',
            arguments: { file_path: '/x/y.md' }, isError: false,
            content: [{ type: 'text', text: 'line1\nline2' }] },
  })
  const res6 = readLedger(led6).filter(r => r.kind === 'result')
  check('只有 result 没有 start 时也落账', res6.length === 1 && res6[0].tool === 'read',
    JSON.stringify(res6))
} finally {
  fs.rmSync(work, { recursive: true, force: true })
}

console.log()
console.log('内层落账: ' + passed + '/' + (passed + failed.length) + ' 通过')
if (failed.length) {
  console.log('失败项:')
  for (const n of failed) console.log('  - ' + n)
  process.exit(1)
}
