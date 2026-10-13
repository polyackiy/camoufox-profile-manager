'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRightLeft, LoaderCircle } from 'lucide-react'
import { SyncRetentionPanel } from './sync-retention-panel'
import { Modal, ConfirmDialog } from './modal'
import { useAuth } from './login-gate'
import { useI18n } from './i18n-provider'
import { profileSyncAPI, syncAPI, readConnectionCode, type ProfileLifecycleAction, type ProfileSyncAction, type ProfileSyncBinding, type ProfileSyncListing } from '@/lib/sync-api'
import type { Profile } from '@/lib/api'

export function syncPhaseLabel(phase: ProfileSyncBinding['phase']): string {
  return ({ held: 'Ready on this device', released: 'Ready for another device', held_elsewhere: 'In use on another device', apply_required: 'Latest profile must be installed', unknown: 'Sync status needs verification', pending: 'Transfer awaiting confirmation', handoff: 'Handoff in progress', conflict: 'Conflicting history — opening blocked', revoked: 'Device access revoked' })[phase]
}

export function ProfileSyncPanel({ profile, running, onClose, onSaved }: { profile: Profile | null; running: boolean; onClose: () => void; onSaved: () => void }) {
  const { username, role } = useAuth()
  const { t } = useI18n()
  const mounted = useRef(true)
  const [listing, setListing] = useState<ProfileSyncListing | null>(null)
  const [authority, setAuthority] = useState(false)
  const [confirmTakeover, setConfirmTakeover] = useState(false)
  const [fileVault, setFileVault] = useState(false)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [profileCode, setProfileCode] = useState('')
  const [password, setPassword] = useState('')
  const [consent, setConsent] = useState(false)
  const [confirmCopy, setConfirmCopy] = useState(false)
  const [lifecycleAction, setLifecycleAction] = useState<ProfileLifecycleAction | null>(null)
  const [confirm, setConfirm] = useState<ProfileSyncAction | null>(null)
  const [selectedId, setSelectedId] = useState(profile?.id ?? '')
  const allowed = !!username && role === 'administrator'
  const binding = listing?.profiles.find(item => item.local_id === selectedId)
  const lifecyclePending = binding?.lifecycle?.pending === true
  const trashed = binding?.lifecycle?.state === 'trashed'
  const unlocked = !fileVault || password.length >= 12
  const load = useCallback(async () => {
    if (!allowed) return
    const status = await syncAPI.status()
    if (mounted.current) { setAuthority(status.authority === true); setFileVault(status.vault_kind === 'encrypted_file'); setReady(['ready', 'freshness_unknown'].includes(status.phase)) }
    if (['ready', 'freshness_unknown'].includes(status.phase)) {
      const result = await profileSyncAPI.list()
      if (mounted.current) setListing(result)
    }
  }, [allowed])
  useEffect(() => {
    mounted.current = true
    void load().catch(() => { if (mounted.current) setError(t('Could not verify synchronization. Check your administrator session and relay connection.')) })
    return () => { mounted.current = false }
  }, [load, t])
  async function run(action: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); onSaved() }
    catch {
      if (mounted.current) setError(t('Synchronization did not finish. Your existing profile is preserved. Refresh its status, then resume the pending operation; do not delete local data.'))
      // A rejected activation may have persisted a verified Trash or conflict fence.
      // Refresh public state so the owner can choose its explicit recovery action.
      try { await load() } catch { /* The visible error and manual refresh remain available. */ }
    }
    finally { if (mounted.current) { setBusy(false); setPassword('') } }
  }
  async function perform(action: ProfileSyncAction) {
    await run(async () => { await profileSyncAPI.action(selectedId, action, password || undefined); if (mounted.current) setNotice(t('Operation completed. Review the verified state below before opening this profile.')) })
  }
  return <>
    <Modal open title={profile ? t('Synchronize {name}', { name: profile.name }) : t('Profiles from trusted devices')} onClose={() => { if (!busy) onClose() }}>
      <div className="space-y-4">
        <p className="text-ink-dim">{t('Close the browser before transferring. Only one trusted device may open a synchronized profile at a time; local access permissions stay on this computer.')}</p>
        {!allowed ? <p>{t('Log in with an administrator account to manage profile synchronization.')}</p> : <>
          <button className="btn btn-default min-h-11" disabled={busy} onClick={() => run(load)}>{t('Refresh sync status')}</button>
          {!ready && <p>{t('Complete device setup and recovery verification in Settings before synchronizing profiles.')}</p>}
          {ready && !profile && <div className="space-y-3 rounded-md border border-line p-3"><label className="flex flex-col gap-1.5">{t('Updated profile list code from the owner')}<textarea className="field min-h-24 font-mono text-xs" value={profileCode} disabled={busy} maxLength={1048576} spellCheck={false} onChange={event => setProfileCode(event.target.value)} /></label><p className="text-ink-dim">{t('Use a new signed code when the owner adds profiles after pairing this device. Existing device keys and profile access remain unchanged.')}</p><button className="btn btn-default min-h-11" disabled={busy || !profileCode.trim()} onClick={() => run(async () => { await profileSyncAPI.acceptCode(readConnectionCode<Record<string, unknown>>('profiles', profileCode)); if (mounted.current) setProfileCode('') })}>{t('Verify updated profile list')}</button></div>}
          {fileVault && <label className="flex flex-col gap-1.5">{t('Unlock password')}<input className="field min-h-11" type="password" autoComplete="off" minLength={12} maxLength={1024} value={password} onChange={event => setPassword(event.target.value)} disabled={busy} /></label>}
          {running && <p role="status" className="text-warn">{t('This browser is running. Stop it before enabling or transferring synchronization.')}</p>}
          {binding && <div className="space-y-3 rounded-md border border-line p-3">
            <p role="status" className="font-medium">{t(trashed ? 'Synchronized profile is in Trash' : syncPhaseLabel(binding.phase))}</p>
            <p className="break-all text-xs text-ink-dim">{t('Sync profile ID')}: {binding.checkpoint.profile_id}</p>
            {binding.phase === 'held_elsewhere' && <p>{t('Close and send this profile from the device currently holding it. A forced takeover cannot close an offline browser.')}</p>}
            {['unknown', 'pending', 'handoff'].includes(binding.phase) && <p>{t('A connection interruption may have hidden the last confirmation. Resume the same operation to avoid publishing conflicting copies.')}</p>}
            {binding.phase === 'conflict' && <p>{t('History verification detected a conflict. Keep both devices’ data and contact the owner; do not overwrite or delete either copy.')}</p>}
            {binding.phase === 'revoked' && <p>{t('The owner revoked this device. Ask the owner to review access; refreshing cannot restore revoked keys.')}</p>}
            {authority && binding.phase === 'held_elsewhere' && <p className="text-ink-dim">{t('If that device is lost, first revoke it in Settings → Device synchronization → Trusted devices. Then recover the last published profile here. Unsent changes on the missing device are unavailable.')}</p>}
            {trashed && <p>{t('A trusted device moved this profile to Trash. Browser data is retained. Confirm applying deletion here, or restore the signed profile as a new version.')}</p>}
            <div className="flex flex-wrap gap-2">
              {trashed && !lifecyclePending && !binding.local_trashed && <button className="btn btn-danger min-h-11" disabled={busy || running || !unlocked} onClick={() => setLifecycleAction('apply-trash')}>{t('Apply deletion on this device')}</button>}
              {trashed && !lifecyclePending && <button className="btn btn-default min-h-11" disabled={busy || running || !unlocked} onClick={() => setLifecycleAction('restore')}>{t('Restore synchronized profile')}</button>}
              {!trashed && !lifecyclePending && binding.phase === 'held' && <button className="btn btn-danger min-h-11" disabled={busy || running || !unlocked} onClick={() => setLifecycleAction('trash')}>{t('Move synchronized profile to Trash')}</button>}
              {authority && binding.phase === 'held_elsewhere' && <button className="btn btn-danger min-h-11" disabled={busy || running || !unlocked} onClick={() => setConfirmTakeover(true)}>{t('Recover from lost device')}</button>}
              {['conflict', 'unknown', 'revoked'].includes(binding.phase) && <button className="btn btn-default min-h-11" disabled={busy || running} onClick={() => setConfirmCopy(true)}>{t('Preserve as a new local copy')}</button>}
              {!trashed && !lifecyclePending && binding.phase === 'held' && <button className="btn btn-primary min-h-11" disabled={busy || running || !unlocked} onClick={() => setConfirm('handoff')}><ArrowRightLeft size={14} aria-hidden="true" />{t('Send to another device')}</button>}
              {trashed && !lifecyclePending && !binding.pending && ['released', 'apply_required', 'unknown'].includes(binding.phase) && <button className="btn btn-default min-h-11" disabled={busy || running || !unlocked} onClick={() => perform('acquire')}>{t('Check for restoration')}</button>}
              {!trashed && ['released', 'apply_required', 'unknown'].includes(binding.phase) && <button className="btn btn-primary min-h-11" disabled={busy || running || !unlocked} onClick={() => perform('acquire')}>{t('Get latest profile')}</button>}
              {(!!binding.pending || binding.phase === 'handoff' || lifecyclePending) && <button className="btn btn-default min-h-11" disabled={busy || running || !unlocked} onClick={() => perform(binding.pending || lifecyclePending ? 'retry' : 'handoff')}>{t('Resume pending transfer')}</button>}
              {binding.phase === 'apply_required' && <button className="btn btn-default min-h-11" disabled={busy || running || !unlocked} onClick={() => perform('recover')}>{t('Recover interrupted installation')}</button>}
            </div>
          </div>}
          {authority && binding && <SyncRetentionPanel key={selectedId} id={selectedId} password={password} busy={busy} unlocked={unlocked} run={run} />}
          {ready && listing && profile && !binding && <div className="space-y-3"><label className="flex min-h-11 items-start gap-2"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} />{t('Allow my trusted devices to receive this profile’s cookies, sessions and proxy credentials.')}</label><button className="btn btn-primary min-h-11" disabled={busy || running || !unlocked || !consent} onClick={() => perform('enable')}>{t('Enable synchronization for this profile')}</button></div>}
          {ready && listing && !profile && <div className="space-y-3"><p>{t('Only profiles authenticated by your owner device are listed here. Add a profile, then get its latest contents before opening it.')}</p><p className="text-ink-dim">{t('For a newly connected device, send the profile again from an existing trusted device so its latest snapshot includes the new device keys.')}</p>{listing.available.length === 0 && <p className="text-ink-dim">{t('No new trusted profiles are available on this device.')}</p>}<ul className="space-y-2">{listing.available.filter(item => !listing.profiles.some(local => local.checkpoint.profile_id === item.profile_id)).map(item => <li key={item.profile_id} className="rounded-md border border-line p-3 space-y-2"><p className="break-all font-mono text-xs">{item.profile_id}</p><button className="btn btn-default min-h-11" disabled={busy || !unlocked} onClick={() => run(async () => { const result = await profileSyncAPI.import(item.profile_id, password || undefined); if (mounted.current) setSelectedId(result.local_id) })}>{t('Add trusted profile to this device')}</button></li>)}</ul></div>}
        </>}
        {busy && <p role="status" className="flex items-center gap-2"><LoaderCircle size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />{t('Transferring securely. Keep this window open.')}</p>}
        {notice && <p role="status">{notice}</p>}{error && <p role="alert" className="text-danger">{error}</p>}
      </div>
    </Modal>
    <ConfirmDialog open={lifecycleAction !== null} title={t(lifecycleAction === 'restore' ? 'Restore synchronized profile?' : lifecycleAction === 'apply-trash' ? 'Apply deletion on this device?' : 'Move synchronized profile to Trash?')} body={t(lifecycleAction === 'restore' ? 'The latest authenticated retained profile will be restored and published as a new version. Local unsent data is backed up before activation. Other devices must retrieve this new version before using the profile.' : lifecycleAction === 'apply-trash' ? 'This applies the verified deletion to this computer. Local browser data and access permissions are retained in Trash; opening stays blocked until an explicit synchronized restore. No local data is permanently erased.' : 'The closed profile will be encrypted and published with a Trash marker, then released. Other trusted devices will see the deletion when they synchronize. Browser data and history are retained for recovery; an offline browser cannot be stopped remotely.')} destructive={lifecycleAction !== 'restore'} confirmLabel={t(lifecycleAction === 'restore' ? 'Restore and publish' : lifecycleAction === 'apply-trash' ? 'Apply verified deletion' : 'Publish deletion')} onCancel={() => setLifecycleAction(null)} onConfirm={() => { const action = lifecycleAction; setLifecycleAction(null); if (action) void run(async () => { await profileSyncAPI.lifecycle(selectedId, action, password || undefined); if (mounted.current) setNotice(t('Operation completed. Review the verified state below before opening this profile.')) }) }} />
    <ConfirmDialog open={confirmTakeover} title={t('Recover this profile from a lost device?')} body={t('First revoke the missing device in Settings. Its browser may still run offline, and existing website sessions cannot be recalled. Recovery uses the last published compatible snapshot and retains a backup of this computer’s local unsent copy. Review website sessions before opening the recovered profile.')} destructive confirmLabel={t('Recover revoked device profile')} onCancel={() => setConfirmTakeover(false)} onConfirm={() => { setConfirmTakeover(false); void run(async () => { await profileSyncAPI.takeover(selectedId, password || undefined); if (mounted.current) setNotice(t('Lost-device recovery completed. Review the verified profile state and website sessions before opening it.')) }) }} />
    <ConfirmDialog open={confirmCopy} title={t('Create a separate local recovery copy?')} body={t('The original synchronized profile stays blocked and unchanged. Another device may still be running the same browser identity. The new copy has no synchronization or shared access permissions; review it before opening.')} confirmLabel={t('Create local copy')} onCancel={() => setConfirmCopy(false)} onConfirm={() => { setConfirmCopy(false); void run(async () => { await profileSyncAPI.preserveCopy(selectedId); setNotice(t('A separate local copy was created. The original sync state is preserved.')) }) }} />
    <ConfirmDialog open={confirm === 'handoff'} title={t('Send this profile to another device?')} body={t('The latest closed profile will be encrypted and uploaded, then released. This device will stop being allowed to open it until you get it back.')} confirmLabel={t('Send profile')} onCancel={() => setConfirm(null)} onConfirm={() => { setConfirm(null); void perform('handoff') }} />
  </>
}
