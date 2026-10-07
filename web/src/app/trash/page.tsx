'use client'

import { useCallback, useEffect, useState } from 'react'
import { LoaderCircle, RotateCcw, Trash2 } from 'lucide-react'
import { EmptyState } from '@/components/empty-state'
import { Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { recoveryAPI, type Profile } from '@/lib/api'

type TrashedProfile = Profile & { deleted_at: string }

export default function TrashPage() {
  const [profiles, setProfiles] = useState<TrashedProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<TrashedProfile | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const toast = useToast()
  const load = useCallback(async () => {
    try {
      setProfiles(await recoveryAPI.trash())
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
      toast('ok', 'Profile restored', `${profile.name} is available in Profiles.`)
      await load()
    } catch (err) {
      toast('error', 'Could not restore profile', err instanceof Error ? err.message : String(err))
    } finally { setBusy(null) }
  }

  async function remove() {
    if (!deleting || confirmation !== deleting.name || busy) return
    setBusy(deleting.id)
    try {
      await recoveryAPI.permanentlyDelete(deleting.id)
      toast('ok', 'Profile permanently deleted', deleting.name)
      setDeleting(null)
      setConfirmation('')
      await load()
    } catch (err) {
      toast('error', 'Could not delete profile', err instanceof Error ? err.message : String(err))
    } finally { setBusy(null) }
  }

  return <>
    <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
      <h1 className="text-[14px] font-semibold">Trash</h1><span className="font-mono text-ink-faint">{profiles.length}</span>
      <button className="btn btn-default ml-auto" onClick={load} disabled={loading || !!busy}>Refresh</button>
    </header>
    <p className="px-5 py-4 text-ink-dim">Profiles in Trash keep their cookies, storage and fingerprint. Restore a profile to use it again, or permanently delete its data.</p>
    {error ? <EmptyState icon={<Trash2 size={18} />} title="Cannot load Trash" body={error} action={<button className="btn btn-default" onClick={load}>Retry</button>} /> : loading ? <p className="px-5 py-8 text-ink-dim" role="status">Loading…</p> : profiles.length === 0 ? <EmptyState icon={<Trash2 size={18} />} title="Trash is empty" body="Profiles you remove will appear here so you can recover them." /> :
      <div className="mx-5 mb-6 overflow-x-auto rounded-lg border border-line"><table className="w-full min-w-[520px] text-left">
        <thead className="bg-surface text-ink-dim"><tr><th className="px-4 py-3 font-medium">Profile</th><th className="px-4 py-3 font-medium">Moved to Trash</th><th className="px-4 py-3 text-right font-medium">Actions</th></tr></thead>
        <tbody className="divide-y divide-line">{profiles.map(profile => <tr key={profile.id}>
          <td className="px-4 py-3"><p className="font-medium">{profile.name}</p><p className="font-mono text-[11px] text-ink-dim">{profile.id}</p></td>
          <td className="px-4 py-3 text-ink-dim">{new Date(profile.deleted_at).toLocaleString()}</td>
          <td className="px-4 py-3"><div className="flex justify-end gap-2">
            <button className="btn btn-default" disabled={!!busy} onClick={() => restore(profile)}>{busy === profile.id ? <LoaderCircle size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <RotateCcw size={13} aria-hidden="true" />}Restore</button>
            <button className="btn btn-danger" disabled={!!busy} onClick={() => { setConfirmation(''); setDeleting(profile) }} aria-label={`Permanently delete ${profile.name}`}>Delete permanently</button>
          </div></td>
        </tr>)}</tbody>
      </table></div>}
    <Modal open={!!deleting} title="Permanently delete profile" onClose={() => { if (!busy) setDeleting(null) }} width={460} footer={<>
      <button className="btn btn-default" disabled={!!busy} onClick={() => setDeleting(null)}>Cancel</button>
      <button className="btn btn-danger" disabled={!!busy || confirmation !== deleting?.name} onClick={remove}>{busy ? 'Deleting…' : 'Delete permanently'}</button>
    </>}>
      <p className="text-ink-dim">This permanently removes {deleting?.name} and its browser data from this instance. This action cannot be undone. Existing backups are kept separately.</p>
      <label htmlFor="delete-name" className="mb-1 mt-4 block">Type <strong className="break-all">{deleting?.name}</strong> to confirm</label>
      <input id="delete-name" className="field" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" autoFocus />
    </Modal>
  </>
}
