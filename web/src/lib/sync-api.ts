import { API_BASE_URL, ApiRequestError } from './api'

export type SyncEnrollment = {
  phase: 'not_enrolled' | 'preparing' | 'recovery_required' | 'ready' | 'freshness_unknown'
  owner_id?: string
  device_id?: string
  device_label?: string
  relay_url?: string
  vault_kind?: 'native' | 'encrypted_file'
  authority?: boolean
  membership_epoch?: number
}
export type RecoveryPackage = { package: string; secret: string }

/** Sync owner ceremonies deliberately use the session cookie without legacy API keys. */
export async function syncRequest<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api/v1/sync${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    let message = 'Synchronization request failed. Check your administrator session and try again.'
    try {
      const error = await response.json()
      const detail = error.error?.message ?? error.detail
      if (typeof detail === 'string' && detail.length <= 512) message = detail
    } catch { /* Keep a credential-free fallback for non-JSON responses. */ }
    throw new ApiRequestError(message, response.status)
  }
  return response.json() as Promise<T>
}

export const syncAPI = {
  status: () => syncRequest<SyncEnrollment>('/status'),
  enroll: (body: { relay_url: string; device_label: string; vault_kind: 'native' | 'encrypted_file'; password?: string }) =>
    syncRequest<SyncEnrollment>('/enrollment', body),
  recoveryPackage: (password?: string) => syncRequest<RecoveryPackage>('/recovery-package', { password }),
  confirmRecovery: (recovery: RecoveryPackage, password?: string) =>
    syncRequest<SyncEnrollment>('/confirm-recovery', { ...recovery, password }),
  abandon: () => syncRequest<unknown>('/abandon', {}),
}

export type OwnerCode = { owner_id: string; relay_url: string; [key: string]: unknown }
export type PairingRequest = { invitation_id: string; [key: string]: unknown }
export type PairingApproval = { request_digest: string; [key: string]: unknown }
export type SyncDevice = { device_id: string; signing_public?: string; encryption_public?: string }
export type PairingDraft = { root: OwnerCode; request: PairingRequest; fingerprint: string; phase: 'preparing' | 'pending' | 'consumed' | 'abandoned'; device_label: string; vault_kind: 'native' | 'encrypted_file' }
export type VaultChoice = 'native' | 'encrypted_file'

export const deviceSyncAPI = {
  draft: () => syncRequest<PairingDraft | null>('/pairing/draft'),
  resume: (password?: string) => syncRequest<{ request: PairingRequest; fingerprint: string; phase: string }>('/pairing/resume', { password }),
  abandon: (password?: string) => syncRequest<unknown>('/pairing/abandon', { password }),
  pendingMembership: () => syncRequest<{ kind: string; phase: string } | null>('/membership/pending'),
  resumeMembership: (password?: string) => syncRequest<{ warning?: string }>('/membership/resume', { password }),
  ownerCode: () => syncRequest<OwnerCode>('/pairing/owner-code'),
  prepare: (body: { root: OwnerCode; device_label: string; vault_kind: VaultChoice; password?: string }) =>
    syncRequest<{ request: PairingRequest; fingerprint: string; phase: string }>('/pairing/prepare', body),
  approve: (body: { request: PairingRequest; confirmed_fingerprint: string; password?: string }) =>
    syncRequest<PairingApproval>('/pairing/approve', body),
  accept: (body: { approval: PairingApproval; confirmed_fingerprint: string; password?: string }) =>
    syncRequest<SyncEnrollment>('/pairing/accept', body),
  devices: () => syncRequest<{ devices: SyncDevice[] }>('/devices'),
  revoke: (device_id: string, password?: string) => syncRequest<{ membership_epoch: number; revoked_device_id: string; checkpoint_scan?: { attempted: number; anchored: number; unavailable: number }; warning?: string }>('/devices/revoke', { device_id, password }),
  connect: (registration_token: string, password?: string) => syncRequest<unknown>('/relay/connect', { registration_token, password }),
}

const CODE_LIMIT = 512 * 1024
export function connectionCode(kind: 'owner' | 'request' | 'approval' | 'profiles', value: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(value))
  if (bytes.length > CODE_LIMIT) throw new Error('Connection code is too large.')
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return `CPM-SYNC-1.${kind}.${btoa(binary)}`
}

