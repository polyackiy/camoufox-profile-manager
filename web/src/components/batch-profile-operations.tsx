'use client'

import { useEffect, useRef, useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { ApiRequestError, profilesAPI } from '@/lib/api'
import { BatchQueue, MAX_BATCH_CONCURRENCY, MAX_BATCH_SIZE, type BatchSnapshot, type BatchTarget, type BatchStatus } from '@/lib/batch-queue'

export function BatchProfileOperations({ action, targets, onClose, onUpdated }: {
  action: 'launch' | 'close'
  targets: BatchTarget[]
  onClose: () => void
  onUpdated: () => Promise<void>
}) {
  const { t } = useI18n()
  const [concurrency, setConcurrency] = useState(2)
  const [started, setStarted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<BatchSnapshot>(() => ({ rows: targets.map(target => ({ ...target, status: 'waiting' })), running: false, accessLost: false }))
  const queue = useRef<BatchQueue | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const denied = () => queue.current?.loseAccess()
    window.addEventListener('camoufox-access-denied', denied)
    return () => {
      mounted.current = false
      window.removeEventListener('camoufox-access-denied', denied)
      queue.current?.dispose()
    }
  }, [])

  const busy = snapshot.running
  const completed = snapshot.rows.filter(row => !['waiting', 'running'].includes(row.status)).length
  const waiting = snapshot.rows.filter(row => row.status === 'waiting').length
  const retryable = snapshot.rows.filter(row => row.status === 'failed' || row.status === 'cancelled').length
  const valid = targets.length > 0 && targets.length <= MAX_BATCH_SIZE

  function statusLabel(status: BatchStatus) {
    switch (status) {
      case 'waiting': return t('Waiting')
      case 'running': return t('In progress')
      case 'succeeded': return t('Completed')
      case 'failed': return t('Failed')
      case 'cancelled': return t('Cancelled')
    }
  }

  async function run(retry = false) {
    if (!valid || queue.current?.snapshot().running || snapshot.accessLost) return
    setError(null)
    if (!queue.current) {
      queue.current = new BatchQueue(targets, {
        concurrency,
        execute: id => action === 'launch' ? profilesAPI.startProfile(id) : profilesAPI.closeProfile(id),
        onChange: setSnapshot,
        accessDenied: error => error instanceof ApiRequestError && (error.status === 401 || error.status === 403),
        describeError: error => {
          if (error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) return t('Access changed. Sign in again and check your profile permissions.')
          if (error instanceof ApiRequestError && error.status === 409) return t('This profile is busy or owned by another device. Refresh its state before retrying.')
          return t('Operation failed. Refresh running status and inspect this profile before retrying; a request may have reached the server.')
        },
      })
    }
    setStarted(true)
    try { await queue.current.run(retry) }
    catch { if (mounted.current) setError(t('Could not start this queue. Close it and check the selected profiles again.')) }
    finally {
      if (mounted.current) {
        try { await onUpdated() }
        catch { if (mounted.current) setError(t('Refresh the profile list to see the latest browser state.')) }
      }
    }
  }

  function close() {
    queue.current?.cancelWaiting()
    onClose()
  }

  return <Modal open title={action === 'launch' ? t('Launch selected profiles') : t('Stop selected profiles')} width={720} onClose={close} footer={<>
    {busy ? <><button className="btn btn-default" disabled={!waiting} onClick={() => queue.current?.cancelWaiting()}>{t('Cancel waiting')}</button><button className="btn btn-default" onClick={close}>{t('Close')}</button></> : <button className="btn btn-default" onClick={close}>{started ? t('Done') : t('Cancel')}</button>}
    {!started && <button className={action === 'launch' ? 'btn btn-primary' : 'btn btn-danger'} disabled={!valid} onClick={() => run()}>{action === 'launch' ? targets.length === 1 ? t('Launch 1 profile') : t('Launch {count} profiles', { count: targets.length }) : targets.length === 1 ? t('Stop 1 profile') : t('Stop {count} profiles', { count: targets.length })}</button>}
    {started && !busy && retryable > 0 && !snapshot.accessLost && <button className="btn btn-primary" onClick={() => run(true)}>{t('Retry {count} incomplete', { count: retryable })}</button>}
  </>}>
    <p className="text-ink-dim">{action === 'launch' ? t('Review the exact profiles below. Already running profiles keep their current browser; access and device ownership are checked by the server.') : t('This closes the browsers of the profiles below. Save unfinished work in those browsers before continuing. Already stopped profiles stay stopped.')}</p>
    <p className="mt-2 text-ink-dim">{t('This queue runs in this view. Leaving the page or closing the manager cancels waiting requests. Requests already sent may still complete; nothing is automatically replayed after reopening.')}</p>
    {!valid && <p role="alert" className="mt-2 text-danger">{t('Select between 1 and {count} profiles for one batch.', { count: MAX_BATCH_SIZE })}</p>}
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <label className="flex items-center gap-2" htmlFor="batch-concurrency">{t('Simultaneous operations')}
        <select id="batch-concurrency" className="field w-20" value={concurrency} disabled={started} onChange={event => setConcurrency(Number(event.target.value))}>
          {Array.from({ length: MAX_BATCH_CONCURRENCY }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}
        </select>
      </label>
      {started && <span role="status">{busy ? t('Batch in progress') : t('Batch finished')} · {t('{completed} of {total} processed', { completed, total: snapshot.rows.length })}</span>}
    </div>
    {action === 'launch' && <p className="mt-2 text-ink-dim">{t('Concurrency limits simultaneous requests, not the total open browsers. Launch only as many profiles as your computer can support.')}</p>}
    {started && <progress className="mt-3 w-full accent-signal" aria-label={t('Batch progress')} max={snapshot.rows.length} value={completed} />}
    {busy && <p className="mt-2 text-ink-dim">{t('Closing this dialog cancels waiting requests. Requests already sent may still finish; check running status before retrying them.')}</p>}
    {started && <p className="mt-2 text-ink-dim">{t('{count} completed', { count: snapshot.rows.filter(row => row.status === 'succeeded').length })} · {t('{count} failed', { count: snapshot.rows.filter(row => row.status === 'failed').length })} · {t('{count} cancelled', { count: snapshot.rows.filter(row => row.status === 'cancelled').length })}</p>}
    {snapshot.accessLost && <p role="alert" className="mt-2 text-danger">{t('Access changed. Remaining requests were cancelled. Reopen the queue after signing in and checking permissions.')}</p>}
    {error && <p role="alert" className="mt-2 text-danger">{error}</p>}
    <ul className="mt-4 max-h-[320px] divide-y divide-line overflow-y-auto rounded-md border border-line" aria-label={t('Selected profiles and results')}>
      {snapshot.rows.map(row => <li key={row.id} className="px-3 py-2.5">
        <div className="flex flex-wrap items-start justify-between gap-2"><span className="min-w-0 break-words font-medium">{row.name}</span><span className={row.status === 'failed' ? 'text-danger' : row.status === 'succeeded' ? 'text-ok' : 'text-ink-dim'}>{statusLabel(row.status)}</span></div>
        <p className="font-mono text-[11px] text-ink-dim">{row.id}</p>
        {row.error && <p className="mt-1 break-words text-danger">{row.error}</p>}
      </li>)}
    </ul>
  </Modal>
}
