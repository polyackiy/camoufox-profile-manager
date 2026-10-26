'use client'

import { useEffect, useRef, useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { ApiRequestError, browsersAPI, profilesAPI, proxiesAPI, type Profile, type LibraryProxy, type ProxyAssignmentResult } from '@/lib/api'
import { proxySequenceAPI, type ProxySequencePair, type ProxySequencePreview } from '@/lib/proxy-sequence-api'

function endpoint(proxy: LibraryProxy) {
  try { const url = new URL(`${proxy.type}://${proxy.server}`); return `${url.protocol}//${url.host}` }
  catch { return '—' }
}

export function ProxySequenceAssignment({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [proxies, setProxies] = useState<LibraryProxy[]>([])
  const [running, setRunning] = useState<string[]>([])
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([])
  const [selectedProxies, setSelectedProxies] = useState<string[]>([])
  const [profileSearch, setProfileSearch] = useState('')
  const [proxySearch, setProxySearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [request, setRequest] = useState<ProxySequencePair[] | null>(null)
  const [preview, setPreview] = useState<ProxySequencePreview[] | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [result, setResult] = useState<ProxyAssignmentResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    async function load() {
      try {
        const collected: Profile[] = []
        for (let page = 1; page <= 200; page++) {
          const data = await profilesAPI.getProfiles({ page, per_page: 100 })
          if (!alive.current) return
          collected.push(...data.profiles)
          if (!data.has_next) break
          if (page === 200) throw new Error('Profile list limit')
        }
        const [library, active] = await Promise.all([proxiesAPI.list(), browsersAPI.active()])
        if (alive.current) { setProfiles(collected); setProxies(library); setRunning(active.active_browsers.map(browser => browser.profile_id)) }
      } catch { if (alive.current) setLoadFailed(true) }
      finally { if (alive.current) setLoading(false) }
    }
    load()
    return () => { alive.current = false }
  }, [])

  function failure(error: unknown, mayHaveWritten = false) {
    if (error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) return t('Administrator access is required. Sign in again before continuing.')
    if (error instanceof ApiRequestError && error.status === 409 && error.code !== 'stale_write') return t('Stored proxy credentials are unavailable. Restore the manager encryption key.')
    if (error instanceof ApiRequestError && (error.status === 409 || error.status === 404)) return t('The profile or saved proxy changed, or its key is unavailable. Close and reopen to review current records.')
    if (!mayHaveWritten) return t('Could not prepare the mapping. Close and reopen to review current profiles and saved proxies.')
    return t('Assignment could not be confirmed. No automatic retry was made. Retry the original mapping or close and review current profiles.')
  }
  function toggle(id: string, values: string[], set: (value: string[]) => void) {
    set(values.includes(id) ? values.filter(value => value !== id) : values.length < 100 ? [...values, id] : values)
  }
  function reason(message: string) {
    const known = ['Profile is running or leased', 'Profile not found or in Trash', 'Profile is in Trash', 'Profile not found', 'Profile or proxy changed; reload before assigning', 'Device synchronization blocks this operation', 'Proxy credentials require the original encryption key', 'Assignment could not be confirmed. Reload profiles before trying again.']
    return known.includes(message) ? t(message) : t('Assignment could not be confirmed. Reload profiles before trying again.')
  }
  async function prepare() {
    if (busy || loading || loadFailed || !selectedProfiles.length) return
    if (selectedProfiles.length !== selectedProxies.length) { setError(t('Select the same number of profiles and distinct saved proxies.')); return }
    const pairs = selectedProfiles.map((id, index) => ({ profile: profiles.find(profile => profile.id === id)!, proxy: proxies.find(proxy => proxy.id === selectedProxies[index])! })).map(({ profile, proxy }) => ({ profile: { id: profile.id, row_version: profile.row_version }, proxy: { id: proxy.id, row_version: proxy.row_version } }))
    setError(null); setBusy(true)
    try {
      const items = await proxySequenceAPI.preview(pairs)
      if (alive.current) { setRequest(pairs); setPreview(items); setConfirmed(false) }
    } catch (error) { if (alive.current) setError(failure(error)) }
    finally { if (alive.current) setBusy(false) }
  }
  const replacements = preview?.filter(item => item.replaces_proxy).length ?? 0
  async function assign() {
    if (!request || busy || (replacements > 0 && !confirmed)) return
    setBusy(true); setSubmitted(true); setError(null)
    try { const data = await proxySequenceAPI.assign(request); if (alive.current) setResult(data) }
    catch (error) { if (alive.current) setError(failure(error, true)) }
    finally { if (alive.current) setBusy(false) }
  }
  const shownProfiles = profiles.filter(profile => `${profile.name} ${profile.id}`.toLowerCase().includes(profileSearch.toLowerCase()))
  const shownProxies = proxies.filter(proxy => `${proxy.name} ${proxy.server}`.toLowerCase().includes(proxySearch.toLowerCase()))
  return <Modal open title={t('Assign proxy list')} width={860} onClose={onClose} footer={<div className="flex min-w-0 flex-wrap justify-end gap-2">
    <button className="btn btn-default" onClick={onClose}>{result ? t('Done') : t('Close')}</button>
    {!preview && <button className="btn btn-primary" disabled={busy || loading || loadFailed || !selectedProfiles.length} onClick={prepare}>{busy ? t('Checking…') : t('Preview mapping')}</button>}
    {preview && !submitted && <button className="btn btn-default" disabled={busy} onClick={() => { setPreview(null); setRequest(null); setError(null) }}>{t('Edit selection')}</button>}
    {preview && !result && <button className="btn btn-primary" disabled={busy || (replacements > 0 && !confirmed)} onClick={assign}>{busy ? t('Assigning…') : submitted ? t('Retry original mapping') : t('Confirm assignment')}</button>}
  </div>}>
    <p className="text-ink-dim">{t('Select profiles and saved proxies in the order you want. The first selected profile receives the first selected proxy. Each saved entry is used once; no random switching or automatic sharing.')}</p>
    <p className="mt-2 text-ink-dim">{t('Assignment copies settings into stopped profiles. Later library edits do not change these copies. Browser data and fingerprints are preserved.')}</p>
    {loading && <p className="mt-3" role="status">{t('Loading…')}</p>}
    {loadFailed && <p role="alert" className="mt-3 text-danger">{t('Could not load profiles, proxies and running state. Close this dialog and try again.')}</p>}
    {!preview && !loading && !loadFailed && <div className="mt-4 grid gap-4 sm:grid-cols-2">
      <fieldset><legend className="font-medium">{t('Profiles in selection order')}</legend><label className="mt-2 block">{t('Search profiles')}<input className="field mt-1" value={profileSearch} disabled={busy} onChange={event => setProfileSearch(event.target.value)} /></label><p role="status" className="mt-2 text-ink-dim">{t('{count} profiles selected', { count: selectedProfiles.length })}</p>
        <ul className="mt-2 max-h-[260px] divide-y divide-line overflow-y-auto rounded-md border border-line">{shownProfiles.slice(0, 200).map(profile => <li key={profile.id}><label className="flex items-start gap-2 px-3 py-2"><input type="checkbox" disabled={busy || running.includes(profile.id) || (!selectedProfiles.includes(profile.id) && selectedProfiles.length >= 100)} checked={selectedProfiles.includes(profile.id)} onChange={() => toggle(profile.id, selectedProfiles, setSelectedProfiles)} aria-label={t('Select {name}', { name: profile.name })} /><span className="min-w-0 break-words">{selectedProfiles.includes(profile.id) && <span className="mr-2 font-mono text-ink-dim">{selectedProfiles.indexOf(profile.id) + 1}</span>}{profile.name}{running.includes(profile.id) && <span className="ml-2 text-ink-dim">{t('Running')}</span>}</span></label></li>)}{!shownProfiles.length && <li className="px-3 py-3 text-ink-dim">{t('No matching profiles')}</li>}</ul>
        {shownProfiles.length > 200 && <p className="mt-2 text-ink-dim">{t('Showing the first 200 matches. Narrow the search to find more.')}</p>}
      </fieldset>
      <fieldset><legend className="font-medium">{t('Saved proxies in selection order')}</legend><label className="mt-2 block">{t('Search library proxies')}<input className="field mt-1" value={proxySearch} disabled={busy} onChange={event => setProxySearch(event.target.value)} /></label><p role="status" className="mt-2 text-ink-dim">{t('{count} proxies selected', { count: selectedProxies.length })}</p>
        <ul className="mt-2 max-h-[260px] divide-y divide-line overflow-y-auto rounded-md border border-line">{shownProxies.slice(0, 200).map(proxy => <li key={proxy.id}><label className="flex items-start gap-2 px-3 py-2"><input type="checkbox" disabled={busy || (!selectedProxies.includes(proxy.id) && selectedProxies.length >= 100)} checked={selectedProxies.includes(proxy.id)} onChange={() => toggle(proxy.id, selectedProxies, setSelectedProxies)} aria-label={t('Select proxy {name}', { name: proxy.name })} /><span className="min-w-0 break-words">{selectedProxies.includes(proxy.id) && <span className="mr-2 font-mono text-ink-dim">{selectedProxies.indexOf(proxy.id) + 1}</span>}{proxy.name}<span className="block break-all font-mono text-[11px] text-ink-dim">{endpoint(proxy)}</span></span></label></li>)}{!shownProxies.length && <li className="px-3 py-3 text-ink-dim">{t('No matching proxies')}</li>}</ul>
        {shownProxies.length > 200 && <p className="mt-2 text-ink-dim">{t('Showing the first 200 matches. Narrow the search to find more.')}</p>}
      </fieldset>
    </div>}
    {preview && <div className="mt-4"><p className="text-ink-dim">{t('Review the exact mapping. Names and saved proxy references were checked; execution rechecks versions, leases, device ownership and administrator access. Proxy connectivity is checked separately in the library.')}</p>
      <ol aria-label={t('Proxy mapping preview')} className="mt-3 max-h-[330px] divide-y divide-line overflow-y-auto rounded-md border border-line">{preview.map((item, index) => {
        const skipped = result?.skipped.find(row => row.id === item.profile_id)
        const assigned = result?.assigned.includes(item.profile_id)
        return <li key={item.profile_id} className="px-3 py-3"><p className="break-words font-medium">{index + 1}. {item.profile_name}</p><p className="mt-1 break-all text-ink-dim">{item.current_connection === 'Direct connection' || item.current_connection === 'Proxy configured' ? t(item.current_connection) : item.current_connection} → {item.proxy_name} · {item.connection}</p><p className={`mt-1 ${skipped || (!result && item.reason) ? 'text-warn' : 'text-ink-dim'}`}>{assigned ? t('Assigned') : skipped ? reason(skipped.reason) : result ? t('Not confirmed') : item.reason ? reason(item.reason) : t('Ready to attempt')}</p></li>
      })}</ol>
      {replacements > 0 && !submitted && <label className="mt-3 flex items-start gap-2"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /><span>{replacements === 1 ? t('Replace existing proxy settings for the selected profile.') : t('Replace existing proxy settings for {count} selected profiles.', { count: replacements })}</span></label>}
      {busy && submitted && <p role="status" className="mt-3 text-ink-dim">{t('Assignment may continue if this dialog closes. Review current profiles before starting another assignment.')}</p>}
      {result && <div className="mt-3" role="status"><p>{t('{assigned} assigned; {skipped} skipped', { assigned: result.assigned.length, skipped: result.skipped.length })}</p>{result.skipped.length > 0 && <p className="mt-1 text-ink-dim">{t('Review skipped or unconfirmed profiles. Close and reopen to inspect current versions before assigning again.')}</p>}</div>}
    </div>}
    {error && <p role="alert" className="mt-3 break-words text-danger">{error}</p>}
  </Modal>
}
