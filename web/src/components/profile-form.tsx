'use client'

import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, Info, Loader2, RefreshCw, X } from 'lucide-react'

import { Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import {
  hasGeography,
  OS_LABELS,
  presetsAPI,
  profilesAPI,
  type DevicePreset,
  type FingerprintSummary,
  type Group,
  type Profile,
  type ProxyCheck,
} from '@/lib/api'

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
  stableCanvas: boolean
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
  stableCanvas: false,
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
  const proxy = profile.proxy_config ?? {}
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
    stableCanvas: bs.stable_canvas ?? false,
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
  const [refreshingBrowser, setRefreshingBrowser] = useState(false)
  const [checkingProxy, setCheckingProxy] = useState(false)
  const [proxyCheck, setProxyCheck] = useState<ProxyCheck | null>(null)
  // The `profile` prop is a snapshot from the list; regenerating or updating the
  // browser returns a new machine, and the panel has to show it without waiting
  // for the dialog to be reopened.
  const [machine, setMachine] = useState<FingerprintSummary | null | undefined>(undefined)
  const [reconciling, setReconciling] = useState(false)
  // Whether the *saved* profile states where it is. The form fields alone cannot
  // answer that once the user has started typing in them.
  const [storedGeography, setStoredGeography] = useState(false)
  const [clearingGeography, setClearingGeography] = useState(false)
  const [presets, setPresets] = useState<DevicePreset[]>([])
  const [presetId, setPresetId] = useState('')
  const toast = useToast()

  useEffect(() => {
    if (open) {
      // The dialog is kept mounted and refilled when it opens, so the form has
      // to be reset here. Remounting it per profile with a key would avoid the
      // effect but lose the open/close transition.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setForm(profile ? fromProfile(profile) : EMPTY)
      setMachine(profile?.fingerprint)
      setStoredGeography(profile ? hasGeography(profile) : false)
      setPresetId('')
    }
  }, [open, profile])

  // Presets only matter while creating: an existing profile is already pinned.
  useEffect(() => {
    if (!open || isEdit || presets.length) return
    presetsAPI
      .list()
      .then(setPresets)
      .catch(() => setPresets([]))
  }, [open, isEdit, presets.length])

  const presetsForOs = useMemo(
    () => presets.filter((preset) => preset.os === form.os),
    [presets, form.os],
  )

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  /**
   * A key we send is authoritative and a null clears the value, so a blank
   * optional field has to mean different things in the two modes: on create it
   * means "let the backend generate this", so the key is omitted; on edit the
   * user is looking at the stored value and blanking it means "remove it", so
   * an explicit null goes out. Sending null on create produced profiles with a
   * missing timezone and geolocation — a fingerprint with holes in it.
   */
  function buildBrowserSettings() {
    const languages = form.languages
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

    const settings: Record<string, unknown> = {
      os: form.os,
      window_width: Number(form.windowWidth) || 1280,
      window_height: Number(form.windowHeight) || 720,
      webrtc_mode: form.webrtcMode,
      stable_canvas: form.stableCanvas,
      // Always explicit: "from the proxy IP" must clear any stored coordinates,
      // otherwise geoip stays disabled and the old position keeps applying.
      geolocation:
        form.geoMode === 'manual' && form.latitude && form.longitude
          ? { lat: Number(form.latitude), lon: Number(form.longitude) }
          : null,
    }

    const optional: [string, unknown][] = [
      ['timezone', form.timezone.trim() || null],
      ['languages', languages.length ? languages : null],
      ['hardware_concurrency', form.hardwareConcurrency ? Number(form.hardwareConcurrency) : null],
    ]
    for (const [key, value] of optional) {
      if (value !== null || isEdit) settings[key] = value
    }
    return settings
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

  /**
   * Checks what is on screen rather than what is stored, so the answer belongs to
   * the proxy the user is currently typing — the point is to find out before
   * saving whether it works and whether it agrees with the profile.
   */
  async function handleCheckProxy() {
    setCheckingProxy(true)
    setProxyCheck(null)
    try {
      setProxyCheck(
        await profilesAPI.checkUnsavedProxy({
          proxy_config: buildProxy(),
          browser_settings: buildBrowserSettings(),
        }),
      )
    } catch (error) {
      toast('error', 'Could not check the proxy', (error as Error).message)
    } finally {
      setCheckingProxy(false)
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
        if (presetId) payload.fingerprint_preset = presetId
        await profilesAPI.createProfile(payload)
        toast(
          'ok',
          'Profile created',
          presetId ? `${form.name.trim()} · pinned to a real device` : form.name.trim(),
        )
      }
      onSaved()
      onClose()
    } catch (err) {
      toast('error', isEdit ? 'Could not update profile' : 'Could not create profile', String(err))
    } finally {
      setSaving(false)
    }
  }

  async function handleRefreshBrowser() {
    if (!profile) return
    setRefreshingBrowser(true)
    try {
      const updated = await profilesAPI.refreshBrowserVersion(profile.id)
      setMachine(updated.fingerprint)
      toast(
        'ok',
        `Browser updated to Firefox ${updated.fingerprint?.browser_major}`,
        'The machine is unchanged.',
      )
      onSaved()
    } catch (err) {
      toast('error', 'Could not update the browser version', String(err))
    } finally {
      setRefreshingBrowser(false)
    }
  }

  /**
   * Keeping the machine only moves the dropdown; keeping the setting replaces the
   * hardware, so the two are reported very differently even though one call does
   * both. The form's own OS field follows, or it would still show the value the
   * user has just resolved away from.
   */
  async function handleReconcileOs(keepMachine: boolean) {
    if (!profile) return
    setReconciling(true)
    try {
      const updated = await profilesAPI.reconcileOs(profile.id, keepMachine)
      setMachine(updated.fingerprint)
      set('os', updated.browser_settings.os)
      toast(
        'ok',
        keepMachine
          ? `Set back to ${OS_LABELS[updated.browser_settings.os] ?? updated.browser_settings.os}`
          : 'New machine pinned',
        keepMachine
          ? 'The machine is untouched.'
          : `${updated.fingerprint?.screen} · ${updated.fingerprint?.hardware_concurrency} cores. The old hardware is gone.`,
      )
      onSaved()
    } catch (err) {
      toast('error', 'Could not reconcile the operating system', String(err))
    } finally {
      setReconciling(false)
    }
  }

  async function handleClearGeography() {
    if (!profile) return
    setClearingGeography(true)
    try {
      await profilesAPI.clearGeography([profile.id])
      setForm((current) => ({ ...current, timezone: '', geoMode: 'auto', latitude: '', longitude: '' }))
      setStoredGeography(false)
      toast('ok', 'Timezone and coordinates cleared', 'Both now follow the proxy.')
      onSaved()
    } catch (err) {
      toast('error', 'Could not clear the geography', String(err))
    } finally {
      setClearingGeography(false)
    }
  }

  async function handleRegenerate() {
    if (!profile) return
    setRegenerating(true)
    try {
      const updated = await profilesAPI.resetFingerprint(profile.id)
      setForm(fromProfile(updated))
      setMachine(updated.fingerprint)
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
            {/* Once a machine is pinned this setting is not what a page sees, so
                the honest warning is stronger than "the rest stays as it was". */}
            {isEdit && form.os !== (profile.browser_settings?.os ?? 'windows') && (
              <p className="mt-1.5 text-ink-faint">
                {machine?.pinned_os
                  ? `Saving this changes nothing a page can see: the pinned machine reports ${
                      OS_LABELS[machine.pinned_os] ?? machine.pinned_os
                    } and keeps doing so. The Machine panel then offers both ways out.`
                  : 'Screen size, locale and fonts stay as they were. Use Regenerate fingerprint for a set that matches the new OS.'}
              </p>
            )}
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

          {/* Firefox refuses SOCKS credentials outright, so the browser would
              fail to start rather than fall back — say so before that happens. */}
          {form.proxyType.startsWith('socks') &&
            (form.proxyUsername.trim() || form.proxyPassword.trim()) && (
              <p className="col-span-2 text-danger">
                Firefox cannot authenticate to a SOCKS proxy, so this profile will fail to launch.
                Use an HTTP or HTTPS proxy for credentials, or a SOCKS proxy that allows this IP
                without them.
              </p>
            )}

          <div className="col-span-2">
            <button
              type="button"
              className="btn"
              onClick={handleCheckProxy}
              disabled={checkingProxy}
            >
              {checkingProxy ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <RefreshCw size={13} />
              )}
              {checkingProxy ? 'Checking…' : 'Check proxy'}
            </button>
            <ProxyCheckResult result={proxyCheck} />
          </div>
        </Section>

        {isEdit ? (
          <PinnedMachine
            fingerprint={machine}
            onRefreshBrowser={handleRefreshBrowser}
            refreshing={refreshingBrowser}
            onReconcileOs={handleReconcileOs}
            reconciling={reconciling}
          />
        ) : (
          <fieldset>
            <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
              Machine
            </legend>
            <p className="mb-2.5 text-ink-faint">
              A generated fingerprint is internally consistent; a preset is a combination that
              genuinely exists. Either way the profile keeps it for good.
            </p>
            <select
              className="field"
              value={presetId}
              onChange={(event) => setPresetId(event.target.value)}
              aria-label="Device preset"
            >
              <option value="">Generate one automatically</option>
              {presetsForOs.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {[preset.screen, `${preset.hardware_concurrency} cores`, shortGpu(preset.gpu)]
                    .filter(Boolean)
                    .join(' · ')}
                </option>
              ))}
            </select>
            {presetsForOs.length > 0 && (
              <p className="mt-1.5 text-ink-faint">
                {presetsForOs.length} real {form.os} devices available.
              </p>
            )}
          </fieldset>
        )}

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

          <div className="col-span-2">
            <label className="field-label" htmlFor="pf-canvas">
              Canvas
            </label>
            <select
              id="pf-canvas"
              className="field"
              value={form.stableCanvas ? 'stable' : 'randomised'}
              onChange={(e) => set('stableCanvas', e.target.value === 'stable')}
            >
              <option value="randomised">Randomised each session (default)</option>
              <option value="stable">Same canvas every launch</option>
            </select>
            <p className="mt-1.5 text-ink-faint">
              {form.stableCanvas
                ? 'Reads the same to a site across launches, like real hardware — but also the same on every site, so sites can link this profile between them.'
                : "A site sees a different canvas each session, and a different one per site. Safer against tracking, but a long-lived account looks like new hardware every visit."}
            </p>
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

          {/* Until recently every new profile was given the timezone and
              coordinates of a randomly chosen region, so an old one can claim
              Shanghai on a German proxy. Nothing recorded which values were
              chosen and which were rolled, so this is offered whenever both are
              set rather than guessed at. */}
          {isEdit && storedGeography && (
            <div className="col-span-2 rounded-md border border-line bg-raised p-2.5">
              <p className="text-ink">
                This profile states where it is instead of taking it from its proxy.
              </p>
              <p className="mt-0.5 text-ink-faint">
                A profile created today leaves both unset, so Camoufox derives the timezone, the
                coordinates and the WebRTC address from the exit address. Profiles created earlier
                were given a randomly chosen region, and nothing records which values were a
                choice — so clear them if this one was not.
              </p>
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  className="btn btn-default"
                  onClick={handleClearGeography}
                  disabled={clearingGeography}
                >
                  {clearingGeography && <Loader2 size={13} className="animate-spin" />}
                  Clear both, follow the proxy
                </button>
              </div>
            </div>
          )}
        </Section>
      </form>
    </Modal>
  )
}

