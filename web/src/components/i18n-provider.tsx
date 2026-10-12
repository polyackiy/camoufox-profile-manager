'use client'

import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from 'react'
import { LANGUAGE_KEY, resolveLocale, translate, type Locale, type Translator } from '@/lib/i18n'

const LANGUAGE_EVENT = 'camoufox-pm.language-changed'
let selectedLocale: Locale | undefined

function getLocale(): Locale {
  if (selectedLocale) return selectedLocale
  let saved = null
  try { saved = window.localStorage.getItem(LANGUAGE_KEY) } catch { /* Embedded storage may be disabled. */ }
  selectedLocale = resolveLocale(saved, navigator.languages)
  return selectedLocale
}

function setLocale(locale: Locale) {
  selectedLocale = locale
  try { window.localStorage.setItem(LANGUAGE_KEY, locale) } catch { /* Keep the choice for this visit. */ }
  window.dispatchEvent(new Event(LANGUAGE_EVENT))
}

function subscribe(onChange: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key !== LANGUAGE_KEY && event.key !== null) return
    selectedLocale = undefined
    onChange()
  }
  window.addEventListener(LANGUAGE_EVENT, onChange)
  window.addEventListener('storage', onStorage)
  return () => {
    window.removeEventListener(LANGUAGE_EVENT, onChange)
    window.removeEventListener('storage', onStorage)
  }
}

const I18nContext = createContext<{
  locale: Locale
  setLocale: (locale: Locale) => void
  t: Translator
}>({ locale: 'en', setLocale, t: (message, values) => translate('en', message, values) })

/** The server snapshot stays English so the static export hydrates consistently. */
export function I18nProvider({ children }: { children: React.ReactNode }) {
  const locale = useSyncExternalStore(subscribe, getLocale, () => 'en' as const)
  const value = useMemo(() => ({
    locale,
    setLocale,
    t: (message: string, values?: Parameters<Translator>[1]) => translate(locale, message, values),
  }), [locale])

  useEffect(() => { document.documentElement.lang = locale }, [locale])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n() { return useContext(I18nContext) }
