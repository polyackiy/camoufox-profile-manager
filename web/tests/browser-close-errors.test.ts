import assert from 'node:assert/strict'
import test from 'node:test'
import { ApiRequestError, browserCloseFailure } from '../src/lib/api'
import { translate, type Locale } from '../src/lib/i18n'

const messages = [
  'Browser exit could not be confirmed. Close remaining browsers, then retry Stop or restart the manager.',
  'Some browser exits could not be confirmed. Retry Stop for active profiles.',
]

for (const locale of ['en', 'ru', 'zh-CN'] as Locale[]) {
  test(`${locale}: close recovery refers to the actual Stop control`, () => {
    for (const message of messages) {
      const detail = browserCloseFailure(new ApiRequestError(message, 500), text => translate(locale, text))
      assert.ok(detail?.includes(translate(locale, 'Stop')))
      if (locale !== 'en') assert.notEqual(detail, message)
    }
  })
}

test('close recovery never rewrites access denials, arbitrary details or partial message matches', () => {
  const translated: string[] = []
  const t = (message: string) => { translated.push(message); return message }
  for (const error of [
    new ApiRequestError(messages[0], 403),
    new ApiRequestError('private diagnostic and profile path', 500),
    new ApiRequestError(messages[0] + ' private suffix', 500),
    new Error(messages[0]),
    null,
  ]) assert.equal(browserCloseFailure(error, t), null)
  assert.deepEqual(translated, [])
})
