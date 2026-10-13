'use client'

import { useState } from 'react'
import { ConfirmDialog } from './modal'
import { useI18n } from './i18n-provider'
import { retentionAPI, type RetentionListing } from '@/lib/sync-api'

export function SyncRetentionPanel({ id, password, busy, unlocked, run }: { id: string; password: string; busy: boolean; unlocked: boolean; run: (action: () => Promise<void>) => Promise<void> }) {
  const { t, locale } = useI18n()
  const [listing, setListing] = useState<RetentionListing | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [confirm, setConfirm] = useState(false)
  async function load() { setListing(await retentionAPI.list(id, password || undefined)); setSelected([]) }
  const stateLabel = { scheduled: 'Cleanup scheduled', cancelled: 'Cleanup cancelled', collecting: 'Cleanup in progress', collected: 'Historical ciphertext removed', unconfirmed: 'Cleanup status unconfirmed', cancellation_pending: 'Cancellation awaiting confirmation' }
  return <details className="rounded-md border border-line p-3">
    <summary className="cursor-pointer min-h-11 font-medium">{t('Historical storage cleanup')}</summary>
    <p className="mb-3 text-ink-dim">{t('Only the owner can remove selected old encrypted versions from the relay. The current version, signed history and local backups are kept. Export any historical recovery bundles you need before scheduling cleanup.')}</p>
    <button className="btn btn-default min-h-11" disabled={busy || !unlocked} onClick={() => run(load)}>{t('Load verified historical versions')}</button>
    {listing && <div className="mt-3 space-y-3">
      {listing.revisions.length === 0 ? <p>{t('No older verified versions are available.')}</p> : <>
        <p className="text-ink-dim">{t('Select up to 64 old versions. Cleanup waits at least seven days after the relay accepts the decision. You can cancel until cleanup begins.')}</p>
        <fieldset className="max-h-48 overflow-y-auto space-y-2"><legend className="mb-2">{t('Old encrypted versions')}</legend>{listing.revisions.map(revision => <label className="flex min-h-11 items-center gap-2 break-all font-mono text-xs" key={revision.revision_id}><input type="checkbox" checked={selected.includes(revision.revision_id)} disabled={busy || (!selected.includes(revision.revision_id) && selected.length >= 64)} onChange={event => setSelected(current => event.target.checked ? [...current, revision.revision_id] : current.filter(value => value !== revision.revision_id))} />{revision.revision_id}</label>)}</fieldset>
        <button className="btn btn-danger min-h-11" disabled={busy || !unlocked || !selected.length} onClick={() => setConfirm(true)}>{t('Schedule selected cleanup')}</button>
      </>}
      <ul className="space-y-3">{listing.decisions.map(item => <li className="rounded border border-line p-3 space-y-2" key={item.decision.body.decision_id}><p role="status">{t(stateLabel[item.state])}</p><p className="break-all font-mono text-xs">{item.decision.body.decision_id}</p><p className="text-ink-dim">{t('Owner-approved earliest cleanup')}: {new Date(item.decision.body.not_before * 1000).toLocaleString(locale)}</p>{['scheduled', 'unconfirmed', 'cancellation_pending'].includes(item.state) && <button className="btn btn-default min-h-11" disabled={busy || !unlocked} onClick={() => run(async () => { await retentionAPI.cancel(id, item.decision.body.decision_id, password || undefined); await load() })}>{t('Cancel historical cleanup')}</button>}{item.state === 'unconfirmed' && <button className="btn btn-default min-h-11" disabled={busy || !unlocked} onClick={() => run(async () => { await retentionAPI.schedule(id, item.decision.body.revisions.map(value => value.revision_id), password || undefined); await load() })}>{t('Retry signed cleanup decision')}</button>}</li>)}</ul>
    </div>}
    <ConfirmDialog open={confirm} title={t('Schedule permanent historical cleanup?')} body={t('After the cancellation period, the relay operator can permanently remove the selected old encrypted versions. They will no longer be available for historical recovery unless you saved an offline bundle. Current profiles and local backups are retained. This cannot erase copies already downloaded by other devices.')} destructive confirmLabel={t('Sign cleanup decision')} onCancel={() => setConfirm(false)} onConfirm={() => { setConfirm(false); void run(async () => { await retentionAPI.schedule(id, selected, password || undefined); await load() }) }} />
  </details>
}
