'use client'

import { useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { isStaleWrite, proxiesAPI, type LibraryProxy } from '@/lib/api'

export function ProxyForm({ proxy, onClose, onSaved, onReload }: {
  proxy: LibraryProxy | null
  onClose: () => void
  onSaved: (proxy: LibraryProxy) => void
  onReload: () => Promise<void>
}) {
  const { t } = useI18n()
  const [name, setName] = useState(proxy?.name ?? '')
  const [type, setType] = useState(proxy?.type ?? 'http')
  const [server, setServer] = useState(proxy?.server ?? '')
  const [username, setUsername] = useState(proxy?.username ?? '')
  const [password, setPassword] = useState('')
  const [clearPassword, setClearPassword] = useState(false)
  const [country, setCountry] = useState(proxy?.country ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (saving) return
    if (!name.trim() || !server.trim()) { setError(t('Enter a name and proxy server.')); return }
    if ((password || (proxy?.has_password && !clearPassword)) && !username.trim()) { setError(t('A password requires a username. Enter a username or remove the stored password.')); return }
    setSaving(true); setError(null); setStale(false)
    const data: Record<string, unknown> = { name: name.trim(), type, server: server.trim(), username: username.trim() || null, country: country.trim().toUpperCase() || null }
    if (clearPassword) data.password = null
    else if (password) data.password = password
    if (proxy) data.row_version = proxy.row_version
    try {
      const saved = proxy ? await proxiesAPI.update(proxy.id, data) : await proxiesAPI.create(data)
      setPassword('')
      onSaved(saved)
    } catch (err) {
      setStale(isStaleWrite(err))
      setError(isStaleWrite(err) ? t('This proxy changed elsewhere. Your draft is preserved. Reload the saved proxy before editing again.') : t(err instanceof Error ? err.message : 'Could not save proxy'))
    } finally { setSaving(false) }
  }

  return <Modal open title={proxy ? t('Edit library proxy') : t('New library proxy')} onClose={() => { if (!saving) onClose() }} width={540} footer={<>
    <button className="btn btn-default" onClick={onClose} disabled={saving}>{t('Cancel')}</button>
    <button type="submit" form="library-proxy-form" className="btn btn-primary" disabled={saving || stale}>{saving && <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}{saving ? t('Saving…') : proxy ? t('Save changes') : t('Create proxy')}</button>
  </>}>
    <form id="library-proxy-form" className="flex flex-col gap-4" onSubmit={save}>
      <p className="text-ink-dim">{t('Library changes apply only to future assignments. Profiles keep their current proxy settings until you explicitly assign again.')}</p>
      <div><label className="field-label" htmlFor="lp-name">{t('Name')}</label><input id="lp-name" className="field" value={name} onChange={event => setName(event.target.value)} maxLength={255} autoFocus required /></div>
      <div className="grid grid-cols-[100px_1fr] gap-3">
        <div><label className="field-label" htmlFor="lp-type">{t('Type')}</label><select id="lp-type" className="field" value={type} onChange={event => setType(event.target.value)}><option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks4">SOCKS4</option><option value="socks5">SOCKS5</option></select></div>
        <div><label className="field-label" htmlFor="lp-server">{t('Server')}</label><input id="lp-server" className="field font-mono" value={server} onChange={event => setServer(event.target.value)} placeholder="host:port" maxLength={512} required autoComplete="off" aria-describedby="lp-server-help" /><p id="lp-server-help" className="mt-1 text-ink-dim">{t('Host and port only. Use brackets for IPv6: [::1]:8080.')}</p></div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div><label className="field-label" htmlFor="lp-user">{t('Username')}</label><input id="lp-user" className="field" value={username} onChange={event => setUsername(event.target.value)} maxLength={255} autoComplete="off" /></div>
        <div><label className="field-label" htmlFor="lp-country">{t('Country (optional)')}</label><input id="lp-country" className="field uppercase" value={country} onChange={event => setCountry(event.target.value)} maxLength={64} placeholder="DE" /></div>
      </div>
      <div>
        <label className="field-label" htmlFor="lp-password">{proxy?.has_password ? t('Replace password') : t('Password (optional)')}</label>
        <input id="lp-password" className="field" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="new-password" maxLength={4096} disabled={clearPassword} aria-describedby="lp-password-help" />
        <p id="lp-password-help" className="mt-1 text-ink-dim">{proxy?.has_password ? t('A password is stored but never displayed. Leave this blank to keep it.') : t('Passwords require encryption to be configured on this instance.')}</p>
        {proxy?.has_password && <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={clearPassword} onChange={event => { setClearPassword(event.target.checked); setPassword('') }} />{t('Remove stored password')}</label>}
      </div>
      {type === 'socks4' && (username || password || (proxy?.has_password && !clearPassword)) && <p className="text-warn">{t('SOCKS4 credentials are not supported. Use SOCKS5, HTTP or HTTPS for authenticated connections.')}</p>}
      {type === 'socks5' && <p className="text-ink-dim">{t('SOCKS5 authentication is handled automatically by a local relay. No extension or manual browser setup is needed.')}</p>}
      {error && <div role="alert" className="rounded-md border border-line bg-raised p-3"><p className="break-words text-danger">{error}</p>{stale && <><p className="mt-1 text-ink-dim">{t('Reloading replaces this draft with the latest saved values and clears the password field.')}</p><button type="button" className="btn btn-default mt-2" onClick={onReload}>{t('Reload saved proxy')}</button></>}</div>}
    </form>
  </Modal>
}
