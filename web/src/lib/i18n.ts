import zhCN from './locales/zh-CN.json'
import ru from './locales/ru.json'

export type Locale = 'en' | 'ru' | 'zh-CN'
export type PluralTranslation = Partial<Record<Intl.LDMLPluralRule, string>> & { other: string }
export type TranslationCatalog = Record<string, string | PluralTranslation>
export type TranslationValues = Record<string, string | number | null | undefined>
export type Translator = (message: string, values?: TranslationValues) => string
export const LANGUAGE_KEY = 'camoufox-pm.ui-language'
export const catalogs: Record<Exclude<Locale, 'en'>, TranslationCatalog> = { ru, 'zh-CN': zhCN }
const pluralRules = { ru: new Intl.PluralRules('ru'), 'zh-CN': new Intl.PluralRules('zh-CN') }

/** English source messages are also the fallback for new server diagnostics. */
export function translate(locale: Locale, message: string, values: TranslationValues = {}): string {
  const catalog = locale === 'en' ? null : catalogs[locale]
  const entry = catalog && Object.hasOwn(catalog, message) ? catalog[message] : message
  const count = values.count
  const category = locale !== 'en' && typeof count === 'number' && Number.isFinite(count)
    ? pluralRules[locale].select(count) : 'other'
  const template = typeof entry === 'string' ? entry : entry[category] ?? entry.other
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    Object.hasOwn(values, key) ? String(values[key] ?? '') : match,
  )
}

export function resolveLocale(saved: string | null, languages: readonly string[]): Locale {
  if (saved === 'en' || saved === 'ru' || saved === 'zh-CN') return saved
  // Use the first supported language in the user's preference order.
  for (const language of languages) {
    if (/^zh(?:-|$)/i.test(language)) return 'zh-CN'
    if (/^ru(?:-|$)/i.test(language)) return 'ru'
    if (/^en(?:-|$)/i.test(language)) return 'en'
  }
  return 'en'
}