/**
 * Reduce a renderer string to the card name.
 *
 *   "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0)" -> "NVIDIA GeForce GTX 980"
 *   "Apple M1, or similar"                                           -> "Apple M1"
 *
 * Unwrapping ANGLE first matters: vendor names contain their own brackets
 * ("Intel(R) HD Graphics"), so cutting at the first one truncates the name.
 */
function shortGpu(gpu?: string | null): string {
  if (!gpu) return ''
  // Drop Camoufox's ", or similar" suffix before unwrapping, or the closing
  // bracket of the ANGLE wrapper is no longer at the end of the string.
  const base = gpu.replace(/,\s*or similar\s*$/i, '')
  const angle = base.match(/^ANGLE \([^,]+,\s*(.*)\)$/)
  const name = angle ? angle[1] : base
  return name.split(/\s+Direct3D|\s+vs_/)[0].trim()
}

/**
 * The machine a profile is pinned to. Without this the profile would look like
 * different hardware every session, so it is worth showing that it does not.
 */
function PinnedMachine({
  fingerprint,
  onRefreshBrowser,
  refreshing,
  onReconcileOs,
  reconciling,
}: {
  fingerprint?: FingerprintSummary | null
  onRefreshBrowser: () => void
  refreshing: boolean
  onReconcileOs: (keepMachine: boolean) => void
  reconciling: boolean
}) {
  if (!fingerprint) {
    return (
      <fieldset>
        <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Machine
        </legend>
        <p className="text-ink-faint">
          Assigned on the first launch, then reused every time so this profile stays the same
          computer.
        </p>
      </fieldset>
    )
  }

  const rows: [string, string | number | null | undefined][] = [
    ['Browser', fingerprint.browser_major ? `Firefox ${fingerprint.browser_major}` : null],
    ['Screen', fingerprint.screen],
    ['CPU cores', fingerprint.hardware_concurrency],
    ['GPU', fingerprint.gpu],
    ['Fonts', fingerprint.font_count],
    ['User agent', fingerprint.user_agent],
  ]

  return (
    <fieldset>
      <legend className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        Machine
      </legend>
      <p className="mb-2.5 text-ink-faint">
        Pinned across launches — {fingerprint.property_count} properties. Regenerate the
        fingerprint to move this profile to different hardware.
      </p>
      <div className="panel divide-y divide-line">
        {rows
          .filter(([, value]) => value !== null && value !== undefined && value !== '')
          .map(([label, value]) => (
            <div key={label} className="flex gap-3 px-3 py-1.5">
              <span className="w-[92px] shrink-0 text-ink-dim">{label}</span>
              <span className="min-w-0 flex-1 break-all font-mono text-ink">{value}</span>
            </div>
          ))}
      </div>

      {/* A pin never ages by itself. A profile kept for months keeps claiming the
          browser it was created with, and a version well behind is itself odd —
          so offer the update a real machine would have taken. */}
      {fingerprint.browser_outdated && (
        <div className="mt-2 flex items-start gap-2.5 rounded-md border border-line bg-raised p-2.5">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-signal" />
          <div className="flex-1">
            <p className="text-ink">
              This profile still reports Firefox {fingerprint.browser_major}; the installed browser
              is {fingerprint.installed_major}.
            </p>
            <p className="mt-0.5 text-ink-faint">
              Updating changes only the browser version. The screen, GPU, cores, fonts and canvas
              stay exactly as they are — the same computer, with its browser updated.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-default shrink-0"
            onClick={onRefreshBrowser}
            disabled={refreshing}
          >
            {refreshing && <Loader2 size={13} className="animate-spin" />}
            Update
          </button>
        </div>
      )}

      {/* The OS dropdown can be changed long after the machine was pinned, and
          nothing stops it — the pin is what a page sees, so the setting simply
          stops meaning anything. The two ways out cost very different amounts,
          so neither is preselected. */}
      {fingerprint.os_mismatch && (
        <div className="mt-2 rounded-md border border-line bg-raised p-2.5">
          <div className="flex items-start gap-2.5">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
            <div>
              <p className="text-ink">
                This profile is set to {osLabel(fingerprint.settings_os)}, but its pinned machine is
                a {osLabel(fingerprint.pinned_os)} one — and the machine is what every page sees.
              </p>
              <p className="mt-0.5 text-ink-faint">
                Keeping the machine puts the setting back to {osLabel(fingerprint.pinned_os)} and
                changes no fingerprint at all. Pinning a{' '}
                {osLabel(fingerprint.settings_os)} machine instead gives this profile different
                hardware — screen, GPU, cores, fonts and canvas — which any account already warmed
                up on the old one will notice.
              </p>
            </div>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              className="btn btn-default"
              onClick={() => onReconcileOs(true)}
              disabled={reconciling}
            >
              {reconciling && <Loader2 size={13} className="animate-spin" />}
              Keep this machine
            </button>
            <button
              type="button"
              className="btn btn-default"
              onClick={() => onReconcileOs(false)}
              disabled={reconciling}
            >
              New {osLabel(fingerprint.settings_os)} machine
            </button>
          </div>
        </div>
      )}
    </fieldset>
  )
}

