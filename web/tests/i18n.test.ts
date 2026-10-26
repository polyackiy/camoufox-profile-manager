import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import ts from 'typescript'
import { catalogs, resolveLocale, translate } from '../src/lib/i18n'

const placeholders = (text: string) => [...new Set(text.match(/\{\w+\}/g) ?? [])].sort()

test('saved UI language wins without changing browser language preferences', () => {
  assert.equal(resolveLocale('en', ['zh-CN', 'en']), 'en')
  assert.equal(resolveLocale('zh-CN', ['en-US']), 'zh-CN')
  assert.equal(resolveLocale(null, ['zh-Hans-CN']), 'zh-CN')
  assert.equal(resolveLocale(null, ['en-GB', 'zh-CN']), 'en')
  assert.equal(resolveLocale('unsupported', ['fr', 'zh-CN']), 'zh-CN')
  assert.equal(resolveLocale(null, ['de']), 'en')
  assert.equal(resolveLocale(null, []), 'en')
  assert.equal(resolveLocale('ru', ['zh-CN']), 'ru')
  assert.equal(resolveLocale(null, ['ru-RU', 'en']), 'ru')
  assert.equal(resolveLocale(null, ['en-GB', 'ru']), 'en')
  assert.equal(resolveLocale('unsupported', ['fr', 'ru-KZ']), 'ru')
})

test('English and unknown diagnostics fall back to their original text', () => {
  assert.equal(translate('en', 'New profile'), 'New profile')
  assert.equal(translate('zh-CN', 'New profile'), '新建配置')
  assert.equal(translate('zh-CN', 'An upstream diagnostic'), 'An upstream diagnostic')
  assert.equal(translate('zh-CN', 'constructor'), 'constructor')
  assert.equal(translate('ru', 'New profile'), 'Новый профиль')
  assert.equal(translate('ru', 'An upstream diagnostic'), 'An upstream diagnostic')
  assert.equal(translate('ru', '__proto__'), '__proto__')
})

test('interpolation preserves user text literally and replaces repeated values', () => {
  assert.equal(translate('zh-CN', 'Edit {name}', { name: '$& <name> {count}' }), '编辑 $& <name> {count}')
  assert.equal(translate('en', '{count} / {count}', { count: 0 }), '0 / 0')
  assert.equal(translate('zh-CN', 'Edit {name}'), '编辑 {name}')
  assert.equal(translate('ru', 'Edit {name}', { name: '$& <name> {count}' }), 'Изменить $& <name> {count}')
})

test('Russian counts use grammatical forms including teens, zero and fractions', () => {
  for (const [count, suffix] of [[1, 'профиль'], [2, 'профиля'], [5, 'профилей'], [0, 'профилей'], [11, 'профилей'], [14, 'профилей'], [21, 'профиль'], [22, 'профиля'], [101, 'профиль'], [1.5, 'профиля']] as const) {
    assert.equal(translate('ru', 'Create {count} profiles', { count }), `Создать ${count} ${suffix}`)
  }
  assert.equal(translate('ru', 'Create {count} profiles'), 'Создать {count} профиля')
})

for (const [locale, catalog] of Object.entries(catalogs)) {
  test(`${locale}: coverage, nonempty values and placeholders match the source`, () => {
    assert.deepEqual(Object.keys(catalog).sort(), Object.keys(catalogs['zh-CN']).sort())
    for (const [source, entry] of Object.entries(catalog)) {
      if (typeof entry !== 'string' && locale === 'ru') {
        assert.deepEqual(Object.keys(entry).sort(), ['few', 'many', 'one', 'other'])
        assert.ok(source.includes('{count}'), `Plural without count: ${source}`)
      }
      for (const translation of typeof entry === 'string' ? [entry] : Object.values(entry)) {
        assert.ok(translation?.trim(), `Empty ${locale} translation: ${source}`)
        assert.deepEqual(placeholders(translation), placeholders(source), `${locale}: ${source}`)
      }
    }
  })

  test(`${locale}: JSON objects contain no silently overwritten duplicate keys`, () => {
    const path = join(process.cwd(), 'src', 'lib', 'locales', `${locale}.json`)
    const source = ts.parseJsonText(path, readFileSync(path, 'utf8'))
    function visit(node: ts.Node) {
      if (ts.isObjectLiteralExpression(node)) {
        const keys = new Set<string>()
        for (const property of node.properties) {
          assert.ok(ts.isPropertyAssignment(property) && ts.isStringLiteral(property.name))
          const key = property.name.text
          assert.ok(!keys.has(key), `Duplicate ${locale} key: ${key}`)
          keys.add(key)
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
  })
}

test('every literal translation call has entries in every supported catalog', () => {
  const sourceRoot = join(process.cwd(), 'src')
  const files = readdirSync(sourceRoot, { recursive: true }) as string[]
  for (const file of files.filter(file => /\.tsx?$/.test(file))) {
    const source = ts.createSourceFile(file, readFileSync(join(sourceRoot, file), 'utf8'), ts.ScriptTarget.Latest, true)
    function visit(node: ts.Node) {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 't') {
        const key = node.arguments[0]
        if (key && ts.isStringLiteralLike(key)) {
          for (const [locale, catalog] of Object.entries(catalogs)) assert.ok(Object.hasOwn(catalog, key.text), `${locale} ${file}: ${key.text}`)
        }
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
