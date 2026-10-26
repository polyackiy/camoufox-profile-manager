'use client'

import { useI18n } from '@/components/i18n-provider'

export function ProxyProtocolHelp({ type, id, className = '' }: {
  type: string
  id: string
  className?: string
}) {
  const { t } = useI18n()
  return <p id={id} className={`text-ink-dim ${className}`}>
    {type === 'http' || type === 'https'
      ? t('HTTP also works with HTTPS websites. Select HTTPS only when your provider supports a TLS connection to the proxy itself.')
      : type === 'socks5'
        ? t('SOCKS5 supports username/password authentication. Enter host:port here and credentials in the separate fields.')
        : t('SOCKS4 uses remote DNS (SOCKS4a). Username/password and IPv6 destinations are unsupported; use SOCKS5 for those.')}
  </p>
}
