import assert from 'node:assert/strict'
import test from 'node:test'
import { BatchQueue, MAX_BATCH_SIZE } from '../src/lib/batch-queue'

const targets = (count: number) => Array.from({ length: count }, (_, i) => ({ id: `p${i}`, name: `Profile ${i}` }))
const tick = () => new Promise<void>(resolve => setImmediate(resolve))
function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>(done => { resolve = done })
  return { promise, resolve }
}

test('preview does not execute; concurrency and target deduplication bound actual work', async () => {
  const pending = Array.from({ length: 5 }, deferred)
  const calls: string[] = []
  let active = 0
  let peak = 0
  const queue = new BatchQueue([...targets(5), ...targets(1)], {
    concurrency: 2,
    execute: async id => {
      calls.push(id)
      peak = Math.max(peak, ++active)
      await pending[Number(id.slice(1))].promise
      active--
    },
  })
  assert.deepEqual(calls, [])
  const run = queue.run()
  assert.deepEqual(calls, ['p0', 'p1'])
  await assert.rejects(queue.run(), /cannot start/)
  pending[0].resolve()
  await tick()
  assert.deepEqual(calls, ['p0', 'p1', 'p2'])
  for (const item of pending) item.resolve()
  await run
  assert.equal(peak, 2)
  assert.equal(calls.length, 5)
  assert.ok(queue.snapshot().rows.every(row => row.status === 'succeeded'))
})

test('cancel stops waiting work while accepted operations finish truthfully', async () => {
  const pending = deferred()
  const calls: string[] = []
  const queue = new BatchQueue(targets(3), {
    concurrency: 1,
    execute: async id => { calls.push(id); await pending.promise },
  })
  const run = queue.run()
  queue.cancelWaiting()
  assert.deepEqual(queue.snapshot().rows.map(row => row.status), ['running', 'cancelled', 'cancelled'])
  pending.resolve()
  await run
  assert.deepEqual(calls, ['p0'])
  assert.deepEqual(queue.snapshot().rows.map(row => row.status), ['succeeded', 'cancelled', 'cancelled'])
})

test('only explicit retry repeats failed or cancelled targets, never successful ones', async () => {
  const calls: string[] = []
  let firstFailure = true
  const queue = new BatchQueue(targets(3), {
    concurrency: 1,
    execute: async id => {
      calls.push(id)
      if (id === 'p0' && firstFailure) { firstFailure = false; throw new Error('private-password') }
      if (id === 'p1' && calls.length === 2) queue.cancelWaiting()
    },
  })
  await queue.run()
  assert.deepEqual(queue.snapshot().rows.map(row => row.status), ['failed', 'succeeded', 'cancelled'])
  assert.ok(!JSON.stringify(queue.snapshot()).includes('private-password'))
  await queue.run()
  assert.deepEqual(calls, ['p0', 'p1'])
  await queue.run(true)
  assert.deepEqual(calls, ['p0', 'p1', 'p0', 'p2'])
})

test('access loss cancels undispatched targets and prevents retry under stale authority', async () => {
  const calls: string[] = []
  const queue = new BatchQueue(targets(4), {
    concurrency: 1,
    execute: async id => { calls.push(id); throw new Error('denied') },
    accessDenied: () => true,
  })
  await queue.run()
  assert.deepEqual(calls, ['p0'])
  assert.equal(queue.snapshot().accessLost, true)
  assert.deepEqual(queue.snapshot().rows.map(row => row.status), ['failed', 'cancelled', 'cancelled', 'cancelled'])
  await assert.rejects(queue.run(true), /cannot start/)
})

test('unmount disposal stops dispatch and callbacks without aborting in-flight requests', async () => {
  const pending = deferred()
  const calls: string[] = []
  let updates = 0
  const queue = new BatchQueue(targets(3), {
    concurrency: 1,
    execute: async id => { calls.push(id); await pending.promise },
    onChange: () => { updates++ },
  })
  const run = queue.run()
  queue.dispose()
  const before = updates
  pending.resolve()
  await run
  assert.deepEqual(calls, ['p0'])
  assert.equal(updates, before)
  await assert.rejects(queue.run(true), /cannot start/)
})

test('snapshots cannot mutate queue targets; invalid limits are rejected before dispatch', async () => {
  for (const concurrency of [0, 5, 1.5, NaN]) {
    assert.throws(() => new BatchQueue(targets(1), { concurrency, execute: async () => {} }))
  }
  for (const count of [0, MAX_BATCH_SIZE + 1]) {
    assert.throws(() => new BatchQueue(targets(count), { concurrency: 1, execute: async () => {} }))
  }
  const calls: string[] = []
  const queue = new BatchQueue(targets(1), { concurrency: 1, execute: async id => { calls.push(id) } })
  queue.snapshot().rows[0].id = 'wrong'
  await queue.run()
  assert.deepEqual(calls, ['p0'])
})
