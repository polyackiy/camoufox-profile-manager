import { apiRequest, type ProxyAssignmentResult } from './api'

export interface ProxySequencePair {
  profile: { id: string; row_version: number }
  proxy: { id: string; row_version: number }
}
export interface ProxySequencePreview {
  profile_id: string
  profile_name: string
  current_connection: string
  proxy_id: string
  proxy_name: string
  connection: string
  replaces_proxy: boolean
  reason: string | null
}
export const proxySequenceAPI = {
  async preview(assignments: ProxySequencePair[]) {
    return (await apiRequest<{ data: { items: ProxySequencePreview[] } }>('/api/v1/proxies/assignment-preview', { method: 'POST', body: JSON.stringify({ assignments }) })).data.items
  },
  async assign(assignments: ProxySequencePair[]) {
    return (await apiRequest<{ data: ProxyAssignmentResult }>('/api/v1/proxies/assign-sequence', { method: 'POST', body: JSON.stringify({ assignments }) })).data
  },
}
