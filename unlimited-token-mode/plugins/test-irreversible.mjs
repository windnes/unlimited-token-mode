#!/usr/bin/env node
/**
 * utc-ledger 的不可逆动作识别 —— 回归测试。
 *
 * v2 的洞：纯子串匹配扫整段 command，于是「描述这个误报的散文」本身
 * 也被记成 irreversible:true，而且计数会滚雪球（每描述一次就多一条）。
 * 这个文件锁住修正后的语义：关键字必须出现在**可执行文本**里，
 * 且**同一行**有执行痕迹。
 *
 * 用法：node plugins/test-irreversible.mjs   （退出码 0 = 全过）
 */
import { detectsIrreversible } from './utc-ledger.mjs'

const PATTERNS = [
  'rm -rf', 'rm -r ', 'Remove-Item', 'rmdir /s', 'git push', 'git reset --hard',
  'git clean', 'DROP TABLE', 'DELETE FROM', 'TRUNCATE', 'docker rm', 'npm publish',
]

/** [名称, command, 期望是否为不可逆] */
const CASES = [
  ['契约正文里的「绝不 git commit / push」', '| 写入边界 | 可写工作区与项目仓库，绝不 git commit / push | 只写工作区 |', false],
  ['报告表格里列举关键字', '| 风险 | 程序源码里出现 git push、rm -rf 等关键字 | 本会话 10 次全部是误报 |', false],
  ['关键字常量表', "const pats = ['rm -rf','rm -r ','git push','git reset --hard']", false],
  ['JS 行注释', '// 注意不要 rm -rf 目录\nconst x = 1', false],
  ['块注释', '/* 这里曾经 rm -rf 过一次 */\nconst y = 2', false],
  ['纯散文无执行痕迹', 'git push 这个动作会改远端，需要先过后果门', false],
  ['bash 真命令', 'rm -rf /tmp/build-cache', true],
  ['git 真命令', 'git push origin main', true],
  ['sudo 真命令', 'sudo rm -rf /var/lib/foo', true],
  ['管道串联真命令', 'cd x && rm -rf dist', true],
  ['中文行里的真命令', 'echo 测试 rm -rf 目录', true],
]

let failed = 0
for (const [name, cmd, want] of CASES) {
  const got = detectsIrreversible(cmd, '', PATTERNS)
  const ok = got === want
  if (!ok) failed += 1
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(36)} want=${want} got=${got}`)
}
console.log()
console.log(`不可逆识别: ${CASES.length - failed}/${CASES.length} 通过`)
process.exit(failed ? 1 : 0)
