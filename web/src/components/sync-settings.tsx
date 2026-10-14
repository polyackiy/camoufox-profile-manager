'use client'

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Copy, Download, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/components/login-gate'
import { useI18n } from '@/components/i18n-provider'
import { ConfirmDialog } from '@/components/modal'
import { syncAPI, deviceSyncAPI, profileSyncAPI, connectionCode, readConnectionCode, type OwnerCode, type PairingRequest, type PairingApproval, type RecoveryPackage, type SyncEnrollment, type SyncDevice, type VaultChoice, type PairingDraft } from '@/lib/sync-api'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="flex flex-col gap-1.5 text-ink-dim"><span>{label}</span>{children}</label>
}

function Code({ label, value, onCopy }: { label: string; value: string; onCopy: (text: string) => void }) {
  const { t } = useI18n()
  return <div className="space-y-2"><Field label={label}><textarea className="field min-h-24 resize-y font-mono text-xs" aria-label={label} readOnly value={value} spellCheck={false} /></Field><button type="button" className="btn btn-default min-h-11" onClick={() => onCopy(value)}><Copy size={14} aria-hidden="true" />{t('Copy connection code')}</button></div>
}

function toBase64(bytes: Uint8Array): string {
  let text = ''
  for (const byte of bytes) text += String.fromCharCode(byte)
  return btoa(text).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
}

function fromBase64(text: string): Uint8Array {
  const binary = atob(text.replaceAll('-', '+').replaceAll('_', '/') + '='.repeat((4 - text.length % 4) % 4))
  return Uint8Array.from(binary, char => char.charCodeAt(0))
}

