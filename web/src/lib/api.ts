// API client for the Camoufox Profile Manager backend.
//
// The UI is served by the API on the same origin, so the base URL is empty by
// default; NEXT_PUBLIC_API_URL overrides it for split deployments.

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface BrowserSettings {
  os: string
  screen: string
  user_agent?: string | null
  languages?: string[]
  timezone?: string | null
  locale?: string | null
  window_width?: number | null
  window_height?: number | null
  geolocation?: {
    lat?: number
    lon?: number
    latitude?: number
    longitude?: number
    accuracy?: number
  } | null
  hardware_concurrency?: number | null
  device_memory?: number | null
  max_touch_points?: number
  webrtc_mode?: string
  /** Keep the canvas reproducible across launches, at the cost of cross-site unlinkability. */
  stable_canvas?: boolean
  canvas_noise?: boolean
  webgl_noise?: boolean
  audio_noise?: boolean
}

export interface ProxyConfig {
  type?: string
  server?: string
  username?: string | null
  password?: string | null
  country?: string | null
}

/** A short description of the machine a profile is pinned to. */
export interface FingerprintSummary {
  user_agent?: string | null
  platform?: string | null
  hardware_concurrency?: number | null
  screen?: string | null
  gpu?: string | null
  font_count?: number | null
  property_count?: number
  /** Firefox major version this pin claims. */
  browser_major?: number | null
  /** Firefox major version of the browser on disk. */
  installed_major?: number | null
  /** The pin claims an older browser than the one installed. */
  browser_outdated?: boolean
}

export interface Profile {
  id: string
  name: string
  group?: string | null
  status: string
  browser_settings: BrowserSettings
  proxy_config?: ProxyConfig | null
  proxy?: ProxyConfig | null
  storage_path?: string | null
  notes?: string | null
  created_at: string
  updated_at?: string
  last_used?: string | null
  fingerprint?: FingerprintSummary | null
}

export interface ProfilesResponse {
  profiles: Profile[]
  total: number
  page: number
  per_page: number
  has_next: boolean
  has_prev: boolean
}

export interface Group {
  id: string
  name: string
  description?: string | null
  created_at: string
  profile_count: number
}

export interface SystemStatus {
  total_profiles: number
  active_profiles: number
  running_browsers: number
  total_groups: number
  system_load: number
  memory_usage: number
  disk_usage: number
  uptime_seconds: number
}

const API_KEY_STORAGE = 'camoufox-pm.api-key'

/** The key CPM_API_KEY expects, if the user has stored one. */
export function getApiKey(): string {
  if (typeof window === 'undefined') return ''
  return window.localStorage.getItem(API_KEY_STORAGE) ?? ''
}

export function setApiKey(key: string): void {
  if (typeof window === 'undefined') return
  if (key) window.localStorage.setItem(API_KEY_STORAGE, key)
  else window.localStorage.removeItem(API_KEY_STORAGE)
}

export function authHeaders(): Record<string, string> {
  const key = getApiKey()
  return key ? { 'X-API-Key': key } : {}
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    // Spread options first: putting it last let a caller's `headers` replace the
    // merged object, silently dropping Content-Type and the API key.
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(options?.headers ?? {}),
    },
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      // FastAPI validation errors arrive as a list of objects; flatten them so
      // the toast shows something readable instead of "[object Object]".
      const raw = body.detail ?? body.message ?? detail
      detail = Array.isArray(raw)
        ? raw.map((item) => item?.msg ?? JSON.stringify(item)).join('; ')
        : typeof raw === 'string'
          ? raw
          : JSON.stringify(raw)
    } catch {
      // response had no JSON body
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function toQuery(params: Record<string, unknown>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  }
  return query.toString() ? `?${query.toString()}` : ''
}

export interface ProxyLocation {
  ip: string
  country: string | null
  timezone: string | null
  latitude: number | null
  longitude: number | null
}

export interface ProxyFinding {
  level: 'error' | 'warning' | 'info'
  field: string
  message: string
}

export interface ProxyCheck {
  reachable: boolean
  error: string | null
  latency_ms: number | null
  location: ProxyLocation | null
  findings: ProxyFinding[]
}

