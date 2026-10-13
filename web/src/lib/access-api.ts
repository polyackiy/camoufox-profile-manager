import { apiRequest } from './api'

export type AccessRole = 'administrator' | 'operator' | 'viewer'

export interface AccessUser {
  username: string
  role: AccessRole
  created_at: string
}

export interface ProfileGrant {
  profile_id: string
  can_export: boolean
}

export interface AuditEvent {
  id: number
  actor_kind: string
  actor_username: string | null
  action: string
  object_id: string | null
  result: string
  created_at: string
}

export interface AuditFilters {
  actor: string
  action: string
  result: string
}

const base = '/api/v1/access'
const userPath = (username: string) => `${base}/users/${encodeURIComponent(username)}`

function query(filters: AuditFilters, offset?: number) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value.trim()) params.set(key, value.trim())
  })
  if (offset !== undefined) {
    params.set('offset', String(offset))
    params.set('limit', '25')
  }
  return `?${params}`
}

export const accessAPI = {
  users: () => apiRequest<{ users: AccessUser[]; total: number }>(`${base}/users`),

  create: (username: string, password: string, role: AccessRole) =>
    apiRequest(`${base}/users`, {
      method: 'POST',
      body: JSON.stringify({ username, password, role }),
    }),

  update: (username: string, data: { role?: AccessRole; password?: string }) =>
    apiRequest(userPath(username), {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  remove: (username: string) => apiRequest(userPath(username), { method: 'DELETE' }),

  grants: (username: string) =>
    apiRequest<{ grants: ProfileGrant[]; total: number }>(`${userPath(username)}/profiles`),

  grant: (username: string, id: string, can_export: boolean) =>
    apiRequest(`${userPath(username)}/profiles/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ can_export }),
    }),

  revoke: (username: string, id: string) =>
    apiRequest(`${userPath(username)}/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  audit: (filters: AuditFilters, offset: number) =>
    apiRequest<{ events: AuditEvent[]; total: number }>(`${base}/audit${query(filters, offset)}`),

  export: (filters: AuditFilters) =>
    apiRequest<{ events: AuditEvent[]; total: number }>(`${base}/audit/export${query(filters)}`),
}
