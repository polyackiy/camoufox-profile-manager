interface Props {
  icon: React.ReactNode
  title: string
  body: string
  action?: React.ReactNode
}

/** Shown instead of an empty grid, so a new install explains itself. */
export function EmptyState({ icon, title, body, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-20 text-center">
      <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg border border-line bg-raised text-ink-faint">
        {icon}
      </div>
      <h2 className="text-[14px] font-semibold">{title}</h2>
      <p className="mt-1.5 max-w-[42ch] text-ink-dim">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
