'use client'

import { useCallback, useEffect, useState } from 'react'
import { Check, Download, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { systemAPI, type BrowserReadiness } from '@/lib/api'

/** Readiness always comes from the executable on disk, never a Python import. */
export function BrowserSetup({ firstProfile = false, onCreate }: { firstProfile?: boolean; onCreate?: () => void }) {
  const [browser, setBrowser] = useState<BrowserReadiness | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [requesting, setRequesting] = useState(false)
  const [dismissed, setDismissed] = useState(false)
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
    setRequesting(true)
    setError(null)
    try {
      setBrowser(await systemAPI.installBrowser())
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
    <section aria-label="Browser setup" className={`panel ${onCreate ? 'mx-5 my-4' : ''}`}>
      <div className="flex items-start gap-3 p-4">
        <span className={`mt-0.5 shrink-0 ${ready ? 'text-ok' : 'text-signal'}`} aria-hidden="true">
          {downloading ? <LoaderCircle size={19} className="animate-spin motion-reduce:animate-none" /> : ready ? <Check size={19} /> : <Download size={19} />}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">{ready ? (firstProfile ? 'Ready for your first profile' : 'Camoufox browser is ready') : downloading ? 'Installing Camoufox browser' : 'Set up your browser'}</h2>
          <p className="mt-1 text-ink-dim">
            {ready ? `Browser ${browser.version || 'installed'}. Each profile keeps its own cookies, storage and fingerprint.` : 'Download the browser once to launch profiles. Creating a profile does not require a download.'}
          </p>
          <p className="mt-1 text-ink-dim" role="status" aria-live="polite">{browser?.message || (!error && !browser ? 'Checking browser files…' : '')}</p>
          {downloading && <div className="mt-3 flex items-center gap-3">
            <progress aria-label="Browser installation progress" className="h-2 w-full accent-signal" max={100} value={progress ?? undefined} />
            {progress !== null && <span className="font-mono text-ink-dim">{Math.round(progress)}%</span>}
          </div>}
          {failure && <p className="mt-2 break-words text-danger" role="alert">{failure}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            {!ready && <button className="btn btn-primary" disabled={downloading || (!browser && !error)} onClick={browser ? install : load}>
              {downloading ? 'Installing…' : !browser ? 'Check again' : failure ? 'Retry installation' : 'Install browser'}
            </button>}
            {ready && onCreate && <button className="btn btn-primary" onClick={onCreate}>Create first profile</button>}
            {ready && !onCreate && <button className="btn btn-default" disabled={downloading} onClick={install}><RefreshCw size={13} aria-hidden="true" />Retry download</button>}
            {!ready && onCreate && firstProfile && <button className="btn btn-default" onClick={onCreate}>Create a profile first</button>}
            {!onCreate && <button className="btn btn-ghost" disabled={downloading} onClick={load}>Check again</button>}
          </div>
        </div>
        {onCreate && <button className="btn btn-ghost h-7 w-7 shrink-0 p-0" aria-label="Dismiss setup banner" title="Setup remains available in Settings" onClick={() => { setDismissed(true); try { window.localStorage.setItem('camoufox-pm.setup-dismissed', '1') } catch { /* Dismissal still applies for this visit. */ } }}><X size={15} aria-hidden="true" /></button>}
      </div>
    </section>
  )
}
