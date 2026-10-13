'use client'

import { useCallback, useEffect, useState } from 'react'
import { Archive, Download, ExternalLink, LoaderCircle, RotateCcw } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { VerifiedUpdateSettings } from '@/components/verified-update-settings'
import { useToast } from '@/components/toast'
import { recoveryAPI, systemAPI, type AppUpdate, type BackupInfo } from '@/lib/api'

function message(error: unknown): string { return error instanceof Error ? error.message : String(error) }

/** The download is exposed only after the server has completed its backup. */
export function UpdateSettings() {
  const { t } = useI18n()
  const [update, setUpdate] = useState<AppUpdate | null>(null)
  const [checking, setChecking] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [prepared, setPrepared] = useState<{ url: string; count: number } | null>(null)
  const [preview, setPreview] = useState(false)
  async function check() {
    setChecking(true); setError(null); setPrepared(null)
    try {
      const result = await systemAPI.updates(preview)
      setUpdate(result)
      if (result.error) setError(result.error)
    } catch (err) { setError(message(err)) }
    finally { setChecking(false) }
  }
  async function prepare() {
    setPreparing(true); setError(null)
    try {
      const result = await systemAPI.prepareUpdate(preview)
      if (!isReleaseLink(result.release_url)) throw new Error(t('The server returned an unverified release link. Check for updates again.'))
      setPrepared({ url: result.release_url, count: result.backup_count })
      window.dispatchEvent(new Event('camoufox-backups-changed'))
    } catch (err) { setError(message(err)) }
    finally { setPreparing(false) }
  }

  return <section aria-labelledby="updates-title">
    <h2 id="updates-title" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">{t('App updates')}</h2>
    <div className="panel p-4">
      <p className="font-medium">{t('Check for a new desktop release')}</p>
      <p className="mt-1 text-ink-dim">{t('Close all running profiles before updating. We create a backup of every profile before making the release download available. Install the downloaded release to update the app.')}</p>
      <label className="mt-3 flex min-h-11 items-center gap-2 text-ink-dim"><input type="checkbox" checked={preview} disabled={checking || preparing} onChange={event => { setPreview(event.target.checked); setUpdate(null); setPrepared(null); setError(null) }} />{t('Include preview releases (opt-in)')}</label>
      {preview && <p className="text-ink-dim">{t('Preview installers may be unsigned and are intended for testing. Stable remains the default channel.')}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button className="btn btn-default" disabled={checking || preparing} onClick={check}>{checking && <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}{checking ? t('Checking…') : t('Check for updates')}</button>
        {update?.available && !prepared && !error && <button className="btn btn-primary" disabled={preparing || checking} onClick={prepare}><Archive size={13} aria-hidden="true" />{preparing ? t('Creating backups…') : t('Back up and prepare update')}</button>}
      </div>
      {update && !error && <p className="mt-3 text-ink-dim" role="status">{update.available ? t('Version {latest} is available. You have {current}.', { latest: update.latest_version, current: update.current_version }) : t('You are up to date ({version}).', { version: update.current_version })}</p>}
      {error && <p className="mt-3 text-danger" role="alert">{error}</p>}
      {prepared && <div className="mt-3 rounded-md border border-line bg-raised p-3">
        <p role="status">{t('{count} profile backups created. Your release is ready to download.', { count: prepared.count })}</p>
        <a className="btn btn-primary mt-3" href={prepared.url} target="_blank" rel="noopener noreferrer"><Download size={13} aria-hidden="true" />{t('Open release downloads')}<ExternalLink size={12} aria-hidden="true" /></a>
        <p className="mt-2 text-ink-dim">{t('Download the asset for your operating system. Backups remain in Settings after the update.')}</p>
      </div>}
      <details className="mt-4 border-t border-line pt-3">
        <summary className="cursor-pointer font-medium">{t('Update recovery')}</summary>
        <p className="mt-2 text-ink-dim">{t('Verified updates create a complete recovery point. Profile backups cover individual profiles; keep your existing data folder when using a manual installer.')}</p>
        <VerifiedUpdateSettings preview={preview} />
      </details>
    </div>
  </section>
}

/** The server verifies the repository; the UI also rejects non-release and unsafe links. */
function isReleaseLink(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && parsed.hostname === 'github.com' && /^\/polyackiy\/camoufox-profile-manager\/releases\/tag\/[^/]+\/?$/.test(parsed.pathname) && !parsed.username && !parsed.password
  } catch { return false }
}

