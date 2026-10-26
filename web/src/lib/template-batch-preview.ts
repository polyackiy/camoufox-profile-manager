/** Deliberately one placeholder rather than a general executable format language. */
export function batchProfileNames(pattern: string, count: number, start: number, digits: number): string[] {
  if (!Number.isInteger(count) || count < 1 || count > 25) throw new Error('Choose between 1 and 25 profiles.')
  if (!Number.isInteger(start) || start < 0 || start + count - 1 > 999999) throw new Error('Choose a starting number between 0 and 999999.')
  if (!Number.isInteger(digits) || digits < 1 || digits > 6) throw new Error('Choose between 1 and 6 number digits.')
  if (count > 1 && !pattern.includes('{n}')) throw new Error('Include {n} in the name pattern for multiple profiles.')
  const names = Array.from({ length: count }, (_, i) => pattern.replaceAll('{n}', String(start + i).padStart(digits, '0')).trim())
  if (names.some(name => !name || name.length > 255 || /[\u0000-\u001f\u007f]/.test(name))) throw new Error('Profile names must contain 1–255 characters without control characters.')
  return names
}
