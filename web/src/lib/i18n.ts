import zhCN from './locales/zh-CN.json'

export type Locale = 'en' | 'zh-CN'
export type TranslationValues = Record<string, string | number | null | undefined>
export type Translator = (message: string, values?: TranslationValues) => string
export const LANGUAGE_KEY = 'camoufox-pm.ui-language'

/** English source messages are also the fallback for new server diagnostics. */
export function translate(locale: Locale, message: string, values: TranslationValues = {}): string {
  const catalog: Record<string, string> = zhCN
  const template = locale === 'zh-CN' && Object.hasOwn(catalog, message) ? catalog[message] : message
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    Object.hasOwn(values, key) ? String(values[key] ?? '') : match,
  )
}

export function resolveLocale(saved: string | null, languages: readonly string[]): Locale {
  if (saved === 'en' || saved === 'zh-CN') return saved
  // Use the first supported language in the user's preference order.
  for (const language of languages) {
    if (/^zh(?:-|$)/i.test(language)) return 'zh-CN'
    if (/^en(?:-|$)/i.test(language)) return 'en'
  }
  return 'en'
}