export function BackupSettings() {
  const { t, locale } = useI18n()
  const [backups, setBackups] = useState<BackupInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [restore, setRestore] = useState<BackupInfo | null>(null)
  const toast = useToast()
  const load = useCallback(async () => {
    try { setBackups(await recoveryAPI.backups()); setError(null) }
    catch (err) { setError(message(err)) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => {
    // The request completes asynchronously; this effect starts the initial API read.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
    window.addEventListener('camoufox-backups-changed', load)
    return () => window.removeEventListener('camoufox-backups-changed', load)
  }, [load])
  async function create() {
    setBusy(true); setError(null); setResult(null)
    try {
      const response = await recoveryAPI.createBackup()
      await load()
      setResult([t('{count} backups created.', { count: response.backups.length }), response.skipped.length ? t('{count} profiles skipped: {details}', { count: response.skipped.length, details: response.skipped.map(item => `${item.profile_id}: ${item.reason}`).join('; ') }) : ''].filter(Boolean).join(' '))
    } catch (err) { setError(message(err)) }
    finally { setBusy(false) }
  }
  async function restoreBackup() {
    if (!restore || busy) return
    setBusy(true); setError(null)
    try {
      const profile = await recoveryAPI.restoreBackup(restore.id)
      toast('ok', t('Backup restored as a new profile'), t('{name} is available in Profiles.', { name: profile.name }))
      setRestore(null)
    } catch (err) { setError(message(err)); setRestore(null) }
    finally { setBusy(false) }
  }

  return <section aria-labelledby="backups-title">
    <h2 id="backups-title" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">{t('Profile backups')}</h2>
    <div className="panel overflow-hidden">
      <div className="p-4">
        <p className="text-ink-dim">{t('Backups include profile settings and browser data. Close running profiles before backing up; running profiles are skipped. Restoring creates a new profile with a new ID and preserves the current profile.')}</p>
        <p className="mt-2 text-ink-dim">{t('Backups are not encrypted. They can contain signed-in sessions and proxy passwords; keep them private.')}</p>
        <div className="mt-3 flex gap-2">
          <button className="btn btn-default" disabled={busy || loading} onClick={create}><Archive size={13} aria-hidden="true" />{busy ? t('Working…') : t('Back up profiles')}</button>
          <button className="btn btn-ghost" disabled={busy} onClick={load}>{t('Refresh')}</button>
        </div>
        {result && <p className="mt-3 break-words text-ink-dim" role="status">{result}</p>}
        {error && <p className="mt-3 break-words text-danger" role="alert">{error}</p>}
      </div>
      {loading ? <p className="border-t border-line px-4 py-3 text-ink-dim" role="status">{t('Loading backups…')}</p> : backups.length === 0 ? <p className="border-t border-line px-4 py-3 text-ink-dim">{t('No backups yet.')}</p> :
        <ul className="max-h-[360px] divide-y divide-line overflow-y-auto border-t border-line">{backups.map(backup => <li key={backup.id} className="flex items-center gap-3 px-4 py-3">
          <div className="min-w-0 flex-1"><p className="break-words font-medium">{backup.profile_name}</p><p className="text-ink-dim">{new Date(backup.created_at).toLocaleString(locale)} · {Math.max(1, Math.round(backup.size_bytes / 1024))} KB · {t(backup.reason)}</p></div>
          <button className="btn btn-default shrink-0" disabled={busy} onClick={() => setRestore(backup)} aria-label={t('Restore backup of {name} from {date}', { name: backup.profile_name, date: new Date(backup.created_at).toLocaleString(locale) })}><RotateCcw size={13} aria-hidden="true" />{t('Restore')}</button>
        </li>)}</ul>}
    </div>
    <Modal open={!!restore} title={t('Restore backup as a new profile')} onClose={() => { if (!busy) setRestore(null) }} width={460} footer={<>
      <button className="btn btn-default" disabled={busy} onClick={() => setRestore(null)}>{t('Cancel')}</button>
      <button className="btn btn-primary" disabled={busy} onClick={restoreBackup}>{busy ? t('Restoring…') : t('Restore as new profile')}</button>
    </>}><p className="text-ink-dim">{restore && t('Restore {name} from {date}? A separate profile with its saved cookies, storage and fingerprint will be created. Your current profiles and this backup are preserved.', { name: restore.profile_name, date: new Date(restore.created_at).toLocaleString(locale) })}</p></Modal>
  </section>
}
