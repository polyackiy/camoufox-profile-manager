'use client'

import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'

interface ModalProps {
  open: boolean
  title: string
  subtitle?: string
  onClose: () => void
  children: React.ReactNode
  footer?: React.ReactNode
  width?: number
}

/**
 * Dialog shell. Closes on Escape and on backdrop click, moves focus inside on
 * open, and locks background scroll — the old modal did none of these.
 */
export function Modal({ open, title, subtitle, onClose, children, footer, width = 620 }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panelRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="overlay-in fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-6 backdrop-blur-[2px]"
      onMouseDown={(event) => {
        // Only a click that starts on the backdrop closes; a drag out of a text
        // field must not dismiss the dialog.
        if (event.target === event.currentTarget) onClose()
      }}
    >
      {/* Header and footer stay pinned; only the body scrolls, so a tall form
          can never push its own title or its Save button out of reach. */}
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{ width }}
        className="dialog-in flex max-h-full max-w-full flex-col rounded-lg border border-line bg-surface shadow-2xl shadow-black/60 outline-none"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-3.5">
          <div>
            <h2 className="text-[14px] font-semibold">{title}</h2>
            {subtitle && <p className="mt-0.5 text-ink-dim">{subtitle}</p>}
          </div>
          <button onClick={onClose} aria-label="Close" className="btn btn-ghost -mr-2 h-7 w-7 p-0">
            <X size={15} strokeWidth={2} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer && (
          <footer className="flex shrink-0 justify-end gap-2 border-t border-line px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}

interface ConfirmProps {
  open: boolean
  title: string
  body: string
  confirmLabel?: string
  destructive?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** Replaces window.confirm so destructive actions match the rest of the UI. */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Confirm',
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmProps) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      width={420}
      footer={
        <>
          <button className="btn btn-default" onClick={onCancel}>
            Cancel
          </button>
          <button
            className={destructive ? 'btn btn-danger' : 'btn btn-primary'}
            onClick={onConfirm}
            autoFocus
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <p className="text-ink-dim">{body}</p>
    </Modal>
  )
}
