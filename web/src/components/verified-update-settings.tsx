'use client'

import { useCallback, useEffect, useState } from 'react'
import { Download, LoaderCircle, RotateCcw } from 'lucide-react'
import { Modal } from '@/components/modal'
import { useI18n } from '@/components/i18n-provider'
import { systemAPI, type UpdateRecovery } from '@/lib/api'

const stateLabels: Record<string, string> = {
  idle: 'No update is in progress.',
  downloading: 'Downloading and verifying the update…',
  staged: 'The verified update is ready to install.',
  handoff_requested: 'Waiting for the manager to close…',
  preparing: 'Creating the recovery point…',
  prepared: 'The recovery point is ready.',
  checkpoint_interrupted: 'Recovery point creation was interrupted.',
  installing_browser: 'Installing the paired browser…',
  browser_ready: 'The paired browser is ready.',
  replacing: 'Installing the manager update…',
  applying: 'Installing the manager update…',
  validating: 'Checking the updated manager and browser…',
  rolling_back: 'Restoring the previous version and data…',
  restoring_data: 'Restoring the previous version and data…',
  committed: 'The update completed successfully.',
  rolled_back: 'The previous version and data have been restored.',
  cancelled: 'Update preparation was cancelled.',
  failed: 'The update could not finish. Review the error and try again.',
  queued: 'The update is waiting to start.',
}
const workingStates = new Set(['downloading', 'queued', 'preparing', 'installing_browser', 'replacing', 'applying', 'validating', 'rolling_back', 'restoring_data'])
type Confirmation = { kind: 'apply' | 'rollback'; transactionId: string; timestamp: string | null }

function validTimestamp(value?: string | null): value is string {
  return !!value && Number.isFinite(new Date(value).getTime())
}