function osLabel(os?: string | null): string {
  return (os && OS_LABELS[os]) || os || 'unknown'
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


/**
 * What the check found: where the proxy actually comes out, and everything a page
 * could notice between that and the profile. A working proxy is not the whole
 * answer — a profile whose timezone contradicts its exit address is detectable
 * however healthy the connection is.
 *
 * Three levels, three treatments: red for something that stops the browser
 * launching, amber for something a page can detect, grey for a note.
 */
function ProxyCheckResult({ result }: { result: ProxyCheck | null }) {
  if (!result) return null

  if (!result.reachable) {
    return (
      <p className="mt-2 flex items-start gap-1.5 text-danger">
        <X size={13} className="mt-0.5 shrink-0" />
        <span>{result.error}</span>
      </p>
    )
  }

  const where = result.location
  const place = [where?.country, where?.timezone].filter(Boolean).join(' · ')

  return (
    <div className="mt-2 space-y-1.5">
      <p className="flex items-start gap-1.5 text-ink-muted">
        <Check size={13} className="mt-0.5 shrink-0 text-ok" />
        <span>
          Exits at <span className="font-mono">{where?.ip}</span>
          {place && <> — {place}</>}
          {result.latency_ms !== null && <> · {result.latency_ms} ms</>}
        </span>
      </p>
      {result.findings.map((finding, index) => (
        <p
          key={index}
          className={`flex items-start gap-1.5 ${
            finding.level === 'error'
              ? 'text-danger'
              : finding.level === 'warning'
                ? 'text-warn'
                : 'text-ink-faint'
          }`}
        >
          {finding.level === 'info' ? (
            <Info size={13} className="mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          )}
          <span>{finding.message}</span>
        </p>
      ))}
    </div>
  )
}
