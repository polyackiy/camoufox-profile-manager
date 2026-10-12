'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { browsersAPI, profilesAPI, proxiesAPI, type LibraryProxy, type Profile, type ProxyAssignmentResult } from '@/lib/api'

export function ProxyAssignment({ proxy, onClose }: { proxy: LibraryProxy; onClose: () => void }) {
  const { t } = useI18n()
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [running, setRunning] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [replaceConfirmed, setReplaceConfirmed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ProxyAssignmentResult | null>(null)
  const load = useCallback(async () => {
    try {
      const collected: Profile[] = []
      for (let page = 1; page <= 200; page++) {
        const data = await profilesAPI.getProfiles({ page, per_page: 100 })
        collected.push(...data.profiles)
        if (!data.has_next) break
      }
      // A failed running-state query blocks assignment rather than suggesting
      // that an open browser is stopped. The server also checks remote leases.
      const active = await browsersAPI.active()
      setProfiles(collected)
      setRunning(new Set(active.active_browsers.map(browser => browser.profile_id)))
      setSelected(new Set()); setReplaceConfirmed(false); setError(null)
    } catch (err) { setError(err instanceof Error ? t(err.message) : t('Could not load profiles')) }
    finally { setLoading(false) }
  }, [t])
  useEffect(() => {
    // The initial API reads resolve asynchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  const shown = useMemo(() => profiles.filter(profile => `${profile.name} ${profile.id}`.toLowerCase().includes(search.toLowerCase())), [profiles, search])
  const eligible = shown.filter(profile => !running.has(profile.id))
  const chosen = profiles.filter(profile => selected.has(profile.id))
  const replacing = chosen.filter(profile => !!profile.proxy_config?.server).length
  function connection(profile: Profile): string {
    const config = profile.proxy_config
    if (!config?.server) return t('Direct connection')
    try {
      // Older manually entered profile URLs may include userinfo. Do not display it.
      const address = new URL(config.server.includes('://') ? config.server : `${config.type || 'http'}://${config.server}`)
      return `${address.protocol}//${address.host}`
    } catch { return t('Proxy configured') }
  }
  function toggle(id: string) {
    setSelected(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else if (next.size < 100) next.add(id); return next })
    setReplaceConfirmed(false)
  }
  async function assign() {
    if (busy || !chosen.length || chosen.length > 100 || (replacing > 0 && !replaceConfirmed)) return
    setBusy(true); setError(null)
    try { setResult(await proxiesAPI.assign(proxy, chosen)); setSelected(new Set()); setReplaceConfirmed(false) }
    catch (err) { setError(err instanceof Error ? t(err.message) : t('Could not assign proxy')) }
    finally { setBusy(false) }
  }

  return <Modal open title={t('Assign proxy to profiles')} subtitle={proxy.name} width={700} onClose={() => { if (!busy) onClose() }} footer={<>
    <button className="btn btn-default" disabled={busy} onClick={onClose}>{result ? t('Done') : t('Cancel')}</button>
    {!result && <button className="btn btn-primary" disabled={loading || busy || !!error || !chosen.length || (replacing > 0 && !replaceConfirmed)} onClick={assign}>{busy ? t('Assigning…') : chosen.length === 1 ? t('Assign to 1 profile') : t('Assign to {count} profiles', { count: chosen.length })}</button>}
  </>}>
    <p className="text-ink-dim">{t('Assignment copies this proxy into each selected profile. Later library edits or deletion do not change those profiles. Fingerprints and browser data are preserved.')}</p>
    <p className="mt-2 font-mono text-ink-dim">{proxy.type}://{proxy.server}</p>
    {result ? <div className="mt-4">
      <p className="font-medium" role="status">{result.assigned.length === 1 ? t('Assigned to 1 profile.') : t('Assigned to {count} profiles.', { count: result.assigned.length })}</p>
      {result.skipped.length > 0 && <div className="mt-3 rounded-md border border-line bg-raised p-3" role="status"><p className="font-medium">{result.skipped.length === 1 ? t('1 profile skipped') : t('{count} profiles skipped', { count: result.skipped.length })}</p><ul className="mt-2 space-y-1 text-ink-dim">{result.skipped.map(item => <li key={item.id} className="break-words"><strong className="font-medium text-ink">{profiles.find(profile => profile.id === item.id)?.name ?? item.id}</strong>: {t(item.reason)}</li>)}</ul><p className="mt-2 text-ink-dim">{t('Skipped profiles keep their current proxy. Close running profiles or reload changed records, then assign again.')}</p></div>}
    </div> : <>
      <p className="mt-3 text-ink-dim">{t('Select up to 100 stopped profiles. Profiles in use or changed elsewhere are skipped.')}</p>
      {loading ? <p className="mt-3 text-ink-dim" role="status">{t('Loading profiles…')}</p> : <>
        <div className="relative mt-3"><Search size={13} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-dim" /><input className="field pl-7" aria-label={t('Search profiles')} value={search} onChange={event => setSearch(event.target.value)} /></div>
        <div className="mt-3 flex items-center justify-between gap-2"><span role="status">{t('{count} selected', { count: selected.size })}</span><button className="btn btn-ghost" disabled={busy || !!error || !eligible.length} onClick={() => { setSelected(new Set(eligible.slice(0, 100).map(profile => profile.id))); setReplaceConfirmed(false) }}>{t('Select visible stopped profiles')}</button><button className="btn btn-ghost" disabled={busy || !selected.size} onClick={() => { setSelected(new Set()); setReplaceConfirmed(false) }}>{t('Clear selection')}</button></div>
        <ul className="mt-2 max-h-[280px] divide-y divide-line overflow-y-auto rounded-md border border-line">
          {shown.map(profile => <li key={profile.id}><label className={`flex items-start gap-3 px-3 py-2.5 ${running.has(profile.id) ? 'text-ink-dim' : 'cursor-pointer hover:bg-raised'}`}><input className="mt-1" type="checkbox" checked={selected.has(profile.id)} disabled={busy || !!error || running.has(profile.id) || (!selected.has(profile.id) && selected.size >= 100)} onChange={() => toggle(profile.id)} aria-label={t('Select {name}', { name: profile.name })} /><span className="min-w-0 flex-1"><span className="block font-medium">{profile.name}</span><span className="block break-all font-mono text-[11px] text-ink-dim">{connection(profile)}</span></span>{running.has(profile.id) && <span>{t('Running')}</span>}</label></li>)}
          {!shown.length && <li className="px-3 py-4 text-ink-dim">{profiles.length ? t('No matching profiles') : t('No profiles to assign yet')}</li>}
        </ul>
        {replacing > 0 && <label className="mt-3 flex items-start gap-2 rounded-md border border-line bg-raised p-3"><input type="checkbox" className="mt-0.5" checked={replaceConfirmed} disabled={busy} onChange={event => setReplaceConfirmed(event.target.checked)} /><span>{replacing === 1 ? t('Replace existing proxy settings for the selected profile.') : t('Replace existing proxy settings for {count} selected profiles.', { count: replacing })}</span></label>}
      </>}
      {error && <div className="mt-3" role="alert"><p className="break-words text-danger">{error}</p><p className="mt-1 text-ink-dim">{t('No automatic retry is performed. If the library proxy changed, close this dialog and reopen it. Reload profiles for updated profile versions.')}</p><button className="btn btn-default mt-2" disabled={busy} onClick={load}>{t('Reload profiles')}</button></div>}
    </>}
  </Modal>
}
