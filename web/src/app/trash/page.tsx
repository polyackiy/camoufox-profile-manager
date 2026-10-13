'use client'

import { useCallback, useEffect, useState } from 'react'
import { LoaderCircle, RotateCcw, Trash2 } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { EmptyState } from '@/components/empty-state'
import { Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { ProfileSyncPanel } from '@/components/profile-sync-panel'
import { profileSyncAPI } from '@/lib/sync-api'
import { recoveryAPI, type Profile } from '@/lib/api'

type TrashedProfile = Profile & { deleted_at: string }

export default function TrashPage() {
  const { t, locale } = useI18n()
  const [profiles, setProfiles] = useState<TrashedProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<TrashedProfile | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [syncIds, setSyncIds] = useState<Set<string>>(new Set())
  const [syncProfile, setSyncProfile] = useState<TrashedProfile | null>(null)
  const toast = useToast()
  const load = useCallback(async () => {
    try {
      const [trash, synchronization] = await Promise.allSettled([recoveryAPI.trash(), profileSyncAPI.list()])
      if (trash.status === 'rejected') throw trash.reason
      setProfiles(trash.value)
      if (synchronization.status === 'fulfilled') setSyncIds(new Set(synchronization.value.profiles.map(item => item.local_id)))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    // The initial API request resolves asynchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  async function restore(profile: TrashedProfile) {
    setBusy(profile.id)
    try {
      await recoveryAPI.restoreProfile(profile.id)
      toast('ok', t('Profile restored'), t('{name} is available in Profiles.', { name: profile.name }))
      await load()
    } catch (err) {
      toast('error', t('Could not restore profile'), err instanceof Error ? err.message : String(err))
    } finally { setBusy(null) }
  }

  async function remove() {
    if (!deleting || confirmation !== deleting.name || busy) return
    setBusy(deleting.id)
    try {
      await recoveryAPI.permanentlyDelete(deleting.id)
      toast('ok', t('Profile permanently deleted'), deleting.name)
      setDeleting(null)
      setConfirmation('')
      await load()
    } catch (err) {
      toast('error', t('Could not delete profile'), err instanceof Error ? err.message : String(err))
    } finally { setBusy(null) }
  }

  return <>
    <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
      <h1 className="text-[14px] font-semibold">{t('Trash')}</h1><span className="font-mono text-ink-faint">{profiles.length}</span>
      <button className="btn btn-default ml-auto" onClick={load} disabled={loading || !!busy}>{t('Refresh')}</button>
    </header>
    <p className="px-5 py-4 text-ink-dim">{t('Profiles in Trash keep their cookies, storage and fingerprint. Restore a profile to use it again, or permanently delete its data.')}</p>
    {syncIds.size > 0 && <p className="px-5 pb-4 text-ink-dim">{t('Use Device synchronization to restore a synchronized profile. Permanent deletion is blocked while synchronized history is retained.')}</p>}
    {error ? <EmptyState icon={<Trash2 size={18} />} title={t('Cannot load Trash')} body={error} action={<button className="btn btn-default" onClick={load}>{t('Retry')}</button>} /> : loading ? <p className="px-5 py-8 text-ink-dim" role="status">{t('Loading…')}</p> : profiles.length === 0 ? <EmptyState icon={<Trash2 size={18} />} title={t('Trash is empty')} body={t('Profiles you remove will appear here so you can recover them.')} /> :
      <div className="mx-5 mb-6 overflow-x-auto rounded-lg border border-line"><table className="w-full min-w-[520px] text-left">
        <thead className="bg-surface text-ink-dim"><tr><th className="px-4 py-3 font-medium">{t('Profile')}</th><th className="px-4 py-3 font-medium">{t('Moved to Trash')}</th><th className="px-4 py-3 text-right font-medium">{t('Actions')}</th></tr></thead>
        <tbody className="divide-y divide-line">{profiles.map(profile => <tr key={profile.id}>
          <td className="px-4 py-3"><p className="font-medium">{profile.name}</p><p className="font-mono text-[11px] text-ink-dim">{profile.id}</p></td>
          <td className="px-4 py-3 text-ink-dim">{new Date(profile.deleted_at).toLocaleString(locale)}</td>
          <td className="px-4 py-3"><div className="flex justify-end gap-2">
            {syncIds.has(profile.id) && <button className="btn btn-default min-h-11" onClick={() => setSyncProfile(profile)} disabled={!!busy}>{t('Device synchronization')}</button>}
            <button className="btn btn-default" disabled={!!busy || syncIds.has(profile.id)} onClick={() => restore(profile)}>{busy === profile.id ? <LoaderCircle size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <RotateCcw size={13} aria-hidden="true" />}{t('Restore')}</button>
            <button className="btn btn-danger" disabled={!!busy || syncIds.has(profile.id)} onClick={() => { setConfirmation(''); setDeleting(profile) }} aria-label={t('Permanently delete {name}', { name: profile.name })}>{t('Delete permanently')}</button>
          </div></td>
        </tr>)}</tbody>
      </table></div>}
    {syncProfile && <ProfileSyncPanel profile={syncProfile} running={false} onClose={() => setSyncProfile(null)} onSaved={() => { void load() }} />}
    <Modal open={!!deleting} title={t('Permanently delete profile')} onClose={() => { if (!busy) setDeleting(null) }} width={460} footer={<>
      <button className="btn btn-default" disabled={!!busy} onClick={() => setDeleting(null)}>{t('Cancel')}</button>
      <button className="btn btn-danger" disabled={!!busy || confirmation !== deleting?.name} onClick={remove}>{busy ? t('Deleting…') : t('Delete permanently')}</button>
    </>}>
      <p className="text-ink-dim">{t('This permanently removes {name} and its browser data from this instance. This action cannot be undone. Existing backups are kept separately.', { name: deleting?.name })}</p>
      <label htmlFor="delete-name" className="mb-1 mt-4 block">{t('Type {name} to confirm', { name: deleting?.name })}</label>
      <input id="delete-name" className="field" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" autoFocus />
    </Modal>
  </>
}
