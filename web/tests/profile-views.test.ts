import assert from 'node:assert/strict'
import test from 'node:test'
import { DEFAULT_VIEW, normalizeTag, profileViewQuery, readProfileView } from '../src/lib/profile-views'

test('tag links use the same Unicode spelling as stored labels', () => {
  assert.equal(normalizeTag(' Straße '), 'strasse')
  assert.equal(normalizeTag(' CAFE\u0301 '), 'café')
  assert.equal(normalizeTag('Client   A'), 'client a')
  assert.equal(normalizeTag('ﬀ'), 'ff')
  assert.equal(normalizeTag('և'), 'եւ')
  assert.equal(normalizeTag('Σς'), 'σσ')
  assert.deepEqual(readProfileView(new URLSearchParams('tags=Stra%C3%9Fe&tag=Client%20%20A')).config.tags, ['strasse', 'client a'])
  assert.deepEqual(readProfileView(new URLSearchParams('tag=%0Awork&tags=x%E2%80%8Dy')).config.tags, [])
})

test('all filters, sort and page survive a shareable URL round trip', () => {
  const config = { ...DEFAULT_VIEW, search: 'Client %_', status: 'active' as const, group: 'team', tags: ['work', 'client a'], sort_key: 'last_used' as const, sort_direction: 'desc' as const }
  const query = profileViewQuery(config, 3, new URLSearchParams('utm_source=local&tag=old&tags=old'))
  assert.equal(new URLSearchParams(query).get('utm_source'), 'local')
  assert.equal(new URLSearchParams(query).has('tag'), false)
  assert.deepEqual(readProfileView(new URLSearchParams(query)), { config, page: 3 })
  assert.equal(profileViewQuery(DEFAULT_VIEW, 1), '')
})

test('malformed URL sort/status/page cannot select unknown operations', () => {
  const result = readProfileView(new URLSearchParams('sort=sql&direction=sideways&status=invalid&page=-1&tag=WORK&tags=work'))
  assert.equal(result.page, 1)
  assert.deepEqual(result.config, { ...DEFAULT_VIEW, tags: ['work'] })
})
