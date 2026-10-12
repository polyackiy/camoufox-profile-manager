'use client'

import { useI18n } from '@/components/i18n-provider'
import type { Locale } from '@/lib/i18n'

export function LanguageSelector() {
  const { locale, setLocale, t } = useI18n()
  return (
    <label className="flex flex-col gap-1 text-ink-dim">
      <span>{t('Language')}</span>
      <select aria-label={t('Language')} className="field" value={locale} onChange={event => setLocale(event.target.value as Locale)}>
        <option value="en" lang="en">English</option>
        <option value="zh-CN" lang="zh-CN">简体中文</option>
      </select>
    </label>
  )
}
