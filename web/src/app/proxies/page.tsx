'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, LoaderCircle, Network, Pencil, Plus, Search, Trash2, Upload } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { EmptyState } from '@/components/empty-state'
import { Modal } from '@/components/modal'
import { ProxyAssignment } from '@/components/proxy-assignment'
import { ProxyForm } from '@/components/proxy-form'
import { ProxyImport } from '@/components/proxy-import'
import { useToast } from '@/components/toast'
import { isStaleWrite, proxiesAPI, readProxyCheck, type LibraryProxy } from '@/lib/api'

export default function ProxiesPage() {
  const { t, locale } = useI18n()
  const toast = useToast()
  const [proxies, setProxies] = useState<LibraryProxy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [form, setForm] = useState<{ proxy: LibraryProxy | null } | null>(null)
  const [importing, setImporting] = useState(false)
  const [assigning, setAssigning] = useState<LibraryProxy | null>(null)
  const [deleting, setDeleting] = useState<LibraryProxy | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleteStale, setDeleteStale] = useState(false)
  const [checking, setChecking] = useState<Set<string>>(new Set())
  const load = useCallback(async () => {
    try { const data = await proxiesAPI.list(); setProxies(data); setError(null); return data }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); return null }
    finally { setLoading(false) }
  }, [])
  useEffect(() => {
    // The library read completes asynchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  const shown = useMemo(() => proxies.filter(proxy => `${proxy.name} ${proxy.server} ${proxy.country ?? ''}`.toLowerCase().includes(search.toLowerCase())), [proxies, search])
  function upsert(proxy: LibraryProxy) { setProxies(current => current.some(item => item.id === proxy.id) ? current.map(item => item.id === proxy.id ? proxy : item) : [proxy, ...current]) }
  async function check(proxy: LibraryProxy) {
    if (checking.has(proxy.id)) return
    setChecking(current => new Set(current).add(proxy.id))
    try {
      const checked = await proxiesAPI.check(proxy.id)
      upsert(checked)
      const health = checked.last_check ? readProxyCheck(checked.last_check, locale) : null
      toast(health?.tone === 'danger' ? 'error' : 'ok', t('Proxy check completed'), `${proxy.name}: ${health?.label ?? t('Not checked')}`)
    } catch (err) { toast('error', t('Could not check proxy'), t(err instanceof Error ? err.message : String(err))); await load() }
    finally { setChecking(current => { const next = new Set(current); next.delete(proxy.id); return next }) }
  }
  async function reloadDraft() {
    const data = await load()
    if (!data || !form?.proxy) return
    const proxy = data.find(item => item.id === form.proxy?.id)
    if (proxy) setForm({ proxy })
    else { setForm(null); toast('error', t('This library proxy no longer exists.')) }
  }
  async function remove() {
    if (!deleting || deleteBusy || deleteStale) return
    setDeleteBusy(true); setDeleteError(null)
    try {
      await proxiesAPI.remove(deleting.id, deleting.row_version)
      setProxies(current => current.filter(proxy => proxy.id !== deleting.id))
      toast('ok', t('Library proxy deleted'), deleting.name)
      setDeleting(null)
    } catch (err) {
      setDeleteStale(isStaleWrite(err))
      setDeleteError(isStaleWrite(err) ? t('This proxy changed elsewhere. Reload and review it before deleting.') : t(err instanceof Error ? err.message : String(err)))
    } finally { setDeleteBusy(false) }
  }
  async function reloadDelete() {
    const data = await load()
    if (!data || !deleting) return
    const latest = data.find(item => item.id === deleting.id)
    setDeleting(latest ?? null); setDeleteError(null); setDeleteStale(false)
  }

  return <>
    <header className="sticky top-0 z-20 flex min-h-[52px] flex-wrap items-center gap-3 border-b border-line bg-canvas/85 px-5 py-2 backdrop-blur">
      <h1 className="text-[14px] font-semibold">{t('Proxy library')}</h1><span className="font-mono text-ink-faint">{proxies.length}</span>
      <div className="relative ml-2 min-w-[160px] flex-1 max-w-[280px]"><Search size={13} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-dim" /><input className="field h-[30px] pl-7" aria-label={t('Search library proxies')} placeholder={t('Search name, server or country')} value={search} onChange={event => setSearch(event.target.value)} /></div>
      <div className="ml-auto flex gap-2"><button className="btn btn-default" onClick={() => setImporting(true)}><Upload size={13} aria-hidden="true" />{t('Import')}</button><button className="btn btn-primary" onClick={() => setForm({ proxy: null })}><Plus size={14} aria-hidden="true" />{t('New proxy')}</button></div>
    </header>
    <div className="border-b border-line px-5 py-4"><p className="text-ink-dim">{t('Save named proxies once, check them on demand, and assign copies to stopped profiles. Editing or deleting a library entry never changes proxies already assigned to profiles.')}</p><p className="mt-1 text-ink-dim">{t('Checks run only when requested. Stored passwords remain hidden.')}</p></div>
    {error ? <EmptyState icon={<Network size={18} />} title={t('Cannot load proxy library')} body={t(error)} action={<button className="btn btn-default" onClick={load}>{t('Retry')}</button>} /> : loading ? <p className="px-5 py-8 text-ink-dim" role="status">{t('Loading…')}</p> : !proxies.length ? <EmptyState icon={<Network size={18} />} title={t('No library proxies yet')} body={t('Add a named proxy or import a list. Profiles can still use their own manually configured proxies.')} action={<button className="btn btn-primary" onClick={() => setForm({ proxy: null })}>{t('Create a proxy')}</button>} /> : !shown.length ? <EmptyState icon={<Search size={18} />} title={t('No matching proxies')} body={t('Try a different name, server or country.')} action={<button className="btn btn-default" onClick={() => setSearch('')}>{t('Clear search')}</button>} /> : <div className="overflow-x-auto"><table className="w-full min-w-[820px] border-collapse text-left">
      <thead><tr className="border-b border-line text-[11px] uppercase tracking-[0.05em] text-ink-faint"><th className="px-5 py-2 font-medium">{t('Name')}</th><th className="px-3 py-2 font-medium">{t('Connection')}</th><th className="px-3 py-2 font-medium">{t('Credentials')}</th><th className="px-3 py-2 font-medium">{t('Last check')}</th><th className="px-5 py-2 text-right font-medium">{t('Actions')}</th></tr></thead>
      <tbody>{shown.map(proxy => {
        const health = proxy.last_check ? readProxyCheck(proxy.last_check, locale) : null
        const busy = checking.has(proxy.id)
        return <tr key={proxy.id} className="border-b border-line/60 hover:bg-surface">
          <td className="px-5 py-3"><p className="font-medium">{proxy.name}</p><p className="font-mono text-[11px] text-ink-dim">{proxy.country || '—'}</p></td>
          <td className="px-3 py-3"><p className="break-all font-mono">{proxy.type}://{proxy.server}</p></td>
          <td className="px-3 py-3 text-ink-dim"><p className="break-all">{proxy.username || t('No username')}</p><p className="text-[11px]">{proxy.has_password ? t('Password stored') : t('No password')}</p></td>
          <td className="max-w-[220px] px-3 py-3">{health ? <><p className={health.tone === 'ok' ? 'text-ok' : health.tone === 'danger' ? 'text-danger' : 'text-warn'} title={health.detail}>{health.label}</p><p className="text-[11px] text-ink-dim">{proxy.last_check?.ip || '—'}{proxy.last_check?.latency_ms != null ? ` · ${proxy.last_check.latency_ms} ms` : ''}</p><p className="text-[11px] text-ink-dim">{proxy.last_check?.checked_at && new Date(proxy.last_check.checked_at).toLocaleString(locale)}</p><details className="mt-1 text-[11px] text-ink-dim"><summary className="cursor-pointer">{t('Check details')}</summary><p className="mt-1 whitespace-pre-line break-words">{health.detail}</p></details></> : <span className="text-ink-dim">{t('Not checked')}</span>}</td>
          <td className="px-5 py-3"><div className="flex justify-end gap-1.5">
            <button className="btn btn-default" onClick={() => check(proxy)} disabled={busy} aria-label={t('Check {name}', { name: proxy.name })}>{busy ? <LoaderCircle size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <Check size={13} aria-hidden="true" />}{busy ? t('Checking…') : t('Check')}</button>
            <button className="btn btn-default" disabled={busy} onClick={() => setAssigning(proxy)} aria-label={t('Assign {name}', { name: proxy.name })}>{t('Assign')}</button>
            <button className="btn btn-ghost h-[30px] w-[30px] p-0" disabled={busy} aria-label={t('Edit {name}', { name: proxy.name })} onClick={() => setForm({ proxy })}><Pencil size={13} aria-hidden="true" /></button>
            <button className="btn btn-ghost h-[30px] w-[30px] p-0 hover:text-danger" disabled={busy} aria-label={t('Delete {name}', { name: proxy.name })} onClick={() => { setDeleteError(null); setDeleteStale(false); setDeleting(proxy) }}><Trash2 size={13} aria-hidden="true" /></button>
          </div></td>
        </tr>
      })}</tbody>
    </table></div>}
    {form && <ProxyForm key={`${form.proxy?.id ?? 'new'}:${form.proxy?.row_version ?? 0}`} proxy={form.proxy} onClose={() => setForm(null)} onReload={reloadDraft} onSaved={proxy => { upsert(proxy); setForm(null); toast('ok', t('Library proxy saved'), proxy.name) }} />}
    {importing && <ProxyImport onClose={() => setImporting(false)} onImported={load} />}
    {assigning && <ProxyAssignment proxy={assigning} onClose={() => { setAssigning(null); load() }} />}
    <Modal open={!!deleting} title={t('Delete library proxy')} width={460} onClose={() => { if (!deleteBusy) setDeleting(null) }} footer={<><button className="btn btn-default" onClick={() => setDeleting(null)} disabled={deleteBusy}>{t('Cancel')}</button><button className="btn btn-danger" onClick={remove} disabled={deleteBusy || deleteStale}>{deleteBusy ? t('Deleting…') : t('Delete')}</button></>}>
      <p>{t('Delete "{name}" from the library?', { name: deleting?.name })}</p><p className="mt-1 break-all font-mono text-ink-dim">{deleting && `${deleting.type}://${deleting.server}`}</p><p className="mt-3 text-ink-dim">{t('Existing profiles keep their copied proxy settings. Only this reusable library entry is removed.')}</p>
      {deleteError && <div className="mt-3" role="alert"><p className="text-danger">{deleteError}</p>{deleteStale && <button className="btn btn-default mt-2" onClick={reloadDelete}>{t('Reload saved proxy')}</button>}</div>}
    </Modal>
  </>
}
