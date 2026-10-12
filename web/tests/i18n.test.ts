import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import ts from 'typescript'
import { resolveLocale, translate } from '../src/lib/i18n'
import zhCN from '../src/lib/locales/zh-CN.json'

const catalog: Record<string, string> = zhCN
const placeholders = (text: string) => [...new Set(text.match(/\{\w+\}/g) ?? [])].sort()

test('saved UI language wins without changing browser language preferences', () => {
  assert.equal(resolveLocale('en', ['zh-CN', 'en']), 'en')
  assert.equal(resolveLocale('zh-CN', ['en-US']), 'zh-CN')
  assert.equal(resolveLocale(null, ['zh-Hans-CN']), 'zh-CN')
  assert.equal(resolveLocale(null, ['en-GB', 'zh-CN']), 'en')
  assert.equal(resolveLocale('unsupported', ['fr', 'zh-CN']), 'zh-CN')
  assert.equal(resolveLocale(null, ['de']), 'en')
  assert.equal(resolveLocale(null, []), 'en')
})

test('English and unknown diagnostics fall back to their original text', () => {
  assert.equal(translate('en', 'New profile'), 'New profile')
  assert.equal(translate('zh-CN', 'New profile'), '新建配置')
  assert.equal(translate('zh-CN', 'An upstream diagnostic'), 'An upstream diagnostic')
  assert.equal(translate('zh-CN', 'constructor'), 'constructor')
})

test('interpolation preserves user text literally and replaces repeated values', () => {
  assert.equal(translate('zh-CN', 'Edit {name}', { name: '$& <name> {count}' }), '编辑 $& <name> {count}')
  assert.equal(translate('en', '{count} / {count}', { count: 0 }), '0 / 0')
  assert.equal(translate('zh-CN', 'Edit {name}'), '编辑 {name}')
})

test('every translation preserves the placeholders used by its English source', () => {
  for (const [source, translation] of Object.entries(catalog)) {
    assert.ok(translation.trim(), `Empty translation: ${source}`)
    assert.deepEqual(placeholders(translation), placeholders(source), source)
  }
})

test('every literal translation call has a Simplified Chinese entry', () => {
  const sourceRoot = join(process.cwd(), 'src')
  const files = readdirSync(sourceRoot, { recursive: true }) as string[]
  for (const file of files.filter(file => /\.tsx?$/.test(file))) {
    const source = ts.createSourceFile(file, readFileSync(join(sourceRoot, file), 'utf8'), ts.ScriptTarget.Latest, true)
    function visit(node: ts.Node) {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 't') {
        const key = node.arguments[0]
        if (key && ts.isStringLiteral(key)) assert.ok(Object.hasOwn(catalog, key.text), `${file}: ${key.text}`)
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
  }
})

test('visible JSX text uses translations except for product names and technical units', () => {
  const allowed = new Set(['Camoufox', 'English', 'Windows', 'macOS', 'Linux', 'HTTP', 'HTTPS', 'SOCKS4', 'SOCKS5', 'WebRTC', 'Canvas', 'ms', 'KB ·'])
  const sourceRoot = join(process.cwd(), 'src')
  const files = readdirSync(sourceRoot, { recursive: true }) as string[]
  for (const file of files.filter(file => file.endsWith('.tsx'))) {
    const source = ts.createSourceFile(file, readFileSync(join(sourceRoot, file), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    function visit(node: ts.Node) {
      if (ts.isJsxText(node)) {
        const text = node.text.replace(/\s+/g, ' ').trim()
        if (/[a-z]/i.test(text)) assert.ok(allowed.has(text), `${file}: untranslated text ${text}`)
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
  }
})
