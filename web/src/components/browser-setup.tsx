'use client'

import { useCallback, useEffect, useId, useState } from 'react'
import { Check, Download, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { systemAPI, type BrowserReadiness } from '@/lib/api'

/** Readiness always comes from the executable on disk, never a Python import. */
export function BrowserSetup({ firstProfile = false, onCreate }: { firstProfile?: boolean; onCreate?: () => void }) {
  const { t } = useI18n()
  const [browser, setBrowser] = useState<BrowserReadiness | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [requesting, setRequesting] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [githubToken, setGithubToken] = useState('')
  const tokenId = useId()
  const load = useCallback(async () => {
    try {
      setBrowser(await systemAPI.browser())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    // Remember an explicit dismissal across visits; Settings always keeps setup available.
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDismissed(window.localStorage.getItem('camoufox-pm.setup-dismissed') === '1')
    } catch { /* Storage can be disabled in embedded browsers. */ }
    // The request completes asynchronously; this effect starts the initial API read.
    load()
  }, [load])

  useEffect(() => {
    if (browser?.state !== 'downloading') return
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [browser?.state, load])

  async function install() {
    const token = githubToken.trim()
    setGithubToken('')
    setRequesting(true)
    setError(null)
    try {
      setBrowser(await systemAPI.installBrowser(token || undefined))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRequesting(false)
    }
  }

  // Ready returning users need no onboarding. Settings always retains setup.
  if (onCreate && !browser && !error) return null
  if (onCreate && (dismissed || (browser?.installed && !firstProfile))) return null
  const downloading = requesting || browser?.state === 'downloading'
  const ready = browser?.installed && browser.state !== 'error' && !downloading
  const failure = error || browser?.error
  const progress = browser?.progress == null ? null : Math.min(100, Math.max(0, browser.progress))

  return (
    <section aria-label={t('Browser setup')} className={`panel ${onCreate ? 'mx-5 my-4' : ''}`}>
      <div className="flex items-start gap-3 p-4">
        <span className={`mt-0.5 shrink-0 ${ready ? 'text-ok' : 'text-signal'}`} aria-hidden="true">
          {downloading ? <LoaderCircle size={19} className="animate-spin motion-reduce:animate-none" /> : ready ? <Check size={19} /> : <Download size={19} />}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">{ready ? (firstProfile ? t('Ready for your first profile') : t('Camoufox browser is ready')) : downloading ? t('Installing Camoufox browser') : t('Set up your browser')}</h2>
          <p className="mt-1 text-ink-dim">
            {ready ? t('Browser {version}. Each profile keeps its own cookies, storage and fingerprint.', { version: browser.version || t('installed') }) : t('Download the browser once to launch profiles. Creating a profile does not require a download.')}
          </p>
          {firstProfile && <ol className="mt-3 space-y-1 text-ink-dim">
            <li>{t('1. Install the browser once, or create your profile while it downloads.')}</li>
            <li>{t('2. Give the profile a name. Defaults are ready to use; a proxy is optional.')}</li>
            <li>{t('3. Click Run to open the browser. Click Stop before editing, moving or backing up the profile.')}</li>
          </ol>}
          {firstProfile && <p className="mt-2 text-ink-faint">{t('Interface language only changes this manager. It never changes profile languages, timezone or fingerprint.')}</p>}
          <p className="mt-1 text-ink-dim" role="status" aria-live="polite">{(browser?.message && browser.message !== failure && t(browser.message)) || (!error && !browser ? t('Checking browser files…') : '')}</p>
          {downloading && <div className="mt-3 flex items-center gap-3">
            <progress aria-label={t('Browser installation progress')} className="h-2 w-full accent-signal" max={100} value={progress ?? undefined} />
            {progress !== null && <span className="font-mono text-ink-dim">{Math.round(progress)}%</span>}
          </div>}
          {failure && <div className="mt-2" role="alert">
            <p className="break-words text-danger">{t(failure)}</p>
            <p className="mt-1 text-ink-dim">{t('Check your internet connection, free disk space and permission to write browser files, then retry installation. Setup stays available in Settings.')}</p>
          </div>}
          {!ready && <details className="mt-3 text-ink-dim">
            <summary className="cursor-pointer">{t('GitHub download authentication (optional)')}</summary>
            <div className="mt-2 max-w-lg">
              <p id={`${tokenId}-help`}>{t('If GitHub limits downloads, use a personal access token for this attempt. It is not saved and is sent only to the GitHub API.')}</p>
              <a className="mt-1 inline-block text-signal underline" href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener noreferrer">{t('Create a GitHub token')}</a>
              <p className="mt-1">{t('Public repository access is enough. Do not grant write access or access to private repositories.')}</p>
              <label className="mt-2 block" htmlFor={tokenId}>{t('GitHub token')}</label>
              <input id={tokenId} type="password" className="field mt-1" autoComplete="off" spellCheck={false} maxLength={255} disabled={downloading} value={githubToken} aria-describedby={`${tokenId}-help`} onChange={event => setGithubToken(event.target.value)} />
            </div>
          </details>}
          <div className="mt-3 flex flex-wrap gap-2">
            {!ready && <button className="btn btn-primary" disabled={downloading || (!browser && !error)} onClick={browser ? install : load}>
              {downloading ? t('Installing…') : !browser ? t('Check again') : failure ? t('Retry installation') : t('Install browser')}
            </button>}
            {ready && onCreate && <button className="btn btn-primary" onClick={onCreate}>{t('Create first profile')}</button>}
            {ready && !onCreate && <button className="btn btn-default" disabled={downloading} onClick={install}><RefreshCw size={13} aria-hidden="true" />{t('Retry download')}</button>}
            {!ready && onCreate && firstProfile && <button className="btn btn-default" onClick={onCreate}>{t('Create a profile first')}</button>}
            {!onCreate && <button className="btn btn-ghost" disabled={downloading} onClick={load}>{t('Check again')}</button>}
          </div>
        </div>
        {onCreate && <button className="btn btn-ghost h-7 w-7 shrink-0 p-0" aria-label={t('Dismiss setup banner')} title={t('Setup remains available in Settings')} onClick={() => { setDismissed(true); try { window.localStorage.setItem('camoufox-pm.setup-dismissed', '1') } catch { /* Dismissal still applies for this visit. */ } }}><X size={15} aria-hidden="true" /></button>}
      </div>
    </section>
  )
}
