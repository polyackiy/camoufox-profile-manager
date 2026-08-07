'use client'

import { useEffect, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'

import { Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { profilesAPI, type Group, type Profile } from '@/lib/api'

interface FormState {
  name: string
  group: string
  status: string
  notes: string
  os: string
  timezone: string
  languages: string
  hardwareConcurrency: string
  windowWidth: string
  windowHeight: string
  webrtcMode: string
  geoMode: 'auto' | 'manual'
  latitude: string
  longitude: string
  proxyType: string
  proxyServer: string
  proxyUsername: string
  proxyPassword: string
}

const EMPTY: FormState = {
  name: '',
  group: '',
  status: 'active',
  notes: '',
  os: 'windows',
  timezone: '',
  languages: '',
  hardwareConcurrency: '',
  windowWidth: '1280',
  windowHeight: '720',
  webrtcMode: 'replace',
  geoMode: 'auto',
  latitude: '',
  longitude: '',
  proxyType: 'http',
  proxyServer: '',
  proxyUsername: '',
  proxyPassword: '',
}

function fromProfile(profile: Profile): FormState {
  const bs = profile.browser_settings ?? {}
  const proxy = profile.proxy ?? profile.proxy_config ?? {}
  const geo = bs.geolocation
  const lat = geo?.lat ?? geo?.latitude
  const lon = geo?.lon ?? geo?.longitude
  return {
    name: profile.name,
    group: profile.group ?? '',
    status: profile.status,
    notes: profile.notes ?? '',
    os: bs.os ?? 'windows',
    timezone: bs.timezone ?? '',
    languages: (bs.languages ?? []).join(', '),
    hardwareConcurrency: bs.hardware_concurrency ? String(bs.hardware_concurrency) : '',
    windowWidth: String(bs.window_width ?? 1280),
    windowHeight: String(bs.window_height ?? 720),
    webrtcMode: bs.webrtc_mode ?? 'replace',
    geoMode: lat != null && lon != null ? 'manual' : 'auto',
    latitude: lat != null ? String(lat) : '',
    longitude: lon != null ? String(lon) : '',
    proxyType: proxy.type ?? 'http',
    proxyServer: proxy.server ?? '',
    proxyUsername: proxy.username ?? '',
    proxyPassword: proxy.password ?? '',
  }
}

interface Props {
  open: boolean
  /** null = create a new profile. */
  profile: Profile | null
  groups: Group[]
  onClose: () => void
  onSaved: () => void
}

export function ProfileForm({ open, profile, groups, onClose, onSaved }: Props) {
  const isEdit = profile !== null
  const [form, setForm] = useState<FormState>(EMPTY)
  const [saving, setSaving] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const toast = useToast()

  useEffect(() => {
    if (open) setForm(profile ? fromProfile(profile) : EMPTY)
  }, [open, profile])

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  function buildBrowserSettings() {
    const languages = form.languages
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

    return {
      os: form.os,
      timezone: form.timezone.trim() || null,
      languages: languages.length ? languages : undefined,
      hardware_concurrency: form.hardwareConcurrency ? Number(form.hardwareConcurrency) : null,
      window_width: Number(form.windowWidth) || 1280,
      window_height: Number(form.windowHeight) || 720,
      webrtc_mode: form.webrtcMode,
      geolocation:
        form.geoMode === 'manual' && form.latitude && form.longitude
          ? { lat: Number(form.latitude), lon: Number(form.longitude) }
          : null,
    }
  }

  function buildProxy() {
    if (!form.proxyServer.trim()) return null
    return {
      type: form.proxyType,
      server: form.proxyServer.trim(),
      username: form.proxyUsername.trim() || undefined,
      password: form.proxyPassword.trim() || undefined,
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!form.name.trim()) {
      toast('error', 'Name is required')
      return
    }
    if (form.geoMode === 'manual' && (!form.latitude || !form.longitude)) {
      toast('error', 'Manual geolocation needs both latitude and longitude')
      return
    }

    setSaving(true)
    try {
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        group: form.group || null,
        notes: form.notes.trim() || null,
        browser_settings: buildBrowserSettings(),
        proxy_config: buildProxy(),
      }

      if (isEdit) {
        payload.status = form.status
        await profilesAPI.updateProfile(profile.id, payload)
        toast('ok', 'Profile updated', form.name.trim())
      } else {
        // The backend generates a consistent fingerprint, then applies these.
        payload.generate_fingerprint = true
        await profilesAPI.createProfile(payload)
        toast('ok', 'Profile created', form.name.trim())
      }
      onSaved()
      onClose()
    } catch (err) {
      toast('error', isEdit ? 'Could not update profile' : 'Could not create profile', String(err))
    } finally {
      setSaving(false)
    }
  }

  async function handleRegenerate() {
    if (!profile) return
    setRegenerating(true)
    try {
      const updated = await profilesAPI.resetFingerprint(profile.id)
      setForm(fromProfile(updated))
      toast('ok', 'Fingerprint regenerated')
      onSaved()
    } catch (err) {
      toast('error', 'Could not regenerate fingerprint', String(err))
    } finally {
      setRegenerating(false)
    }
  }

  return (
    <Modal
      open={open}
      title={isEdit ? 'Edit profile' : 'New profile'}
      subtitle={
        isEdit
          ? `${profile.id} · created ${new Date(profile.created_at).toLocaleDateString()}`
          : 'Anything left blank is generated as a consistent fingerprint.'
      }
      onClose={onClose}
      width={640}
      footer={
        <>
          {isEdit && (
            <button
              type="button"
              className="btn btn-default mr-auto"
              onClick={handleRegenerate}
              disabled={regenerating}
            >
              <RefreshCw size={13} className={regenerating ? 'animate-spin' : ''} />
              Regenerate fingerprint
            </button>
          )}
          <button type="button" className="btn btn-default" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="profile-form" className="btn btn-primary" disabled={saving}>
            {saving && <Loader2 size={13} className="animate-spin" />}
            {isEdit ? 'Save changes' : 'Create profile'}
          </button>
        </>
      }
    >
      <form id="profile-form" onSubmit={handleSubmit} className="flex flex-col gap-5">
        <Section title="Identity">
          <div className="col-span-2">
            <label className="field-label" htmlFor="pf-name">
              Name
            </label>
            <input
              id="pf-name"
              className="field"
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
              placeholder="account-1"
              autoFocus
              required
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-group">
              Group
            </label>
            <select
              id="pf-group"
              className="field"
              value={form.group}
              onChange={(e) => set('group', e.target.value)}
            >
              <option value="">No group</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="field-label" htmlFor="pf-os">
              Operating system
            </label>
            <select
              id="pf-os"
              className="field"
              value={form.os}
              onChange={(e) => set('os', e.target.value)}
            >
              <option value="windows">Windows</option>
              <option value="macos">macOS</option>
              <option value="linux">Linux</option>
            </select>
          </div>

          {isEdit && (
            <div>
              <label className="field-label" htmlFor="pf-status">
                Status
              </label>
              <select
                id="pf-status"
                className="field"
                value={form.status}
                onChange={(e) => set('status', e.target.value)}
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
                <option value="blocked">Blocked</option>
                <option value="maintenance">Maintenance</option>
              </select>
            </div>
          )}

          <div className="col-span-2">
            <label className="field-label" htmlFor="pf-notes">
              Notes
            </label>
            <textarea
              id="pf-notes"
              className="field resize-y"
              rows={2}
              value={form.notes}
              onChange={(e) => set('notes', e.target.value)}
            />
          </div>
        </Section>

        <Section
          title="Proxy"
          hint="Leave the server empty for a direct connection."
        >
          <div>
            <label className="field-label" htmlFor="pf-proxy-type">
              Type
            </label>
            <select
              id="pf-proxy-type"
              className="field"
              value={form.proxyType}
              onChange={(e) => set('proxyType', e.target.value)}
            >
              <option value="http">HTTP</option>
              <option value="https">HTTPS</option>
              <option value="socks4">SOCKS4</option>
              <option value="socks5">SOCKS5</option>
            </select>
          </div>

          <div>
            <label className="field-label" htmlFor="pf-proxy-server">
              Server
            </label>
            <input
              id="pf-proxy-server"
              className="field font-mono"
              value={form.proxyServer}
              onChange={(e) => set('proxyServer', e.target.value)}
              placeholder="host:port"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-proxy-user">
              Username
            </label>
            <input
              id="pf-proxy-user"
              className="field"
              value={form.proxyUsername}
              onChange={(e) => set('proxyUsername', e.target.value)}
              autoComplete="off"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-proxy-pass">
              Password
            </label>
            <input
              id="pf-proxy-pass"
              type="password"
              className="field"
              value={form.proxyPassword}
              onChange={(e) => set('proxyPassword', e.target.value)}
              autoComplete="new-password"
            />
          </div>
        </Section>

        <Section
          title="Fingerprint"
          hint="Camoufox keeps the fingerprint internally consistent; only override what you need."
        >
          <div>
            <label className="field-label" htmlFor="pf-tz">
              Timezone
            </label>
            <input
              id="pf-tz"
              className="field font-mono"
              value={form.timezone}
              onChange={(e) => set('timezone', e.target.value)}
              placeholder="Europe/Berlin"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-langs">
              Languages
            </label>
            <input
              id="pf-langs"
              className="field font-mono"
              value={form.languages}
              onChange={(e) => set('languages', e.target.value)}
              placeholder="en-US, en"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-cores">
              CPU cores
            </label>
            <input
              id="pf-cores"
              type="number"
              min={1}
              max={32}
              className="field"
              value={form.hardwareConcurrency}
              onChange={(e) => set('hardwareConcurrency', e.target.value)}
              placeholder="auto"
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-webrtc">
              WebRTC
            </label>
            <select
              id="pf-webrtc"
              className="field"
              value={form.webrtcMode}
              onChange={(e) => set('webrtcMode', e.target.value)}
            >
              <option value="replace">Replace with proxy IP</option>
              <option value="real">Use the real IP</option>
              <option value="forward">Forward</option>
              <option value="none">Disable WebRTC</option>
            </select>
          </div>

          <div>
            <label className="field-label" htmlFor="pf-win-w">
              Window width
            </label>
            <input
              id="pf-win-w"
              type="number"
              className="field"
              value={form.windowWidth}
              onChange={(e) => set('windowWidth', e.target.value)}
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-win-h">
              Window height
            </label>
            <input
              id="pf-win-h"
              type="number"
              className="field"
              value={form.windowHeight}
              onChange={(e) => set('windowHeight', e.target.value)}
            />
          </div>

          <div>
            <label className="field-label" htmlFor="pf-geo">
              Geolocation
            </label>
            <select
              id="pf-geo"
              className="field"
              value={form.geoMode}
              onChange={(e) => set('geoMode', e.target.value as 'auto' | 'manual')}
            >
              <option value="auto">From the proxy IP</option>
              <option value="manual">Set coordinates</option>
            </select>
          </div>

          {form.geoMode === 'manual' && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="field-label" htmlFor="pf-lat">
                  Latitude
                </label>
                <input
                  id="pf-lat"
                  className="field font-mono"
                  value={form.latitude}
                  onChange={(e) => set('latitude', e.target.value)}
                  placeholder="52.52"
                />
              </div>
              <div>
                <label className="field-label" htmlFor="pf-lon">
                  Longitude
                </label>
                <input
                  id="pf-lon"
                  className="field font-mono"
                  value={form.longitude}
                  onChange={(e) => set('longitude', e.target.value)}
                  placeholder="13.405"
                />
              </div>
            </div>
          )}
        </Section>
      </form>
    </Modal>
  )
}

function Section({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <fieldset>
      <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {title}
      </legend>
      {hint && <p className="mb-2.5 text-ink-faint">{hint}</p>}
      <div className="grid grid-cols-2 gap-3">{children}</div>
    </fieldset>
  )
}