export const profilesAPI = {
  getProfiles(params: Record<string, unknown> = {}): Promise<ProfilesResponse> {
    return request<ProfilesResponse>(`/api/profiles${toQuery(params)}`)
  },

  createProfile(data: Record<string, unknown>): Promise<Profile> {
    return request<Profile>('/api/profiles', { method: 'POST', body: JSON.stringify(data) })
  },

  updateProfile(id: string, data: Record<string, unknown>): Promise<Profile> {
    return request<Profile>(`/api/profiles/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  deleteProfile(id: string): Promise<void> {
    return request<void>(`/api/profiles/${id}`, { method: 'DELETE' })
  },

  cloneProfile(id: string, newName: string): Promise<Profile> {
    return request<Profile>(`/api/profiles/${id}/clone`, {
      method: 'POST',
      body: JSON.stringify({ new_name: newName }),
    })
  },

  startProfile(id: string): Promise<unknown> {
    return request<unknown>(`/api/profiles/${id}/launch`, {
      method: 'POST',
      body: JSON.stringify({ headless: false }),
    })
  },

  closeProfile(id: string): Promise<unknown> {
    return request<unknown>(`/api/profiles/${id}/close`, { method: 'POST' })
  },

  resetFingerprint(id: string): Promise<Profile> {
    return request<Profile>(`/api/profiles/${id}/reset-fingerprint`, { method: 'POST' })
  },

  refreshBrowserVersion(id: string): Promise<Profile> {
    return request<Profile>(`/api/profiles/${id}/refresh-browser`, { method: 'POST' })
  },

  checkProxy(id: string): Promise<ProxyCheck> {
    return request<ProxyCheck>(`/api/profiles/${id}/check-proxy`, { method: 'POST' })
  },

  // The same check for a profile that is still being filled in, so the form can
  // answer before anything is saved.
  checkUnsavedProxy(body: {
    proxy_config: Record<string, unknown> | null
    browser_settings: Record<string, unknown>
  }): Promise<ProxyCheck> {
    return request<ProxyCheck>('/api/proxy/check', { method: 'POST', body: JSON.stringify(body) })
  },

  async exportExcel(): Promise<Blob> {
    const response = await fetch(`${API_BASE_URL}/api/profiles/export/excel`, {
      headers: authHeaders(),
    })
    if (!response.ok) throw new Error(response.statusText)
    return response.blob()
  },

  async exportArchive(id: string): Promise<Blob> {
    const response = await fetch(`${API_BASE_URL}/api/profiles/${id}/export`, {
      headers: authHeaders(),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(body?.detail ?? response.statusText)
    }
    return response.blob()
  },

  async importArchive(file: File): Promise<Profile> {
    const body = new FormData()
    body.append('file', file)
    // No Content-Type: the browser has to set the multipart boundary itself.
    const response = await fetch(`${API_BASE_URL}/api/profiles/import`, {
      method: 'POST',
      headers: authHeaders(),
      body,
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => null)
      throw new Error(detail?.detail ?? response.statusText)
    }
    return response.json() as Promise<Profile>
  },

  async importExcel(file: File): Promise<ImportResult> {
    const body = new FormData()
    body.append('file', file)
    // No Content-Type: the browser has to set the multipart boundary itself.
    const response = await fetch(`${API_BASE_URL}/api/profiles/import/excel`, {
      method: 'POST',
      headers: authHeaders(),
      body,
    })
    return response.json() as Promise<ImportResult>
  },
}

export interface ImportResult {
  success: boolean
  message: string
  data?: { created_count?: number; errors?: string[] }
}

export const browsersAPI = {
  active(): Promise<{ active_browsers: { profile_id: string }[]; count: number }> {
    return request('/api/browsers/active')
  },

  closeAll(): Promise<{ closed_count: number; message: string }> {
    return request('/api/browsers/close-all', { method: 'POST' })
  },
}

export const groupsAPI = {
  list(): Promise<{ groups: Group[]; total: number }> {
    return request('/api/groups')
  },

  create(data: { name: string; description?: string }): Promise<Group> {
    return request<Group>('/api/groups', { method: 'POST', body: JSON.stringify(data) })
  },

  update(id: string, data: { name?: string; description?: string }): Promise<Group> {
    return request<Group>(`/api/groups/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  remove(id: string): Promise<void> {
    return request<void>(`/api/groups/${id}`, { method: 'DELETE' })
  },
}

export interface SystemConfig {
  version: string
  host: string
  port: number
  database_path: string
  api_key_set: boolean
  encryption_enabled: boolean
  cors_origins: string[]
  camoufox_available: boolean
  uptime_seconds: number
}

/** A fingerprint captured from a real machine, bundled with Camoufox. */
export interface DevicePreset {
  id: string
  os: string
  screen?: string | null
  hardware_concurrency?: number | null
  gpu?: string | null
  vendor?: string | null
  user_agent?: string | null
}

export const presetsAPI = {
  async list(os?: string): Promise<DevicePreset[]> {
    const body = await request<{ data: { presets: DevicePreset[] } }>(
      `/api/fingerprints/presets${toQuery({ os })}`,
    )
    return body.data.presets
  },
}

export const systemAPI = {
  status(): Promise<SystemStatus> {
    return request<SystemStatus>('/api/system/status')
  },

  async config(): Promise<SystemConfig> {
    const body = await request<{ data: SystemConfig }>('/api/system/config')
    return body.data
  },
}

// --- Display helpers ---------------------------------------------------------

export function formatProxyString(proxy?: ProxyConfig | null): string {
  if (!proxy || !proxy.server) return ''
  const scheme = proxy.type ? `${proxy.type}://` : ''
  return `${scheme}${proxy.server}`
}

export function formatLastUsed(value?: string | null): string {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Never'

  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  if (seconds < 60) return 'Just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`
  return date.toLocaleDateString()
}

export const OS_LABELS: Record<string, string> = {
  windows: 'Windows',
  macos: 'macOS',
  linux: 'Linux',
}
