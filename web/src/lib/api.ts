// API client for the Camoufox Profile Manager backend.
//
// The base URL is read from NEXT_PUBLIC_API_URL and falls back to the local
// development server.

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? ''

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
  last_opened?: string | null
  platform?: string | null
  custom_number?: number | null
}

export interface ProfilesResponse {
  profiles: Profile[]
  total: number
  page: number
  per_page: number
  has_next: boolean
  has_prev: boolean
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) },
    ...options,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? body.message ?? detail
    } catch {
      // response had no JSON body
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const profilesAPI = {
  getProfiles(params: Record<string, unknown> = {}): Promise<ProfilesResponse> {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        query.set(key, String(value))
      }
    }
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<ProfilesResponse>(`/api/profiles${suffix}`)
  },

  updateProfile(id: string, data: Record<string, unknown>): Promise<Profile> {
    return request<Profile>(`/api/profiles/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
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

  resetFingerprint(id: string): Promise<Profile> {
    return request<Profile>(`/api/profiles/${id}/reset-fingerprint`, { method: 'POST' })
  },
}

// --- Display helpers ---------------------------------------------------------

export function formatProxyString(proxy?: ProxyConfig | null): string {
  if (!proxy || !proxy.server) return 'No proxy'
  const scheme = proxy.type ? `${proxy.type}://` : ''
  return `${scheme}${proxy.server}`
}

export function formatLastUsed(value?: string | null): string {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Never'
  return date.toLocaleString()
}

export function getStatusColor(status: string): string {
  switch (status) {
    case 'active':
      return '#22c55e'
    case 'inactive':
      return '#9ca3af'
    case 'blocked':
    case 'error':
      return '#ef4444'
    case 'maintenance':
    case 'pending':
      return '#f59e0b'
    default:
      return '#9ca3af'
  }
}
