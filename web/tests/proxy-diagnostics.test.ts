import assert from 'node:assert/strict'
import test from 'node:test'
import { readProxyCheck, type ProxyCheckRecord } from '../src/lib/api'

const record: ProxyCheckRecord = { checked_at: '2026-01-01T00:00:00Z', reachable: false, error: 'The proxy rejected the credentials.', latency_ms: null, ip: null, country: null, timezone: null, findings: [] }

test('known proxy diagnostics use the UI language and unknown upstream text preserves fallback', () => {
  const chinese = readProxyCheck(record, 'zh-CN')
  assert.ok(chinese.detail.includes('代理拒绝了凭据。'))
  assert.ok(readProxyCheck(record, 'ru').detail.includes('Прокси отклонил учётные данные.'))
  assert.equal(chinese.tone, 'danger')
  assert.ok(readProxyCheck({ ...record, error: 'Unknown upstream diagnostic' }, 'zh-CN').detail.includes('Unknown upstream diagnostic'))
  const invalid = readProxyCheck({ ...record, error: null, findings: [{ level: 'error', field: 'proxy', message: 'SOCKS5 username and password must each be 1–255 UTF-8 bytes.' }] }, 'zh-CN')
  assert.ok(invalid.detail.includes('SOCKS5 用户名和密码各需包含 1–255 个 UTF-8 字节。'))
})

test('location-resource advice stays visible without reporting a reachable proxy as broken', () => {
  const info = { ...record, reachable: true, error: null, findings: [{ level: 'info' as const, field: 'proxy', message: "The proxy works, but its address could not be placed on the map. Run 'camoufox fetch' to install the location database." }] }
  for (const [locale, action] of [['en', 'Settings → Browser setup'], ['ru', 'Настройки → Установка браузера'], ['zh-CN', '设置 → 浏览器安装']] as const) {
    const result = readProxyCheck(info, locale)
    assert.equal(result.tone, 'ok')
    assert.ok(result.detail.includes(action))
    assert.ok(!result.detail.includes('camoufox fetch'))
  }
  assert.equal(readProxyCheck({ ...info, findings: [{ level: 'warning', field: 'timezone', message: 'Unknown advisory' }] }, 'ru').tone, 'warn')
  assert.equal(readProxyCheck({ ...info, findings: [{ level: 'error', field: 'proxy', message: 'Unknown error' }] }, 'ru').tone, 'danger')
  assert.ok(readProxyCheck({ ...info, findings: [{ level: 'info', field: 'proxy', message: '<unknown upstream text>' }] }, 'ru').detail.includes('<unknown upstream text>'))
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
