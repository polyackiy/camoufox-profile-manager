'use client'

import { useEffect, useRef, useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { ApiRequestError, groupsAPI, proxiesAPI, type Group, type LibraryProxy } from '@/lib/api'
import { templatesAPI, type ProfileTemplate, type TemplateBatch, type TemplateBatchPreview, type TemplateBatchRequest } from '@/lib/templates-api'
import { batchProfileNames } from '@/lib/template-batch-preview'

function endpoint(proxy: { type: string; server: string }) {
  try { const url = new URL(`${proxy.type}://${proxy.server}`); return `${url.protocol}//${url.host}` }
  catch { return '—' }
}

export function TemplateBatchCreate({ template, existing = null, onClose }: {
  template: ProfileTemplate | null
  existing?: TemplateBatch | null
  onClose: () => void
}) {
  const { t } = useI18n()
  const [pattern, setPattern] = useState(`${template?.name ?? ''} {n}`)
  const [count, setCount] = useState(5)
  const [start, setStart] = useState(1)
  const [digits, setDigits] = useState(3)
  const [group, setGroup] = useState('')
  const [allowDuplicates, setAllowDuplicates] = useState(false)
  const [groups, setGroups] = useState<Group[]>([])
  const [proxies, setProxies] = useState<LibraryProxy[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(!existing)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(!!existing)
  const [request, setRequest] = useState<TemplateBatchRequest | null>(existing?.request ?? null)
  const [preview, setPreview] = useState<TemplateBatchPreview | null>(existing?.preview ?? null)
  const [result, setResult] = useState<TemplateBatch | null>(existing)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)
  const incomplete = result?.items.filter(item => item.status === 'pending' || item.status === 'failed').length ?? 0

  useEffect(() => {
    mounted.current = true
    if (!existing) Promise.all([groupsAPI.list(), proxiesAPI.list()]).then(([g, p]) => {
      if (mounted.current) { setGroups(g.groups); setProxies(p) }
    }).catch(() => { if (mounted.current) setLoadFailed(true) }).finally(() => { if (mounted.current) setLoading(false) })
    return () => { mounted.current = false }
  }, [existing])

  function failure(error: unknown) {
    const known = [
      'A profile name already exists; change the names or explicitly allow duplicates',
      'Names repeat within this batch; change the pattern or explicitly allow duplicates',
      'Selected group no longer exists', 'Proxy not found',
      'Select one distinct proxy per profile, or no proxies',
      'Stored proxy credentials are unavailable. Restore the manager encryption key.',
    ]
    if (error instanceof ApiRequestError && known.includes(error.message)) return t(error.message)
    if (error instanceof ApiRequestError && error.status === 409) return t('The template or selected proxy changed. Reload and review a new preview.')
    if (error instanceof ApiRequestError && error.status === 404) return t('The saved template is no longer available. Existing profiles are preserved; use a new preview for the remaining names.')
    if (error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) return t('Administrator access is required. Sign in again before continuing.')
    return t('The request could not be confirmed. Check the saved batch or retry the same batch; do not create a replacement blindly.')
  }

  async function prepare() {
    if (!template || busy || submitted || loading || loadFailed) return
    setError(null)
    let names: string[]
    try { names = batchProfileNames(pattern, count, start, digits) }
    catch (error) { setError(t(error instanceof Error ? error.message : 'Could not prepare profile names.')); return }
    if (selected.length && selected.length !== count) { setError(t('Select exactly {count} distinct proxies, or leave all proxies unselected.', { count })); return }
    const refs = selected.map(id => proxies.find(proxy => proxy.id === id)).filter((proxy): proxy is LibraryProxy => !!proxy)
    if (refs.length !== selected.length) { setError(t('The template or selected proxy changed. Reload and review a new preview.')); return }
    const data: TemplateBatchRequest = { batch_id: crypto.randomUUID(), row_version: template.row_version, names, group: group || null, proxy_refs: refs.map(({ id, row_version }) => ({ id, row_version })), allow_duplicate_names: allowDuplicates }
    setBusy(true)
    try {
      const checked = await templatesAPI.previewBatch(template, data)
      if (mounted.current) { setRequest(data); setPreview(checked) }
    } catch (error) { if (mounted.current) setError(failure(error)) }
    finally { if (mounted.current) setBusy(false) }
  }

  async function create() {
    if (!request || busy || (!template && !existing)) return
    setBusy(true); setSubmitted(true); setError(null)
    try {
      const created = await templatesAPI.createBatch(existing?.template_id ?? template!.id, request)
      if (mounted.current) setResult(created)
    } catch (error) { if (mounted.current) setError(failure(error)) }
    finally { if (mounted.current) setBusy(false) }
  }

  async function checkSaved() {
    if (!request || busy) return
    setBusy(true); setError(null)
    try { const saved = await templatesAPI.getBatch(request.batch_id); if (mounted.current) setResult(saved) }
    catch (error) { if (mounted.current) setError(error instanceof ApiRequestError && error.status === 404 ? t('No saved batch was found. Retrying this same batch is safe.') : failure(error)) }
    finally { if (mounted.current) setBusy(false) }
  }

  function label(status: TemplateBatch['items'][number]['status']) {
    switch (status) {
      case 'created': return t('Created')
      case 'pending': return t('Not created')
      case 'failed': return t('Failed')
      case 'in_trash': return t('In Trash; not recreated')
      case 'unavailable': return t('Removed; not recreated')
    }
  }

  return <Modal open title={t('Create profiles from template')} subtitle={template?.name ?? t('Saved batch')} width={760} onClose={onClose} footer={<div className="flex min-w-0 flex-wrap justify-end gap-2">
    <button className="btn btn-default" onClick={onClose}>{result ? t('Done') : t('Close')}</button>
    {!preview && <button className="btn btn-primary" disabled={busy || loading || loadFailed || !template} onClick={prepare}>{busy ? t('Checking…') : t('Preview batch')}</button>}
    {preview && !submitted && <><button className="btn btn-default" disabled={busy} onClick={() => { setPreview(null); setRequest(null); setError(null) }}>{t('Edit draft')}</button><button className="btn btn-primary" disabled={busy} onClick={create}>{t('Create {count} profiles', { count: preview.names.length })}</button></>}
    {submitted && (!result || incomplete > 0) && <><button className="btn btn-default" disabled={busy} onClick={checkSaved}>{t('Check saved result')}</button><button className="btn btn-primary" disabled={busy} onClick={create}>{busy ? t('Creating…') : result ? t('Resume incomplete') : t('Retry same batch')}</button></>}
  </div>}>
    <p className="text-ink-dim">{t('Each profile starts with fresh browser data and an independent identity. Only template settings and extension snapshots are copied. Proxies are copied only from your explicit selection.')}</p>
    {!preview && <div className="mt-4 space-y-4">
      {loading && <p role="status">{t('Loading…')}</p>}
      {loadFailed && <p role="alert" className="text-danger">{t('Could not load groups and proxies. Close this dialog and try again.')}</p>}
      <label className="block">{t('Name pattern')}<input className="field mt-1" value={pattern} maxLength={255} onChange={event => setPattern(event.target.value)} disabled={busy} /></label>
      <p className="text-ink-dim">{t('Use {n} for the profile number. Preview the exact names before creating anything.')}</p>
      <div className="grid grid-cols-3 gap-3">
        <label>{t('Count')}<input className="field mt-1" type="number" min={1} max={25} value={count} disabled={busy} onChange={event => setCount(Number(event.target.value))} /></label>
        <label>{t('Start number')}<input className="field mt-1" type="number" min={0} max={999999} value={start} disabled={busy} onChange={event => setStart(Number(event.target.value))} /></label>
        <label>{t('Number digits')}<select className="field mt-1" value={digits} disabled={busy} onChange={event => setDigits(Number(event.target.value))}>{[1, 2, 3, 4, 5, 6].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
      </div>
      <label className="block">{t('Group')}<select className="field mt-1" value={group} disabled={busy} onChange={event => setGroup(event.target.value)}><option value="">{t('Ungrouped')}</option>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>
      <label className="flex items-start gap-2"><input type="checkbox" checked={allowDuplicates} disabled={busy} onChange={event => setAllowDuplicates(event.target.checked)} /><span>{t('Allow duplicate profile names. Existing profiles will never be overwritten.')}</span></label>
      <fieldset><legend className="font-medium">{t('Optional proxy list')}</legend><p className="mt-1 text-ink-dim">{t('Select one distinct proxy per profile, or none. The preview follows your selection order; passwords remain hidden.')}</p>
        <p className="mt-1 text-ink-dim">{t('Preview checks names and saved proxy selections. Use Check in the proxy library to test connections.')}</p>
        <ul className="mt-2 max-h-[180px] divide-y divide-line overflow-y-auto rounded-md border border-line">{proxies.map(proxy => <li key={proxy.id}><label className="flex items-start gap-2 px-3 py-2"><input type="checkbox" checked={selected.includes(proxy.id)} disabled={busy || (!selected.includes(proxy.id) && selected.length >= 25)} onChange={() => setSelected(current => current.includes(proxy.id) ? current.filter(id => id !== proxy.id) : [...current, proxy.id])} /><span><span className="block">{proxy.name}</span><span className="break-all font-mono text-[11px] text-ink-dim">{endpoint(proxy)}</span></span></label></li>)}{!loading && !proxies.length && <li className="px-3 py-2 text-ink-dim">{t('No saved proxies. These profiles will use a direct connection.')}</li>}</ul>
      </fieldset>
    </div>}
    {preview && <div className="mt-4">
      <p className="text-ink-dim">{submitted ? t('The batch identifier is retained on this manager. Retrying resumes only rows without a committed profile; deleted profiles are never recreated.') : t('Nothing has been created yet. Confirm the exact names and proxy mapping below.')}</p>
      {busy && submitted && <p className="mt-2 text-ink-dim" role="status">{t('Creation may continue if this dialog closes or the connection drops. Check Recent batches before starting another batch.')}</p>}
      {request && <p className="mt-2 break-all font-mono text-[11px] text-ink-dim">{request.batch_id}</p>}
      {result && <p className="mt-2" role="status">{t('{created} of {total} profiles created', { created: result.items.filter(item => item.profile_id !== null).length, total: result.items.length })}</p>}
      <ol className="mt-3 max-h-[320px] divide-y divide-line overflow-y-auto rounded-md border border-line" aria-label={t('Batch creation preview')}>
        {preview.names.map((name, position) => { const proxy = preview.proxy_mapping[position]; const item = result?.items[position]; return <li key={position} className="px-3 py-2.5"><div className="flex flex-wrap justify-between gap-2"><span className="break-words font-medium">{name}</span><span className={item?.status === 'failed' ? 'text-danger' : 'text-ink-dim'}>{item ? label(item.status) : t('Ready to create')}</span></div><p className="mt-1 break-all text-ink-dim">{proxy ? `${proxy.name} · ${endpoint(proxy)}` : t('Direct connection')}</p>{item?.profile_id && <p className="font-mono text-[11px] text-ink-dim">{item.profile_id}</p>}{item?.error && <p className="mt-1 text-danger">{t(item.error)}</p>}</li> })}
      </ol>
    </div>}
    {error && <p role="alert" className="mt-3 break-words text-danger">{error}</p>}
  </Modal>
}

export function TemplateBatchHistory({ onClose, onReview }: { onClose: () => void; onReview: (batch: TemplateBatch) => void }) {
  const { t, locale } = useI18n()
  const [items, setItems] = useState<TemplateBatch[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  useEffect(() => {
    let alive = true
    templatesAPI.recentBatches().then(items => { if (alive) setItems(items) }).catch(() => { if (alive) setError(true) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])
  return <Modal open title={t('Recent batches')} width={620} onClose={onClose}>
    <p className="text-ink-dim">{t('The last 20 batches on this manager. Opening a saved batch never resumes creation automatically.')}</p>
    {loading ? <p className="mt-3" role="status">{t('Loading…')}</p> : error ? <p role="alert" className="mt-3 text-danger">{t('Could not load batches. Close this dialog and try again.')}</p> : <ul className="mt-3 divide-y divide-line">{items.map(batch => <li key={batch.id} className="flex flex-wrap items-center justify-between gap-2 py-3"><div><p>{new Date(batch.created_at).toLocaleString(locale)}</p><p className="text-ink-dim">{t('{created} of {total} profiles created', { created: batch.items.filter(item => item.profile_id !== null).length, total: batch.items.length })}</p></div><button className="btn btn-default" onClick={() => onReview(batch)}>{t('Review batch {id}', { id: batch.id.slice(0, 8) })}</button></li>)}{!items.length && <li className="py-3 text-ink-dim">{t('No batch creations yet.')}</li>}</ul>}
  </Modal>
}
