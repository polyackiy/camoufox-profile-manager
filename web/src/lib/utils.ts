import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merge Tailwind class names, resolving conflicts. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/** Format an ISO date string for display; returns a dash for empty values. */
export function formatDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString()
}

/** Tailwind text-color class for a profile status. */
export function getStatusColor(status: string): string {
  switch (status) {
    case 'active':
      return 'text-green-500'
    case 'inactive':
      return 'text-gray-400'
    case 'blocked':
    case 'error':
      return 'text-red-500'
    case 'maintenance':
    case 'pending':
      return 'text-amber-500'
    default:
      return 'text-gray-400'
  }
}

/** Tailwind badge classes for a profile status. */
export function getStatusBadgeColor(status: string): string {
  switch (status) {
    case 'active':
      return 'bg-green-500/10 text-green-500'
    case 'inactive':
      return 'bg-gray-500/10 text-gray-400'
    case 'blocked':
    case 'error':
      return 'bg-red-500/10 text-red-500'
    case 'maintenance':
    case 'pending':
      return 'bg-amber-500/10 text-amber-500'
    default:
      return 'bg-gray-500/10 text-gray-400'
  }
}
