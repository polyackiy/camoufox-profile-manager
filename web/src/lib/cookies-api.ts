import { API_BASE_URL, ApiRequestError, apiRequest, authHeaders, notifyAccessDenied } from './api'

export type CookieFormat = 'json' | 'netscape'
export interface CookieSkip { row: number; reason: string }
export interface CookiePreview {
  total: number
  importable: number
  session_count: number
  skipped: CookieSkip[]
}
export interface CookieImportResult {
  imported: number
  session_count: number
  skipped: CookieSkip[]
  row_version: number
  backup_id: string | null
}
interface Envelope<T> { success: boolean; message: string; data: T }
const root = (id: string) => `/api/v1/profiles/${encodeURIComponent(id)}/cookies`

export const cookiesAPI = {
  async preview(id: string, text: string, format: CookieFormat): Promise<CookiePreview> {
    const response = await apiRequest<Envelope<CookiePreview>>(`${root(id)}/preview`, {
      method: 'POST', body: JSON.stringify({ text, format }),
    })
    return response.data
  },
  async import(id: string, text: string, format: CookieFormat, mode: 'merge' | 'replace', rowVersion: number, confirmReplace: boolean): Promise<CookieImportResult> {
    const response = await apiRequest<Envelope<CookieImportResult>>(`${root(id)}/import`, {
      method: 'POST', body: JSON.stringify({ text, format, mode, row_version: rowVersion, confirm_replace: confirmReplace }),
    })
    return response.data
  },
  async download(id: string, format: CookieFormat, rowVersion: number): Promise<Blob> {
    const response = await fetch(`${API_BASE_URL}${root(id)}/export?format=${format}&row_version=${rowVersion}`, {
      headers: authHeaders(), credentials: 'same-origin',
    })
    if (!response.ok) {
      notifyAccessDenied(response.status)
      let message = 'Could not export cookies'
      let code: string | undefined
      try {
        const body = await response.json()
        message = body.error?.message ?? message
        code = body.error?.code
      } catch { /* A non-JSON failure must not expose cookie contents. */ }
      throw new ApiRequestError(message, response.status, code)
    }
    return response.blob()
  },
}
