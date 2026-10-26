import type { Translator } from './i18n'

/** Replace this known legacy CLI instruction with its desktop recovery path.
 * Unknown upstream text stays intact and is rendered as plain React text.
 */
export function translateProxyDiagnostic(message: string, t: Translator): string {
  if (message === "The proxy works, but its address could not be placed on the map. Run 'camoufox fetch' to install the location database.") {
    return t('The proxy answered, but location resources are missing. Before launching a profile, open Settings → Browser setup and click Retry download.')
  }
  return t(message)
}
