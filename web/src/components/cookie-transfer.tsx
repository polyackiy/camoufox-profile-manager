'use client'

import { useAuth } from '@/components/login-gate'
import { useRef, useState } from 'react'
import { Download, FileUp, LoaderCircle } from 'lucide-react'
import { Modal } from '@/components/modal'
import { useI18n } from '@/components/i18n-provider'
import { isStaleWrite, type Profile } from '@/lib/api'
import { cookiesAPI, type CookieFormat, type CookieImportResult, type CookiePreview } from '@/lib/cookies-api'

const MAX_BYTES = 2 * 1024 * 1024

export function CookieTransfer({ profile, onClose, onChanged }: {
  profile: Profile
  onClose: () => void
  onChanged: () => void | Promise<void>
}) {
  const { t } = useI18n()
  const { canManageAccess } = useAuth()
  const canEdit = canManageAccess || profile.permissions?.edit === true
  const canExport = canManageAccess || profile.permissions?.export === true
  const [text, setText] = useState('')
  const [format, setFormat] = useState<CookieFormat>('json')
  const [mode, setMode] = useState<'merge' | 'replace'>('merge')
  const [confirmed, setConfirmed] = useState(false)
  const [preview, setPreview] = useState<CookiePreview | null>(null)
  const [result, setResult] = useState<CookieImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(profile.row_version ?? 0)
  const file = useRef<HTMLInputElement>(null)
  const errorBox = useRef<HTMLDivElement>(null)

  function clearDraft(value: string) {
    setText(value); setPreview(null); setResult(null); setConfirmed(false); setError(null)
  }
  function close() {
    if (busy) return
    setText(''); if (file.current) file.current.value = ''; onClose()
  }
  function fail(err: unknown) {
    setStale(isStaleWrite(err))
    setError(t(isStaleWrite(err) ? 'This profile changed elsewhere. Close this dialog and reload before transferring cookies.' : err instanceof Error ? err.message : 'Could not transfer cookies'))
    requestAnimationFrame(() => errorBox.current?.focus())
  }
  async function readFile(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.target
    const selected = input.files?.[0]
    if (!selected) return
    clearDraft('')
    input.value = ''
    if (selected.size > MAX_BYTES) { setError(t('Cookie input exceeds 2 MiB')); return }
    try { clearDraft(await selected.text()) } catch { setError(t('Could not read the cookie file')) }
  }
  async function validate() {
    if (!canEdit || busy || !text.trim()) return
    setBusy(true); setError(null)
    try {
      if (new Blob([text]).size > MAX_BYTES) throw new Error('Cookie input exceeds 2 MiB')
      setPreview(await cookiesAPI.preview(profile.id, text, format))
    } catch (err) { setPreview(null); fail(err) } finally { setBusy(false) }
  }
  async function importCookies() {
    if (!canEdit || busy || !preview || stale || (mode === 'replace' && !confirmed)) return
    setBusy(true); setError(null)
    try {
      const imported = await cookiesAPI.import(profile.id, text, format, mode, version, confirmed)
      setResult(imported); setVersion(imported.row_version); setText(''); setPreview(null); setConfirmed(false)
      await onChanged()
    } catch (err) { fail(err) } finally { setBusy(false) }
  }
  async function download() {
    if (!canExport || busy || stale) return
    setBusy(true); setError(null)
    try {
      const blob = await cookiesAPI.download(profile.id, format, version)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url; link.download = `cookies.${format === 'json' ? 'json' : 'txt'}`
      document.body.appendChild(link); link.click(); link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  return <Modal open title={t('Transfer cookies')} subtitle={profile.name} onClose={close} width={640} footer={<>
    <button className="btn btn-default" onClick={close} disabled={busy}>{t('Close')}</button>
    {canEdit && <button className="btn btn-primary" onClick={importCookies} disabled={busy || !preview || stale || (mode === 'replace' && !confirmed)}>{busy && <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}{t('Import cookies')}</button>}
  </>}>
    <div className="flex flex-col gap-4">
      <p className="text-ink-dim">{t('Close the profile browser before importing or exporting. Cookie values stay in this dialog and the downloaded file; they are never included in previews.')}</p>
      <p className="text-ink-dim">{t('Cookie transfer requires the browser installed in Settings. Imported session cookies apply once at the next launch and expire when that session closes.')}</p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[160px] flex-1"><label htmlFor="cookie-format" className="field-label">{t('Cookie format')}</label><select id="cookie-format" className="field" value={format} onChange={event => { setFormat(event.target.value as CookieFormat); clearDraft(text) }} disabled={busy}><option value="json">{t('Browser cookie JSON')}</option><option value="netscape">{t('Netscape cookie text')}</option></select></div>
        {canExport && <button className="btn btn-default" onClick={download} disabled={busy || stale}><Download size={14} aria-hidden="true" />{t('Export cookies')}</button>}
      </div>
      {format === 'netscape' && <p className="text-warn">{t('Netscape files preserve HttpOnly, domain, path and expiry, but cannot represent SameSite. Use JSON when those settings matter.')}</p>}
      {canEdit && <>
      <div>
        <label htmlFor="cookie-input" className="field-label">{t('Paste cookies or choose a file')}</label>
        <textarea id="cookie-input" className="field min-h-[160px] font-mono" value={text} onChange={event => clearDraft(event.target.value)} spellCheck={false} autoComplete="off" autoFocus disabled={busy || stale} aria-describedby="cookie-limit" />
        <p id="cookie-limit" className="mt-1 text-ink-dim">{t('Up to 2 MiB and 10000 cookies. Every row is validated before profile data changes.')}</p>
        <div className="mt-2 flex flex-wrap gap-2"><input ref={file} type="file" className="sr-only" accept=".json,.txt,application/json,text/plain" onChange={readFile} aria-label={t('Choose cookie file')} disabled={busy || stale} /><button className="btn btn-default" onClick={() => file.current?.click()} disabled={busy || stale}><FileUp size={14} aria-hidden="true" />{t('Choose cookie file')}</button><button className="btn btn-default" onClick={validate} disabled={busy || stale || !text.trim()}>{t('Validate cookies')}</button></div>
      </div>
      <div><label htmlFor="cookie-mode" className="field-label">{t('Import behavior')}</label><select id="cookie-mode" className="field" value={mode} onChange={event => { setMode(event.target.value as 'merge' | 'replace'); setConfirmed(false) }} disabled={busy}><option value="merge">{t('Merge with existing cookies')}</option><option value="replace">{t('Replace existing cookies')}</option></select><p className="mt-1 text-ink-dim">{mode === 'merge' ? t('Cookies with the same name, domain and path are updated. Other cookies are kept.') : t('Replacement removes existing cookies. A complete profile backup must succeed before replacement begins.')}</p></div>
      {preview && <div role="status" className="rounded-md border border-line bg-raised p-3"><p>{t('{total} rows: {importable} ready to import, {skipped} skipped, {sessions} session cookies.', { total: preview.total, importable: preview.importable, skipped: preview.skipped.length, sessions: preview.session_count })}</p>{preview.skipped.length > 0 && <ul className="mt-2 max-h-24 overflow-auto text-ink-dim">{preview.skipped.slice(0, 20).map((item, index) => <li key={index}>{t('Row {row}: {reason}', { row: item.row, reason: t(item.reason) })}</li>)}</ul>}</div>}
      {mode === 'replace' && preview && <label className="flex items-start gap-2 rounded-md border border-danger/30 p-3"><input type="checkbox" className="mt-0.5" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={busy} /><span>{t('I understand replacement removes existing cookies and creates a recovery backup.')}</span></label>}
      {result && <div role="status" className="rounded-md border border-line bg-raised p-3"><p>{t('Imported {count} cookies; {skipped} skipped.', { count: result.imported, skipped: result.skipped.length })}</p>{canManageAccess && result.backup_id && <p className="mt-1 text-ink-dim">{t('A recovery backup is available in Settings → Profile backups.')}</p>}</div>}
      </>}
      {error && <div ref={errorBox} tabIndex={-1} role="alert" className="rounded-md border border-danger/30 p-3 focus:ring-2 focus:ring-danger"><p className="break-words text-danger">{error}</p>{stale && <button className="btn btn-default mt-2" onClick={async () => { await onChanged(); close() }} disabled={busy}>{t('Close and reload profile')}</button>}</div>}
    </div>
  </Modal>
}
