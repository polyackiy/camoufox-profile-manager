import assert from 'node:assert/strict'
import test from 'node:test'
import { readProxyCheck, type ProxyCheckRecord } from '../src/lib/api'

const record: ProxyCheckRecord = { checked_at: '2026-01-01T00:00:00Z', reachable: false, error: 'The proxy rejected the credentials.', latency_ms: null, ip: null, country: null, timezone: null, findings: [] }

test('known proxy diagnostics use the UI language and unknown upstream text preserves fallback', () => {
  const chinese = readProxyCheck(record, 'zh-CN')
  assert.ok(chinese.detail.includes('代理拒绝了凭据。'))
  assert.equal(chinese.tone, 'danger')
  assert.ok(readProxyCheck({ ...record, error: 'Unknown upstream diagnostic' }, 'zh-CN').detail.includes('Unknown upstream diagnostic'))
  const invalid = readProxyCheck({ ...record, error: null, findings: [{ level: 'error', field: 'proxy', message: 'SOCKS5 username and password must each be 1–255 UTF-8 bytes.' }] }, 'zh-CN')
  assert.ok(invalid.detail.includes('SOCKS5 用户名和密码各需包含 1–255 个 UTF-8 字节。'))
})

import { proxyAuthenticationError } from '../src/lib/proxy-auth'

test('authentication policy counts UTF-8 bytes and permits an unchanged hidden password', () => {
  assert.equal(proxyAuthenticationError('socks5', '界'.repeat(85), 'p'), null)
  assert.ok(proxyAuthenticationError('socks5', '界'.repeat(86), 'p'))
  assert.ok(proxyAuthenticationError('socks5', 'u', '😀'.repeat(64)))
  assert.ok(proxyAuthenticationError('socks5', 'u', ''))
  assert.equal(proxyAuthenticationError('socks5', 'u', '', true), null)
  assert.equal(proxyAuthenticationError('socks5', '', ''), null)
  assert.equal(proxyAuthenticationError('socks5', ' user ', ' password '), null)
  assert.ok(proxyAuthenticationError('socks4', 'u', 'p'))
  assert.ok(proxyAuthenticationError('http', '', 'p'))
})
