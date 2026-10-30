'use client'

import { useEffect, useRef, useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { ApiRequestError, proxiesAPI, readProxyCheck, type LibraryProxy } from '@/lib/api'
import { BatchQueue, MAX_BATCH_CONCURRENCY, MAX_BATCH_SIZE, type BatchSnapshot, type BatchTarget, type BatchStatus } from '@/lib/batch-queue'

export function BatchProxyChecks({ targets, onClose, onChecked, onChecking }: {
  targets: BatchTarget[]
  onClose: () => void
  onChecked: (proxy: LibraryProxy) => void
  onChecking: (id: string, checking: boolean) => void
}) {
  const { t, locale } = useI18n()
  const [concurrency, setConcurrency] = useState(2)
  const [started, setStarted] = useState(false)
  const [snapshot, setSnapshot] = useState<BatchSnapshot>(() => ({ rows: targets.map(target => ({ ...target, status: 'waiting' })), running: false, accessLost: false }))
  const [results, setResults] = useState<Record<string, LibraryProxy>>({})
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

  const valid = targets.length > 0 && targets.length <= MAX_BATCH_SIZE
  const waiting = snapshot.rows.filter(row => row.status === 'waiting').length
  const completed = snapshot.rows.filter(row => !['waiting', 'running'].includes(row.status)).length
  const checks = Object.values(results).flatMap(proxy => proxy.last_check ? [proxy.last_check] : [])

  function statusLabel(status: BatchStatus) {
    switch (status) {
      case 'waiting': return t('Waiting')
      case 'running': return t('Checking…')
      case 'succeeded': return t('Completed')
      case 'failed': return t('Could not check proxy')
      case 'cancelled': return t('Cancelled')
    }
  }

  async function run() {
    if (!valid || queue.current) return
    queue.current = new BatchQueue(targets, {
      concurrency,
      execute: async id => {
        onChecking(id, true)
        try {
          const checked = await proxiesAPI.check(id)
          // In-flight checks still update the library after the dialog closes.
          onChecked(checked)
          if (mounted.current) setResults(current => ({ ...current, [id]: checked }))
        } finally { onChecking(id, false) }
      },
      onChange: setSnapshot,
      accessDenied: error => error instanceof ApiRequestError && (error.status === 401 || error.status === 403),
      // Do not retain upstream errors: they can contain proxy credentials.
      describeError: error => error instanceof ApiRequestError && error.status === 409
        ? 'This proxy changed during the check. Refresh the library before checking it again.'
        : 'Could not check proxy',
    })
    setStarted(true)
    await queue.current.run()
  }

  function close() {
    queue.current?.cancelWaiting()
    onClose()
  }

  return <Modal open title={t('Batch proxy check')} width={720} onClose={close} footer={<>
    {snapshot.running && <button className="btn btn-default" disabled={!waiting} onClick={() => queue.current?.cancelWaiting()}>{t('Cancel waiting')}</button>}
    <button className="btn btn-default" onClick={close}>{started ? t('Close') : t('Cancel')}</button>
    {!started && <button className="btn btn-primary" disabled={!valid} onClick={run}>{t('Start checks')}</button>}
  </>}>
    <p className="text-ink-dim">{t('Only the proxies listed below will be checked. Results are saved to the proxy library.')}</p>
    <p className="mt-2 text-ink-dim">{t('Closing this dialog cancels waiting checks. Checks already started may still finish.')}</p>
    {!valid && <p role="alert" className="mt-2 text-danger">{t('Select between 1 and {count} proxies for one batch.', { count: MAX_BATCH_SIZE })}</p>}
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2">
        <label htmlFor="proxy-check-concurrency">{t('Simultaneous checks')}</label>
        <select id="proxy-check-concurrency" className="field w-20" value={concurrency} disabled={started} onChange={event => setConcurrency(Number(event.target.value))}>
          {Array.from({ length: MAX_BATCH_CONCURRENCY }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}
        </select>
      </div>
      {started && <span role="status">{snapshot.running ? t('Batch in progress') : t('Batch finished')} · {t('{completed} of {total} processed', { completed, total: snapshot.rows.length })}</span>}
    </div>
    {started && <>
      <progress className="mt-3 w-full accent-signal" aria-label={t('Batch progress')} max={snapshot.rows.length} value={completed} />
      <p className="mt-2 text-ink-dim">{t('Reachable: {reachable}; unreachable: {unreachable}; check errors: {errors}; cancelled: {cancelled}.', {
        reachable: checks.filter(check => check.reachable).length,
        unreachable: checks.filter(check => !check.reachable).length,
        errors: snapshot.rows.filter(row => row.status === 'failed').length,
        cancelled: snapshot.rows.filter(row => row.status === 'cancelled').length,
      })}</p>
    </>}
    {snapshot.accessLost && <p role="alert" className="mt-2 text-danger">{t('Access changed. Remaining requests were cancelled. Reopen the queue after signing in and checking permissions.')}</p>}
    <ul className="mt-4 max-h-[320px] divide-y divide-line overflow-y-auto rounded-md border border-line" aria-label={t('Proxies and check results')}>
      {snapshot.rows.map(row => {
        const check = results[row.id]?.last_check
        const health = check ? readProxyCheck(check, locale) : null
        return <li key={row.id} className="px-3 py-2.5">
          <div className="flex flex-wrap items-start justify-between gap-2"><span className="min-w-0 break-words font-medium">{row.name}</span><span className={row.status === 'failed' || health?.tone === 'danger' ? 'text-danger' : health?.tone === 'ok' ? 'text-ok' : health?.tone === 'warn' ? 'text-warn' : 'text-ink-dim'}>{health?.label ?? statusLabel(row.status)}</span></div>
          {check && <p className="mt-1 text-ink-dim">{check.reachable ? t('Proxy answered') : t('Proxy did not answer')}{check.ip ? ` · ${check.ip}` : ''}{check.latency_ms != null ? ` · ${check.latency_ms} ms` : ''}</p>}
          {row.error && <p className="mt-1 break-words text-danger">{t(row.error)}</p>}
        </li>
      })}
    </ul>
  </Modal>
}
