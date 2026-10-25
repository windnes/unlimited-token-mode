#!/usr/bin/env node
/**
 * 用**真实录制的会话事件**回放验证内层落账。
 *
 * test-dispatch.mjs 用的是合成事件；这个用当前会话日志里真实的
 * tool/ptc-dispatch-start / tool/ptc-dispatch 载荷驱动同一个 apply()，
 * 检查落账结果是否与真实执行一致（工具名、命令、退出码）。
 *
 * 用法：node plugins/replay-dispatch.mjs <session.v4.jsonl.zstd> [sample]
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import zlib from 'node:zlib'
import { apply } from './utc-ledger.mjs'

function frameEnd(b, off) {
  let p = off + 4
  const fhd = b[p]; p += 1
  const fcsFlag = fhd >> 6, single = (fhd >> 5) & 1, checksum = (fhd >> 2) & 1, dictFlag = fhd & 3
  if (!single) p += 1
  p += [0, 1, 2, 4][dictFlag]
  p += [single ? 1 : 0, 2, 4, 8][fcsFlag]
  for (;;) {
    const bh = b[p] | (b[p + 1] << 8) | (b[p + 2] << 16); p += 3
    const last = bh & 1, type = (bh >> 1) & 3, size = bh >>> 3
    p += type === 1 ? 1 : size
    if (last) break
  }
  if (checksum) p += 4
  return p
}

function decodeSession(file) {
  const buf = fs.readFileSync(file)
  const parts = []
  let off = 0
  while (off < buf.length) {
    const e = frameEnd(buf, off)
    parts.push(zlib.zstdDecompressSync(buf.subarray(off, e)))
    off = e
  }
  return Buffer.concat(parts).toString('utf8')
    .split('\n').filter(l => l.trim().startsWith('{'))
    .map(l => { try { return JSON.parse(l) } catch { return null } })
    .filter(Boolean)
}

const file = process.argv[2]
const sample = Number(process.argv[3] ?? 120)
if (!file) { console.error('usage: node replay-dispatch.mjs <session.zstd> [sample]'); process.exit(2) }

const recs = decodeSession(file)
const events = recs.filter(r => r.type === 'tool/ptc-dispatch-start' || r.type === 'tool/ptc-dispatch')
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'utc-replay-'))
const ledger = path.join(work, 'ledger.jsonl')

const handlers = new Map()
apply({
  on(type, fn) { handlers.set(type, fn) },
  effect() {},
}, { ledgerPath: ledger, injectState: false })

const session = { header: { id: 'replay' } }
const emit = (ev) => handlers.get('session/event')(session, { type: ev.type, data: ev.data })
for (const ev of events) emit(ev)

const rows = fs.readFileSync(ledger, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
fs.rmSync(work, { recursive: true, force: true })

const results = rows.filter(r => r.kind === 'result')
const inner = results.filter(r => r.scope === 'inner')
const withExit = inner.filter(r => typeof r.exit === 'number')

// 与录制的原始事件逐条比对：工具名与退出码必须一致
const truth = new Map()
for (const ev of events) {
  if (ev.type !== 'tool/ptc-dispatch') continue
  const text = (ev.data.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n')
  const m = /\[exit code:\s*(-?\d+)\]/.exec(text)
  truth.set(String(ev.data.subCallId), {
    name: ev.data.name,
    exit: m ? Number(m[1]) : null,
    cmd: ev.data.arguments && (ev.data.arguments.command ?? ev.data.arguments.file_path ?? '') || '',
  })
}

let mismatch = 0
let checked = 0
for (const r of inner.slice(0, sample)) {
  const t = truth.get(String(r.callId))
  if (!t) continue
  checked += 1
  if (t.name !== r.tool) mismatch += 1
  if (t.exit !== null && t.exit !== r.exit) mismatch += 1
}

console.log('录制事件数: ' + events.length)
console.log('落账 result 数: ' + results.length)
console.log('其中 scope=inner: ' + inner.length)
console.log('exit 为数字的: ' + withExit.length)
console.log('逐条比对: ' + checked + ' 条, 不一致 ' + mismatch + ' 处')

let bad = 0
if (results.length !== events.filter(e => e.type === 'tool/ptc-dispatch').length) { console.log('FAIL 落账条数与录制条数不符'); bad++ }
if (inner.length !== results.length) { console.log('FAIL 存在非 inner 记录'); bad++ }
if (withExit.length === 0) { console.log('FAIL 没有任何 exit 被解析出来'); bad++ }
if (mismatch > 0) { console.log('FAIL 工具名或退出码与录制不一致'); bad++ }

console.log()
if (bad) { console.log('回放: 失败'); process.exit(1) }
console.log('回放: 通过（' + inner.length + ' 条内层调用全部按真实执行落账）')