export function readConnectionCode<T>(kind: 'owner' | 'request' | 'approval' | 'profiles', input: string): T {
  const code = input.trim()
  const prefix = `CPM-SYNC-1.${kind}.`
  if (!code.startsWith(prefix) || code.length > CODE_LIMIT * 2) throw new Error('Paste the matching connection code from your other device.')
  try {
    const encoded = code.slice(prefix.length)
    const binary = atob(encoded)
    if (btoa(binary) !== encoded || binary.length > CODE_LIMIT) throw new Error()
    const value: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(binary, char => char.charCodeAt(0))))
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error()
    return value as T
  } catch { throw new Error('Connection code is invalid. Copy the complete code again.') }
}

export type SyncCheckpoint = { profile_id: string; holder_device_id: string | null; revision_id: string | null; checkout_generation: number; membership_epoch: number }
export type ProfileSyncBinding = { local_id: string; name?: string; local_trashed?: boolean; lifecycle?: { state: 'active' | 'trashed' | 'unknown'; revision_id: string | null; pending?: boolean; operation?: ProfileLifecycleAction }; phase: 'held' | 'released' | 'held_elsewhere' | 'apply_required' | 'unknown' | 'pending' | 'handoff' | 'conflict' | 'revoked'; checkpoint: SyncCheckpoint; installed_revision: string | null; needs_snapshot: boolean; forked?: boolean; pending?: { kind: string; operation_id: string } | null }
export type ProfileSyncListing = { profiles: ProfileSyncBinding[]; available: { profile_id: string; checkpoint: SyncCheckpoint }[] }
export type ProfileSyncAction = 'enable' | 'handoff' | 'acquire' | 'retry' | 'recover'
export type ProfileLifecycleAction = 'trash' | 'apply-trash' | 'restore'
export const profileSyncAPI = {
  lifecycle: (id: string, action: ProfileLifecycleAction, password?: string) => syncRequest<ProfileSyncBinding>(`/profiles/${encodeURIComponent(id)}/${action}`, { ...(action === 'restore' ? { acknowledge_restore: true } : { acknowledge_deletion: true }), password }),
  takeover: (id: string, password?: string) => syncRequest<ProfileSyncBinding>(`/profiles/${encodeURIComponent(id)}/takeover`, { acknowledge_lost_device: true, password }),
  share: (target_device_id: string, password?: string) => syncRequest<Record<string, unknown>>('/profiles/share', { target_device_id, password }),
  acceptCode: (pack: Record<string, unknown>) => syncRequest<unknown>('/profiles/accept-code', { pack }),
  preserveCopy: (id: string) => syncRequest<{local_id: string; name: string}>(`/profiles/${encodeURIComponent(id)}/preserve-copy`, { acknowledge_fork: true }),
  list: () => syncRequest<ProfileSyncListing>('/profiles'),
  action: (id: string, action: ProfileSyncAction, password?: string) => syncRequest<ProfileSyncBinding>(`/profiles/${encodeURIComponent(id)}/${action}`, { password }),
  import: (profile_id: string, password?: string) => syncRequest<ProfileSyncBinding>('/profiles/import', { profile_id, password }),
}

export type RetentionRevision = { revision_id: string; profile_id: string; manifest_sha256: string }
export type RetentionStatus = { decision: { body: { decision_id: string; not_before: number; revisions: RetentionRevision[] }; signature: string }; state: 'scheduled' | 'cancelled' | 'collecting' | 'collected' | 'unconfirmed' | 'cancellation_pending' }
export type RetentionListing = { revisions: RetentionRevision[]; decisions: RetentionStatus[]; grace_seconds: number }
export const retentionAPI = {
  list: (id: string, password?: string) => syncRequest<RetentionListing>(`/profiles/${encodeURIComponent(id)}/retention/list`, { password }),
  schedule: (id: string, revisions: string[], password?: string) => syncRequest<RetentionStatus>(`/profiles/${encodeURIComponent(id)}/retention/schedule`, { revisions, acknowledge_cleanup: true, password }),
  cancel: (id: string, decision_id: string, password?: string) => syncRequest<RetentionStatus>(`/profiles/${encodeURIComponent(id)}/retention/cancel`, { decision_id, password }),
}