/** Only server-approved actions are offered; trust configuration never comes from the UI. */
export function VerifiedUpdateSettings({ preview }: { preview: boolean }) {
  const { t, locale } = useI18n()
  const [status, setStatus] = useState<UpdateRecovery | null>(null)
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [handoff, setHandoff] = useState(false)
  const refresh = useCallback(async () => {
    setRefreshing(true)
    try {
      const next = await systemAPI.updateRecovery()
      setStatus(next); setError(null)
      if (next.error) setHandoff(false)
    }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setRefreshing(false) }
  }, [])
  useEffect(() => {
    const timer = window.setTimeout(() => { void refresh() }, 0)
    return () => window.clearTimeout(timer)
  }, [refresh])
  const working = !!status && workingStates.has(status.state)
  const pending = handoff || (!!status?.request_pending && !status.error)
  useEffect(() => {
    if (!working || pending) return
    const timer = window.setInterval(() => { void refresh() }, 2500)
    return () => window.clearInterval(timer)
  }, [working, pending, refresh])
  const recoveryTimestamp = validTimestamp(status?.checkpoint_timestamp) ? status.checkpoint_timestamp : null
  const rollbackTransactionId = status?.rollback_transaction_id || status?.transaction_id
  const confirmationMatches = !!confirmation && confirmation.transactionId === (confirmation.kind === 'rollback' ? rollbackTransactionId : status?.transaction_id) &&
    (confirmation.kind === 'rollback' ? !!status?.can_rollback && confirmation.timestamp === recoveryTimestamp : !!status?.can_apply)

  async function action(kind: 'download' | 'apply' | 'rollback' | 'resume' | 'cancel') {
    if (busy || pending) return
    if ((kind === 'apply' || kind === 'rollback') && (!acknowledged || !confirmationMatches)) return
    setBusy(true); setError(null)
    try {
      const next = kind === 'download'
        ? await systemAPI.downloadVerifiedUpdate(preview)
        : kind === 'cancel'
          ? await systemAPI.cancelUpdatePreparation()
          : await systemAPI.requestUpdateAction(kind, confirmation?.transactionId ?? status?.transaction_id ?? '', kind === 'rollback' ? confirmation?.timestamp : undefined)
      setStatus(next)
      if (kind !== 'download' && kind !== 'cancel') setHandoff(true)
      setConfirmation(null); setAcknowledged(false)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }

  function confirm(kind: 'apply' | 'rollback') {
    const transactionId = kind === 'rollback' ? rollbackTransactionId : status?.transaction_id
    if (!transactionId || (kind === 'rollback' && !recoveryTimestamp)) return
    setError(null); setAcknowledged(false)
    setConfirmation({ kind, transactionId, timestamp: kind === 'rollback' ? recoveryTimestamp : null })
  }
  const failure = error || status?.error
  const rollback = confirmation?.kind === 'rollback'

  return <div className="mt-4 border-t border-line pt-4" aria-labelledby="verified-update-heading" aria-busy={busy}>
    <h3 id="verified-update-heading" className="font-medium">{t('Verified update and rollback')}</h3>
    {!status && !error && <p className="mt-2 text-ink-dim" role="status">{t('Loading update status…')}</p>}
    {status?.disabled_reason && <p className="mt-2 break-words text-ink-dim">{t('Verified installation is unavailable. Use the release downloads above.')} {t(status.disabled_reason)}</p>}
    {status && <div className="mt-3 space-y-2" role="status" aria-live="polite">
      <p>{t(stateLabels[status.state] ?? 'Update status is unavailable. Refresh to try again.')}</p>
      <p className="text-ink-dim">{status.maintenance_active ? t('Update maintenance is active. Profile changes and launches are paused.') : t('No update maintenance is active.')}</p>
      {status.target_version && <p>{t('Target manager: {manager}; Camoufox: {browser}.', { manager: status.target_version, browser: status.browser_version ?? '—' })}</p>}
      {recoveryTimestamp && <p>{t('Recovery point: {date}', { date: new Date(recoveryTimestamp).toLocaleString(locale) })}</p>}
      {pending && <p>{t('The manager is closing for the update. Recovery will remain available if the update cannot finish.')}</p>}
      {status.can_cancel && <p className="text-ink-dim">{t('Cancel preparation to resume normal work. No installed application or profile data will be replaced.')}</p>}
      {status.maintenance_active && !status.can_cancel && !status.can_resume && !status.can_rollback && !pending && <p className="text-ink-dim">{t('Close the manager and use the documented offline recovery procedure before resuming work.')}</p>}
    </div>}
    {failure && !confirmation && <p className="mt-3 break-words text-danger" role="alert">{t(failure)}</p>}
    <div className="mt-3 flex flex-wrap gap-2">
      <button className="btn btn-default" disabled={busy || refreshing} onClick={refresh}>{refreshing ? t('Checking…') : t('Check recovery status')}</button>
      {status?.can_download && <button className="btn btn-primary" disabled={busy || working || pending} onClick={() => action('download')}><Download size={13} aria-hidden="true" />{t('Download verified update')}</button>}
      {status?.can_apply && <button className="btn btn-primary" disabled={busy || pending || !status.transaction_id} onClick={() => confirm('apply')}>{t('Restart and install update')}</button>}
      {status?.can_resume && <button className="btn btn-default" disabled={busy || pending || !status.transaction_id} onClick={() => action('resume')}>{t('Retry interrupted update')}</button>}
      {status?.can_rollback && recoveryTimestamp && <button className="btn btn-default" disabled={busy || pending || !rollbackTransactionId} onClick={() => confirm('rollback')}><RotateCcw size={13} aria-hidden="true" />{t('Restore previous version')}</button>}
      {status?.can_cancel && <button className="btn btn-default" disabled={busy || pending} onClick={() => action('cancel')}>{t('Cancel update preparation')}</button>}
      {busy && <span role="status" className="flex items-center gap-2 text-ink-dim"><LoaderCircle size={14} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />{t('Working…')}</span>}
    </div>
    <Modal open={!!confirmation} width={500} title={rollback ? t('Restore previous version') : t('Restart and install update')} onClose={() => { if (!busy) setConfirmation(null) }} footer={<>
      <button className="btn btn-default" disabled={busy} onClick={() => setConfirmation(null)}>{t('Cancel')}</button>
      <button className="btn btn-primary" disabled={!acknowledged || busy || !confirmationMatches || pending} onClick={() => { if (confirmation) void action(confirmation.kind) }}>{busy ? t('Working…') : rollback ? t('Confirm restore') : t('Confirm restart')}</button>
    </>}>
      <p className="text-ink-dim">{rollback
        ? t('The previous manager, browser and matching data will be restored together. Changes made after {date} will no longer be active. The newer state is retained for recovery.', { date: confirmation?.timestamp ? new Date(confirmation.timestamp).toLocaleString(locale) : '—' })
        : t('All profiles and the manager will close. A complete recovery point is created before replacing the application. Please finish your work first.')}</p>
      {confirmation && !confirmationMatches && <p className="mt-3 text-danger" role="alert">{t('The recovery state changed. Close this dialog, refresh the status and review the recovery point again.')}</p>}
      {failure && <p className="mt-3 break-words text-danger" role="alert">{t(failure)}</p>}
      <label className="mt-4 flex min-h-11 items-center gap-3"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} disabled={busy || !confirmationMatches} />{t('I understand and want to continue.')}</label>
    </Modal>
  </div>
}
