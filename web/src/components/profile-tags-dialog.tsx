'use client'

import { useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { profilesAPI, type BulkTagsResult, type Profile } from '@/lib/api'

export function ProfileTagsDialog({ profiles, onClose, onUpdated }: { profiles: Profile[]; onClose: () => void; onUpdated: (updated: Profile[]) => void }) {
  const { t } = useI18n()
  const [add, setAdd] = useState('')
  const [remove, setRemove] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BulkTagsResult | null>(null)
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (busy || profiles.length > 1000) return
    setBusy(true); setError(null)
    try {
      const response = await profilesAPI.bulkTags(profiles, add.split(',').map(tag => tag.trim()).filter(Boolean), remove.split(',').map(tag => tag.trim()).filter(Boolean))
      setResult(response); onUpdated(response.updated)
    } catch (err) { setError(t(err instanceof Error ? err.message : String(err))) }
    finally { setBusy(false) }
  }
  return <Modal open title={t('Edit tags for selected profiles')} width={560} onClose={() => { if (!busy) onClose() }} footer={<><button className="btn btn-default" disabled={busy} onClick={onClose}>{result ? t('Done') : t('Cancel')}</button>{!result && <button type="submit" form="bulk-tags-form" className="btn btn-primary" disabled={busy || profiles.length > 1000 || (!add.trim() && !remove.trim())}>{busy ? t('Saving…') : t('Apply tags')}</button>}</>}>
    <p className="text-ink-dim">{t('Tags are labels only and can be changed while a browser is running. Each edit uses the profile version you selected.')}</p>
    {result ? <div className="mt-3"><p role="status">{t('Updated tags for {count} profiles.', { count: result.updated.length })}</p>{result.skipped.length > 0 && <div className="mt-3" role="status"><p className="font-medium">{t('Profiles skipped: {count}', { count: result.skipped.length })}</p><ul className="mt-2 space-y-1 text-ink-dim">{result.skipped.map(item => <li key={item.id}>{profiles.find(profile => profile.id === item.id)?.name ?? item.id}: {t(item.reason)}</li>)}</ul><p className="mt-2 text-ink-dim">{t('Skipped profiles were not changed. Reload the profile list before trying again.')}</p></div>}</div> : <form id="bulk-tags-form" onSubmit={save} className="mt-3 flex flex-col gap-3">
      <p>{t('{count} selected', { count: profiles.length })}</p><ul className="max-h-[120px] overflow-y-auto rounded-md border border-line p-2 text-ink-dim">{profiles.map(profile => <li key={profile.id}>{profile.name}</li>)}</ul>
      <div><label className="field-label" htmlFor="tags-add">{t('Add tags')}</label><input id="tags-add" className="field" value={add} onChange={event => setAdd(event.target.value)} maxLength={700} /></div>
      <div><label className="field-label" htmlFor="tags-remove">{t('Remove tags')}</label><input id="tags-remove" className="field" value={remove} onChange={event => setRemove(event.target.value)} maxLength={700} /></div>
      <p className="text-ink-dim">{t('Separate tags with commas. Removals happen first; additions then apply. Each profile can have up to 20 tags of 32 characters.')}</p>
      {profiles.length > 1000 && <p className="text-danger" role="alert">{t('Select at most 1000 profiles for one tag edit.')}</p>}
    </form>}
    {error && <p className="mt-3 text-danger" role="alert">{error}</p>}
  </Modal>
}
