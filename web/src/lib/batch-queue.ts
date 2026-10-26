/** Interactive, bounded queue. Accepted server operations are never aborted or replayed. */
export const MAX_BATCH_SIZE = 100
export const MAX_BATCH_CONCURRENCY = 4

export type BatchTarget = { id: string; name: string }
export type BatchStatus = 'waiting' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type BatchRow = BatchTarget & { status: BatchStatus; error?: string }
export type BatchSnapshot = { rows: BatchRow[]; running: boolean; accessLost: boolean }

type Options = {
  concurrency: number
  execute: (id: string) => Promise<unknown>
  onChange?: (snapshot: BatchSnapshot) => void
  describeError?: (error: unknown) => string
  accessDenied?: (error: unknown) => boolean
}

export class BatchQueue {
  private rows: BatchRow[]
  private active = false
  private disposed = false
  private accessLost = false
  private onChange: Options['onChange']

  constructor(targets: BatchTarget[], private readonly options: Options) {
    if (!targets.length || targets.length > MAX_BATCH_SIZE) throw new Error('Invalid batch size')
    if (!Number.isInteger(options.concurrency) || options.concurrency < 1 || options.concurrency > MAX_BATCH_CONCURRENCY) throw new Error('Invalid concurrency')
    const seen = new Set<string>()
    this.rows = targets.filter(target => {
      if (!target.id || seen.has(target.id)) return false
      seen.add(target.id)
      return true
    }).map(target => ({ id: target.id, name: target.name, status: 'waiting' }))
    if (!this.rows.length) throw new Error('Invalid batch size')
    this.onChange = options.onChange
  }

  snapshot(): BatchSnapshot {
    return { rows: this.rows.map(row => ({ ...row })), running: this.active, accessLost: this.accessLost }
  }

  private publish() { this.onChange?.(this.snapshot()) }

  cancelWaiting() {
    for (const row of this.rows) if (row.status === 'waiting') row.status = 'cancelled'
    this.publish()
  }

  loseAccess() {
    this.accessLost = true
    this.cancelWaiting()
  }

  dispose() {
    this.disposed = true
    this.onChange = undefined
    this.cancelWaiting()
  }

  async run(retryIncomplete = false): Promise<void> {
    if (this.active || this.disposed || this.accessLost) throw new Error('Queue cannot start')
    if (retryIncomplete) {
      for (const row of this.rows) {
        if (row.status === 'failed' || row.status === 'cancelled') {
          row.status = 'waiting'
          row.error = undefined
        }
      }
    }
    this.active = true
    this.publish()
    try {
      await Promise.all(Array.from({ length: this.options.concurrency }, async () => {
        while (!this.disposed && !this.accessLost) {
          // No await between claiming the item and marking it running.
          const row = this.rows.find(item => item.status === 'waiting')
          if (!row) return
          row.status = 'running'
          this.publish()
          try {
            await this.options.execute(row.id)
            row.status = 'succeeded'
          } catch (error) {
            row.status = 'failed'
            // Never retain arbitrary server errors, which may include secrets.
            row.error = this.options.describeError?.(error) ?? 'Operation failed.'
            if (this.options.accessDenied?.(error)) {
              this.loseAccess()
            }
          }
          this.publish()
        }
      }))
    } finally {
      this.active = false
      this.publish()
    }
  }
}