async function fingerprint(request: PairingRequest): Promise<string> {
  const canonical = (value: unknown): unknown => Array.isArray(value) ? value.map(canonical) : value !== null && typeof value === 'object' ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, child]) => [key, canonical(child)])) : value
  const ascii = JSON.stringify(canonical(request)).replace(/[\u007f-\uffff]/g, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`)
  const bytes = new TextEncoder().encode(`camoufox-pm/sync/experimental-v1/identity/PairingRequest\0${ascii}`)
  const hash = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function SyncSettings() {
  const { username, role } = useAuth()
  const { t } = useI18n()
  const titleId = useId()
  const mounted = useRef(true)
  const [status, setStatus] = useState<SyncEnrollment | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [mode, setMode] = useState<'new' | 'join'>('new')
  const [relay, setRelay] = useState('')
  const [label, setLabel] = useState('')
  const [vault, setVault] = useState<VaultChoice>('native')
  const [password, setPassword] = useState('')
  const [consent, setConsent] = useState(false)
  const [recovery, setRecovery] = useState<RecoveryPackage | null>(null)
  const [showSecret, setShowSecret] = useState(false)
  const [savedPackage, setSavedPackage] = useState('')
  const [savedSecret, setSavedSecret] = useState('')
  const [token, setToken] = useState('')
  const [devices, setDevices] = useState<SyncDevice[] | null>(null)
  const [revoke, setRevoke] = useState<string | null>(null)
  const [abandon, setAbandon] = useState(false)
  const [profileCode, setProfileCode] = useState('')
  const [ownerCode, setOwnerCode] = useState('')
  const [inputCode, setInputCode] = useState('')
  const [requestCode, setRequestCode] = useState('')
  const [requestFingerprint, setRequestFingerprint] = useState('')
  const [incomingRequest, setIncomingRequest] = useState<PairingRequest | null>(null)
  const [incomingFingerprint, setIncomingFingerprint] = useState('')
  const [approvalCode, setApprovalCode] = useState('')
  const [draft, setDraft] = useState<PairingDraft | null>(null)
  const [pendingMembership, setPendingMembership] = useState(false)
  const [abandonPairing, setAbandonPairing] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const allowed = !!username && role === 'administrator'
  const fileVault = status?.vault_kind === 'encrypted_file' || (status?.phase === 'not_enrolled' && (draft?.vault_kind ?? vault) === 'encrypted_file')
  const unlockReady = !fileVault || password.length >= 12
  const setupReady = !!label.trim() && consent && unlockReady

  const load = useCallback(async () => {
    if (!allowed) return
    try {
      const value = await syncAPI.status()
      const savedDraft = value.phase === 'not_enrolled' ? await deviceSyncAPI.draft() : null
      const pending = savedDraft && ['preparing', 'pending'].includes(savedDraft.phase) ? savedDraft : null
      const membership = value.authority && value.phase === 'ready' ? await deviceSyncAPI.pendingMembership() : null
      if (mounted.current) {
        setStatus(value); setDraft(pending); setPendingMembership(membership?.phase === 'pending'); setError('')
        setRequestCode(pending?.phase === 'pending' ? connectionCode('request', pending.request) : '')
        setRequestFingerprint(pending?.phase === 'pending' ? pending.fingerprint : '')
      }
    }
    catch { if (mounted.current) setError(t('Synchronization is unavailable. Check your administrator login and the sync installation, then retry.')) }
    finally { if (mounted.current) setLoading(false) }
  }, [allowed, t])

  useEffect(() => {
    mounted.current = true
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    return () => { mounted.current = false }
  }, [load])

  async function run(action: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try { await action() }
    catch { if (mounted.current) { setRecovery(null); setSavedSecret(''); setShowSecret(false); setError(t('The operation could not finish. Check the connection code, your login and unlock password, then retry. Your existing profiles are preserved.')) } }
    finally { if (mounted.current) { setBusy(false); setPassword(''); setToken('') } }
  }

  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); setNotice(t('Copied. Share connection codes only with your own trusted device.')) }
    catch { setError(t('Clipboard access is unavailable. Select and copy the text manually.')) }
  }

  async function selectRecovery(file: File | undefined) {
    setSavedPackage(''); setError('')
    if (!file) return
    if (!file.size || file.size > 90000) { setError(t('Choose a saved Camoufox recovery package smaller than 90 KB.')); return }
    try { const bytes = new Uint8Array(await file.arrayBuffer()); if (mounted.current) setSavedPackage(toBase64(bytes)) }
    catch { setError(t('The recovery file could not be read. Choose it again.')) }
  }

  function downloadRecovery() {
    if (!recovery) return
    try {
      const blob = new Blob([fromBase64(recovery.package) as BlobPart], { type: 'application/octet-stream' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url; link.download = 'camoufox-owner-recovery.cpmrecovery'; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      setNotice(t('Recovery package downloaded. Store the recovery secret separately, then verify both below.'))
    } catch { setError(t('Recovery package could not be downloaded. Export it again.')) }
  }

  const vaultFields = <div className="grid gap-3 sm:grid-cols-2">
    <Field label={t('Device name')}><input className="field min-h-11" value={label} onChange={event => setLabel(event.target.value)} maxLength={80} autoComplete="off" required /></Field>
    <Field label={t('Protect device keys')}><select className="field min-h-11" value={vault} onChange={event => { setVault(event.target.value as VaultChoice); setPassword('') }}><option value="native">{t('System credential store (recommended)')}</option><option value="encrypted_file">{t('Encrypted file with an unlock password')}</option></select></Field>
  </div>

  return <section aria-labelledby={titleId}>
    <h2 id={titleId} className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">{t('Device synchronization')}</h2>
    <div className="panel space-y-4 p-4">
      <p className="rounded-md border border-line bg-raised p-3 text-ink-dim">{t('Experimental preview — off until you set up trusted devices and explicitly enable each profile. Use test profiles; an external security audit is still pending.')}</p>
      <div className="flex items-start gap-3"><ShieldCheck size={20} className="mt-0.5 shrink-0 text-ink-dim" aria-hidden="true" /><div><p className="font-medium">{t('Your profiles, on your trusted devices')}</p><p className="mt-1 text-ink-dim">{t('Encrypted transfers through your own relay. Each profile has one active device; hand it over after closing the browser.')}</p></div></div>
      {!allowed ? <p className="text-ink-dim">{t('Log in with an administrator account to set up synchronization. An API key or profile export permission is not enough.')}</p> : <>
        {loading ? <p role="status">{t('Loading synchronization…')}</p> : status && <p className="rounded-md border border-line bg-raised p-3" role="status">{t(status.phase === 'not_enrolled' ? 'Local only — synchronization is off.' : status.phase === 'preparing' ? 'Setup was interrupted. Resume recovery export or abandon this setup.' : status.phase === 'recovery_required' ? 'Save and verify your recovery package before enabling synchronization.' : status.phase === 'freshness_unknown' ? 'Device connected. Profiles remain blocked until their latest history and contents are verified.' : 'Owner recovery verified. Connect your relay and choose which profiles to synchronize.')}</p>}
        <button type="button" className="btn btn-default min-h-11" disabled={busy} onClick={() => run(load)}><RefreshCw size={14} aria-hidden="true" />{t('Refresh sync status')}</button>
        <fieldset disabled={busy} className="min-w-0 space-y-4">
          {status?.phase === 'not_enrolled' && !draft && <>
            <div className="flex flex-wrap gap-2"><button className={`btn min-h-11 ${mode === 'new' ? 'btn-primary' : 'btn-default'}`} type="button" aria-pressed={mode === 'new'} onClick={() => { setMode('new'); setInputCode('') }}>{t('Set up my first device')}</button><button type="button" className={`btn min-h-11 ${mode === 'join' ? 'btn-primary' : 'btn-default'}`} aria-pressed={mode === 'join'} onClick={() => { setMode('join'); setInputCode('') }}>{t('Connect another device')}</button></div>
            {vaultFields}
            {mode === 'new' ? <Field label={t('Relay address')}><input className="field min-h-11" type="url" value={relay} onChange={event => setRelay(event.target.value)} placeholder="https://relay.example.com" maxLength={2048} autoComplete="off" /></Field> : <Field label={t('Owner connection code')}><textarea className="field min-h-24 font-mono text-xs" value={inputCode} onChange={event => setInputCode(event.target.value)} maxLength={1048576} spellCheck={false} autoComplete="off" /></Field>}
            <label className="flex min-h-11 items-start gap-2 text-ink-dim"><input type="checkbox" className="mt-1" checked={consent} onChange={event => setConsent(event.target.checked)} />{t('I understand that trusted devices receive the selected profiles’ cookies, sessions and proxy credentials.')}</label>
          </>}
          {fileVault && <Field label={t('Unlock password')}><input className="field min-h-11" type="password" aria-label={t('Unlock password')} autoComplete="off" value={password} onChange={event => setPassword(event.target.value)} minLength={12} maxLength={1024} /><span className="text-xs">{t('At least 12 characters. Kept only for this operation; enter it again when needed.')}</span></Field>}
          {status?.phase === 'not_enrolled' && !draft && (mode === 'new' ? <button className="btn btn-primary min-h-11" disabled={!setupReady || !relay.startsWith('https://')} onClick={() => run(async () => { const value = await syncAPI.enroll({ relay_url: relay.trim(), device_label: label.trim(), vault_kind: vault, password: password || undefined }); if (mounted.current) setStatus(value) })}>{t('Create owner and recovery setup')}</button> : <button className="btn btn-primary min-h-11" disabled={!setupReady || !inputCode.trim()} onClick={() => run(async () => { const root = readConnectionCode<OwnerCode>('owner', inputCode); const result = await deviceSyncAPI.prepare({ root, device_label: label.trim(), vault_kind: vault, password: password || undefined }); if (mounted.current) { setRequestCode(connectionCode('request', result.request)); setRequestFingerprint(result.fingerprint); setInputCode(''); setConfirmed(false); setDraft({ root, request: result.request, fingerprint: result.fingerprint, phase: 'pending', device_label: label.trim(), vault_kind: vault }) } })}>{t('Prepare device connection')}</button>)}
          {draft && <div className="space-y-3 rounded-md border border-line p-3"><p>{t('An unfinished device connection is saved on this computer. Resume it with the same keys, or explicitly abandon it before starting again.')}</p><p className="text-ink-dim">{draft.device_label}</p><div className="flex flex-wrap gap-2"><button className="btn btn-default min-h-11" disabled={!unlockReady} onClick={() => run(async () => { await deviceSyncAPI.resume(password || undefined); await load() })}>{t('Resume saved device connection')}</button><button className="btn btn-default min-h-11" disabled={!unlockReady} onClick={() => setAbandonPairing(true)}>{t('Abandon saved device connection')}</button></div></div>}
          {pendingMembership && <div className="space-y-3 rounded-md border border-line p-3"><p>{t('A device membership update is waiting for relay confirmation. Resume this update before making another change.')}</p><button className="btn btn-primary min-h-11" disabled={!unlockReady} onClick={() => run(async () => { const result = await deviceSyncAPI.resumeMembership(password || undefined); await load(); if (result.warning) setNotice(t('Some profile heads could not be verified before revocation. Those profiles may require historical-copy recovery.')) })}>{t('Resume membership update')}</button></div>}
          {requestCode && <div className="space-y-3 border-t border-line pt-4"><p>{t('Send this request to the owner device. Compare the entire fingerprint over a separate trusted channel before approving.')}</p><Code label={t('Device request code')} value={requestCode} onCopy={copy} /><Field label={t('Full pairing fingerprint')}><input className="field min-h-11 font-mono text-xs" readOnly value={requestFingerprint} /></Field><Field label={t('Approved connection code')}><textarea className="field min-h-24 font-mono text-xs" value={inputCode} onChange={event => { setInputCode(event.target.value); setConfirmed(false) }} maxLength={1048576} spellCheck={false} /></Field><label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{t('I compared the full fingerprint with the owner device.')}</label><button className="btn btn-primary min-h-11" disabled={!confirmed || !inputCode.trim() || !unlockReady} onClick={() => run(async () => { const approval = readConnectionCode<PairingApproval>('approval', inputCode); if (approval.request_digest !== requestFingerprint) throw new Error('Pairing mismatch'); const value = await deviceSyncAPI.accept({ approval, confirmed_fingerprint: requestFingerprint, password: password || undefined }); if (mounted.current) { setStatus(value); setDraft(null); setInputCode(''); setRequestCode(''); setRequestFingerprint(''); setConfirmed(false) } })}>{t('Accept trusted device connection')}</button></div>}
          {status && ['preparing', 'recovery_required', 'ready'].includes(status.phase) && status.authority !== false && <details open={status.phase !== 'ready'} className="border-t border-line pt-4"><summary className="cursor-pointer font-medium">{t('Offline owner recovery')}</summary><div className="mt-3 space-y-3"><p className="text-ink-dim">{t('Keep the encrypted package and its secret in separate safe places. Losing every device and recovery secret means losing access; a relay password reset cannot restore profiles.')}</p><button className="btn btn-default min-h-11" disabled={!unlockReady} onClick={() => run(async () => { const result = await syncAPI.recoveryPackage(password || undefined); if (mounted.current) { setRecovery(result); setShowSecret(false); if (status.phase === 'preparing') setStatus({ ...status, phase: 'recovery_required' }) } })}>{t('Export encrypted recovery package')}</button>
            {recovery && <div className="space-y-3 rounded-md border border-line bg-raised p-3"><button className="btn btn-primary min-h-11" onClick={downloadRecovery}><Download size={14} aria-hidden="true" />{t('Download recovery package')}</button><Field label={t('Separate recovery secret')}><input className="field min-h-11 font-mono" type={showSecret ? 'text' : 'password'} readOnly value={recovery.secret} autoComplete="off" /></Field><div className="flex flex-wrap gap-2"><button className="btn btn-default min-h-11" onClick={() => setShowSecret(!showSecret)}>{t(showSecret ? 'Hide recovery secret' : 'Show recovery secret')}</button><button className="btn btn-default min-h-11" onClick={() => copy(recovery.secret)}>{t('Copy recovery secret')}</button><button className="btn btn-default min-h-11" onClick={() => { setRecovery(null); setShowSecret(false) }}>{t('Clear displayed recovery material')}</button></div></div>}
            {status.phase === 'recovery_required' && <div className="space-y-3"><Field label={t('Select the saved recovery package')}><input className="field min-h-11" type="file" accept=".cpmrecovery,application/octet-stream" onChange={event => void selectRecovery(event.target.files?.[0])} /></Field><Field label={t('Enter the separately saved recovery secret')}><input className="field min-h-11 font-mono" type="password" autoComplete="off" value={savedSecret} onChange={event => setSavedSecret(event.target.value)} maxLength={43} /></Field><button className="btn btn-primary min-h-11" disabled={!savedPackage || savedSecret.length !== 43 || !unlockReady} onClick={() => run(async () => { const value = await syncAPI.confirmRecovery({ package: savedPackage, secret: savedSecret }, password || undefined); if (mounted.current) { setStatus(value); setSavedPackage(''); setSavedSecret(''); setRecovery(null); setShowSecret(false) } })}>{t('Verify saved recovery and continue')}</button></div>}
            {status.phase !== 'ready' && <button className="btn btn-default min-h-11" onClick={() => setAbandon(true)}>{t('Abandon incomplete setup')}</button>}
          </div></details>}
          {status?.phase === 'ready' && status.authority && <>
            <details className="border-t border-line pt-4"><summary className="cursor-pointer font-medium">{t('Connect your relay')}</summary><div className="mt-3 space-y-3"><p className="break-all text-ink-dim">{status.relay_url}</p><Field label={t('Relay registration token')}><input className="field min-h-11" type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} maxLength={4096} /></Field><button className="btn btn-primary min-h-11" disabled={!token || !unlockReady} onClick={() => run(async () => { await deviceSyncAPI.connect(token, password || undefined); if (mounted.current) setNotice(t('Relay connected. You can now enable synchronization for selected profiles.')) })}>{t('Register owner with relay')}</button></div></details>
            <details className="border-t border-line pt-4"><summary className="cursor-pointer font-medium">{t('Add a trusted device')}</summary><div className="mt-3 space-y-3"><p className="text-ink-dim">{t('Open Settings on your other device and choose Connect another device. Send it the owner code, then paste its request here.')}</p><button className="btn btn-default min-h-11" onClick={() => run(async () => { const root = await deviceSyncAPI.ownerCode(); if (mounted.current) setOwnerCode(connectionCode('owner', root)) })}>{t('Create owner connection code')}</button>{ownerCode && <Code label={t('Owner connection code')} value={ownerCode} onCopy={copy} />}<Field label={t('Request from the other device')}><textarea className="field min-h-24 font-mono text-xs" value={inputCode} onChange={event => { setInputCode(event.target.value); setIncomingRequest(null); setIncomingFingerprint(''); setConfirmed(false); setApprovalCode('') }} maxLength={1048576} spellCheck={false} /></Field><button className="btn btn-default min-h-11" disabled={!inputCode.trim()} onClick={() => run(async () => { const request = readConnectionCode<PairingRequest>('request', inputCode); const hash = await fingerprint(request); if (mounted.current) { setIncomingRequest(request); setIncomingFingerprint(hash); setConfirmed(false) } })}>{t('Review device request')}</button>{incomingRequest && <><Field label={t('Full pairing fingerprint')}><input className="field min-h-11 font-mono text-xs" readOnly value={incomingFingerprint} /></Field><label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{t('I compared every fingerprint character with the new device through a separate trusted channel.')}</label><button className="btn btn-primary min-h-11" disabled={!confirmed || !unlockReady} onClick={() => run(async () => { const approval = await deviceSyncAPI.approve({ request: incomingRequest, confirmed_fingerprint: incomingFingerprint, password: password || undefined }); if (mounted.current) { setApprovalCode(connectionCode('approval', approval)); setIncomingRequest(null); setInputCode(''); setConfirmed(false) } })}>{t('Approve this exact device')}</button></>}{approvalCode && <Code label={t('Approved connection code')} value={approvalCode} onCopy={copy} />}</div></details>
          </>}
          {status && ['ready', 'freshness_unknown'].includes(status.phase) && <details className="border-t border-line pt-4"><summary className="cursor-pointer font-medium">{t('Trusted devices')}</summary><div className="mt-3 space-y-3"><button className="btn btn-default min-h-11" onClick={() => run(async () => { const result = await deviceSyncAPI.devices(); if (mounted.current) setDevices(result.devices) })}>{t('Refresh trusted devices')}</button>{devices && <ul className="space-y-2">{devices.map(device => <li key={device.device_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line p-3"><span className="min-w-0 break-all font-mono text-xs">{device.device_id}{device.device_id === status.device_id && <span className="ml-2 font-sans">{t('(this device)')}</span>}</span>{status.authority && device.device_id !== status.device_id && <button className="btn btn-danger min-h-11" disabled={!unlockReady} onClick={() => setRevoke(device.device_id)}>{t('Revoke device')}</button>}{status.authority && device.device_id !== status.device_id && <button className="btn btn-default min-h-11" disabled={!unlockReady} onClick={() => run(async () => { const pack = await profileSyncAPI.share(device.device_id, password || undefined); if (mounted.current) setProfileCode(connectionCode('profiles', pack)) })}>{t('Share updated profile list')}</button>}</li>)}</ul>}{profileCode && <><p className="text-ink-dim">{t('Send this signed profile list only to the selected trusted device. On that device, open Profiles from trusted devices and paste the code.')}</p><Code label={t('Updated profile list code')} value={profileCode} onCopy={copy} /></>}</div></details>}
        </fieldset>
        {busy && <p className="flex items-center gap-2 text-ink-dim" role="status"><LoaderCircle size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />{t('Working securely… Keep this page open.')}</p>}
        {notice && <p role="status" className="text-ink-dim">{notice}</p>}
        {error && <p role="alert" className="text-danger">{error}</p>}
      </>}
    </div>
    <ConfirmDialog open={abandonPairing} title={t('Abandon saved device connection?')} body={t('This removes the pending device keys. Any approval for this request will no longer work. Local profiles remain unchanged.')} destructive confirmLabel={t('Abandon connection')} onCancel={() => setAbandonPairing(false)} onConfirm={() => { setAbandonPairing(false); void run(async () => { await deviceSyncAPI.abandon(password || undefined); setInputCode(''); setConfirmed(false); await load() }) }} />
    <ConfirmDialog open={abandon} title={t('Abandon incomplete setup?')} body={t('This discards the unfinished owner setup. Local profiles remain unchanged. Start again only if you no longer need this pending identity.')} destructive confirmLabel={t('Abandon setup')} onCancel={() => setAbandon(false)} onConfirm={() => { setAbandon(false); void run(async () => { await syncAPI.abandon(); setRecovery(null); setSavedPackage(''); setSavedSecret(''); await load() }) }} />
    <ConfirmDialog open={!!revoke} title={t('Revoke trusted device?')} body={t('The device will lose future sync access after peers observe revocation. Downloaded profiles and website sessions cannot be recalled; an offline browser may still be running.')} destructive confirmLabel={t('Revoke device')} onCancel={() => setRevoke(null)} onConfirm={() => { const id = revoke; setRevoke(null); if (id) void run(async () => { const result = await deviceSyncAPI.revoke(id, password || undefined); setDevices(null); setNotice(t('Revocation published. Review website sessions and rotate exposed credentials if the device was lost.') + (result.warning ? ' ' + t('Some profile heads could not be verified before revocation. Those profiles may require historical-copy recovery.') : '')); await load() }) }} />
  </section>
}
