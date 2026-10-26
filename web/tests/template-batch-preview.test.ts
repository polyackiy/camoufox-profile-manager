import assert from 'node:assert/strict'
import test from 'node:test'
import { batchProfileNames } from '../src/lib/template-batch-preview'

test('batch names preserve literal user text and expand only numbered placeholders', () => {
  assert.deepEqual(batchProfileNames(' 客户 {n} · {n} ', 3, 9, 3), ['客户 009 · 009', '客户 010 · 010', '客户 011 · 011'])
  assert.deepEqual(batchProfileNames('One profile', 1, 0, 1), ['One profile'])
  assert.deepEqual(batchProfileNames('$& {other} {n}', 1, 1, 2), ['$& {other} 01'])
})

test('invalid sizes, missing numbering and overlong or control-character names cannot produce a preview', () => {
  for (const count of [0, 26, 1.5, NaN]) assert.throws(() => batchProfileNames('Name {n}', count, 1, 3))
  assert.throws(() => batchProfileNames('Same', 2, 1, 3))
  assert.throws(() => batchProfileNames(' ', 1, 1, 3))
  assert.throws(() => batchProfileNames('a'.repeat(255) + '{n}', 1, 1, 3))
  assert.throws(() => batchProfileNames('hidden\n{n}', 1, 1, 3))
  assert.throws(() => batchProfileNames('{n}', 2, 999999, 3))
  assert.throws(() => batchProfileNames('{n}', 1, -1, 3))
  assert.throws(() => batchProfileNames('{n}', 1, 1, 7))
})
