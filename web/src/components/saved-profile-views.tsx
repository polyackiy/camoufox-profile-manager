'use client'

import { useCallback, useEffect, useState } from 'react'
import { Bookmark, Pencil, Trash2 } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { isStaleWrite, savedFiltersAPI, type ProfileViewConfig, type SavedFilter } from '@/lib/api'

export function SavedProfileViews({ config, onApply }: { config: ProfileViewConfig; onApply: (config: ProfileViewConfig) => void }) {
  const { t } = useI18n()
  const [views, setViews] = useState<SavedFilter[]>([])
  const [selected, setSelected] = useState('')
  const [dialog, setDialog] = useState<{ mode: 'create' | 'rename' | 'update' | 'delete'; view?: SavedFilter } | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  const [listError, setListError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try { setViews(await savedFiltersAPI.list()); setListError(null) }
    catch (err) { setListError(err instanceof Error ? err.message : String(err)) }
  }, [])
  useEffect(() => {
    // The instance's view metadata is read asynchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])
  const active = views.find(view => view.id === selected)
  const modified = active && JSON.stringify(active.config) !== JSON.stringify(config)
  function open(mode: 'create' | 'rename' | 'update' | 'delete') {
    setDialog({ mode, view: active }); setName(mode === 'create' ? '' : active?.name ?? ''); setError(null); setStale(false)
  }
  async function submit(event?: React.FormEvent) {
    event?.preventDefault()
    if (!dialog || busy || stale) return
    setBusy(true); setError(null)
    try {
      if (dialog.mode === 'delete' && dialog.view) {
        await savedFiltersAPI.remove(dialog.view)
        setViews(current => current.filter(view => view.id !== dialog.view?.id)); setSelected('')
      } else {
        const saved = dialog.mode === 'create' ? await savedFiltersAPI.create(name.trim(), config) : await savedFiltersAPI.update(dialog.view!, { name: name.trim(), ...(dialog.mode === 'update' ? { config } : {}) })
        setViews(current => [saved, ...current.filter(view => view.id !== saved.id)]); setSelected(saved.id)
      }
      setDialog(null)
    } catch (err) {
      setStale(isStaleWrite(err))
      setError(isStaleWrite(err) ? t('This saved view changed elsewhere. Reload saved views before changing it again.') : t(err instanceof Error ? err.message : String(err)))
    } finally { setBusy(false) }
  }
  return <>
    <div className="flex flex-wrap items-center gap-2">
      <Bookmark size={14} aria-hidden="true" className="text-ink-dim" />
      <label htmlFor="saved-profile-view" className="sr-only">{t('Saved views')}</label>
      <select id="saved-profile-view" className="field h-[30px] w-[190px]" value={active?.id ?? ''} onChange={event => { const view = views.find(item => item.id === event.target.value); setSelected(event.target.value); if (view) onApply(view.config) }}><option value="">{t('Current view')}</option>{views.map(view => <option key={view.id} value={view.id}>{view.name}</option>)}</select>
      <button className="btn btn-default" onClick={() => open('create')}>{t('Save view')}</button>
      {active && <><button className="btn btn-default" disabled={!modified} onClick={() => open('update')}>{t('Update view')}</button><button className="btn btn-ghost h-[30px] w-[30px] p-0" aria-label={t('Rename saved view')} onClick={() => open('rename')}><Pencil size={13} aria-hidden="true" /></button><button className="btn btn-ghost h-[30px] w-[30px] p-0" aria-label={t('Delete saved view')} onClick={() => open('delete')}><Trash2 size={13} aria-hidden="true" /></button></>}
      {modified && <span className="text-ink-dim">{t('View modified')}</span>}
      <span className="text-[11px] text-ink-dim">{t('Saved views are shared by users of this instance.')}</span>
    </div>
    {listError && <div className="mt-2 flex items-center gap-2 text-danger" role="alert"><span>{t('Could not load saved views')}</span><button className="btn btn-default" onClick={load}>{t('Retry')}</button></div>}
    <Modal open={!!dialog} title={dialog?.mode === 'delete' ? t('Delete saved view') : dialog?.mode === 'rename' ? t('Rename saved view') : dialog?.mode === 'update' ? t('Update saved view') : t('Save current view')} width={460} onClose={() => { if (!busy) setDialog(null) }} footer={<>
      <button className="btn btn-default" disabled={busy} onClick={() => setDialog(null)}>{t('Cancel')}</button>
      <button className={dialog?.mode === 'delete' ? 'btn btn-danger' : 'btn btn-primary'} disabled={busy || stale || (dialog?.mode !== 'delete' && !name.trim())} type={dialog?.mode === 'delete' ? 'button' : 'submit'} form="saved-view-form" onClick={dialog?.mode === 'delete' ? () => submit() : undefined}>{busy ? t('Saving…') : dialog?.mode === 'delete' ? t('Delete') : t('Save')}</button>
    </>}>
      {dialog?.mode === 'delete' ? <p>{t('Delete the saved view "{name}"? Profiles and their settings are kept.', { name: dialog.view?.name })}</p> : <form id="saved-view-form" onSubmit={submit} className="flex flex-col gap-3"><label htmlFor="saved-view-name" className="field-label">{t('View name')}</label><input id="saved-view-name" className="field" maxLength={100} required autoFocus value={name} onChange={event => setName(event.target.value)} /><p className="text-ink-dim">{dialog?.mode === 'rename' ? t('Renaming keeps this saved view’s filters and sort order.') : t('Save the current search, status, group, tags and sort order. Applying a saved view starts on the first page.')}</p></form>}
      {error && <div className="mt-3" role="alert"><p className="text-danger">{error}</p>{stale && <button className="btn btn-default mt-2" disabled={busy} onClick={() => { setDialog(null); load() }}>{t('Reload saved views')}</button>}</div>}
    </Modal>
  </>
}
