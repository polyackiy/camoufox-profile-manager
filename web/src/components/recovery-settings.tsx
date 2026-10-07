'use client'

import { useCallback, useEffect, useState } from 'react'
import { Archive, Download, ExternalLink, LoaderCircle, RotateCcw } from 'lucide-react'
import { Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { recoveryAPI, systemAPI, type AppUpdate, type BackupInfo } from '@/lib/api'

function message(error: unknown): string { return error instanceof Error ? error.message : String(error) }

/** The download is exposed only after the server has completed its backup. */
export function UpdateSettings() {
  const [update, setUpdate] = useState<AppUpdate | null>(null)
  const [checking, setChecking] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [prepared, setPrepared] = useState<{ url: string; count: number; message: string } | null>(null)

  async function check() {
    setChecking(true); setError(null); setPrepared(null)
    try {
      const result = await systemAPI.updates()
      setUpdate(result)
      if (result.error) setError(result.error)
    } catch (err) { setError(message(err)) }
    finally { setChecking(false) }
  }
  async function prepare() {
    setPreparing(true); setError(null)
    try {
      const result = await systemAPI.prepareUpdate()
      if (!isReleaseLink(result.release_url)) throw new Error('The server returned an unverified release link. Check for updates again.')
      setPrepared({ url: result.release_url, count: result.backup_count, message: result.message })
      window.dispatchEvent(new Event('camoufox-backups-changed'))
    } catch (err) { setError(message(err)) }
    finally { setPreparing(false) }
  }

  return <section aria-labelledby="updates-title">
    <h2 id="updates-title" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">App updates</h2>
    <div className="panel p-4">
      <p className="font-medium">Check for a new desktop release</p>
      <p className="mt-1 text-ink-dim">Close all running profiles before updating. We create a backup of every profile before making the release download available. Install the downloaded release to update the app.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button className="btn btn-default" disabled={checking || preparing} onClick={check}>{checking && <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}{checking ? 'Checking…' : 'Check for updates'}</button>
        {update?.available && !prepared && !error && <button className="btn btn-primary" disabled={preparing || checking} onClick={prepare}><Archive size={13} aria-hidden="true" />{preparing ? 'Creating backups…' : 'Back up and prepare update'}</button>}
      </div>
      {update && !error && <p className="mt-3 text-ink-dim" role="status">{update.available ? `Version ${update.latest_version} is available. You have ${update.current_version}.` : `You are up to date (${update.current_version}).`}</p>}
      {error && <p className="mt-3 text-danger" role="alert">{error}</p>}
      {prepared && <div className="mt-3 rounded-md border border-line bg-raised p-3">
        <p role="status">{prepared.message || `${prepared.count} profile backups created. Your release is ready to download.`}</p>
        <a className="btn btn-primary mt-3" href={prepared.url} target="_blank" rel="noopener noreferrer"><Download size={13} aria-hidden="true" />Open release downloads<ExternalLink size={12} aria-hidden="true" /></a>
        <p className="mt-2 text-ink-dim">Download the asset for your operating system. Backups remain in Settings after the update.</p>
      </div>}
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
      setResult(`${response.backups.length} backups created.${response.skipped.length ? ` ${response.skipped.length} profiles skipped: ${response.skipped.map(item => `${item.profile_id}: ${item.reason}`).join('; ')}` : ''}`)
    } catch (err) { setError(message(err)) }
    finally { setBusy(false) }
  }
  async function restoreBackup() {
    if (!restore || busy) return
    setBusy(true); setError(null)
    try {
      const profile = await recoveryAPI.restoreBackup(restore.id)
      toast('ok', 'Backup restored as a new profile', `${profile.name} is available in Profiles.`)
      setRestore(null)
    } catch (err) { setError(message(err)); setRestore(null) }
    finally { setBusy(false) }
  }

  return <section aria-labelledby="backups-title">
    <h2 id="backups-title" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">Profile backups</h2>
    <div className="panel overflow-hidden">
      <div className="p-4">
        <p className="text-ink-dim">Backups include profile settings and browser data. Close running profiles before backing up; running profiles are skipped. Restoring creates a new profile with a new ID and preserves the current profile.</p>
        <p className="mt-2 text-ink-dim">Backups are not encrypted. They can contain signed-in sessions and proxy passwords; keep them private.</p>
        <div className="mt-3 flex gap-2">
          <button className="btn btn-default" disabled={busy || loading} onClick={create}><Archive size={13} aria-hidden="true" />{busy ? 'Working…' : 'Back up profiles'}</button>
          <button className="btn btn-ghost" disabled={busy} onClick={load}>Refresh</button>
        </div>
        {result && <p className="mt-3 break-words text-ink-dim" role="status">{result}</p>}
        {error && <p className="mt-3 break-words text-danger" role="alert">{error}</p>}
      </div>
      {loading ? <p className="border-t border-line px-4 py-3 text-ink-dim" role="status">Loading backups…</p> : backups.length === 0 ? <p className="border-t border-line px-4 py-3 text-ink-dim">No backups yet.</p> :
        <ul className="max-h-[360px] divide-y divide-line overflow-y-auto border-t border-line">{backups.map(backup => <li key={backup.id} className="flex items-center gap-3 px-4 py-3">
          <div className="min-w-0 flex-1"><p className="break-words font-medium">{backup.profile_name}</p><p className="text-ink-dim">{new Date(backup.created_at).toLocaleString()} · {Math.max(1, Math.round(backup.size_bytes / 1024))} KB · {backup.reason}</p></div>
          <button className="btn btn-default shrink-0" disabled={busy} onClick={() => setRestore(backup)} aria-label={`Restore backup of ${backup.profile_name} from ${new Date(backup.created_at).toLocaleString()}`}><RotateCcw size={13} aria-hidden="true" />Restore</button>
        </li>)}</ul>}
    </div>
    <Modal open={!!restore} title="Restore backup as a new profile" onClose={() => { if (!busy) setRestore(null) }} width={460} footer={<>
      <button className="btn btn-default" disabled={busy} onClick={() => setRestore(null)}>Cancel</button>
      <button className="btn btn-primary" disabled={busy} onClick={restoreBackup}>{busy ? 'Restoring…' : 'Restore as new profile'}</button>
    </>}><p className="text-ink-dim">Restore <strong className="text-ink">{restore?.profile_name}</strong> from {restore && new Date(restore.created_at).toLocaleString()}? A separate profile with its saved cookies, storage and fingerprint will be created. Your current profiles and this backup are preserved.</p></Modal>
  </section>
}
