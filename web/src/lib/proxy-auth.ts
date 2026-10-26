/** Match protocol byte limits without retrieving a stored library password. */
export function proxyAuthenticationError(type: string, username: string, password: string, hasStoredPassword = false): string | null {
  if (type === 'socks4' && (username || password || hasStoredPassword)) return 'SOCKS4 credentials are not supported. Use SOCKS5, HTTP or HTTPS for authenticated connections.'
  if (type === 'socks5' && (username || password || hasStoredPassword)) {
    const userBytes = new TextEncoder().encode(username).length
    const passwordBytes = new TextEncoder().encode(password).length
    if (userBytes < 1 || userBytes > 255 || ((!hasStoredPassword || password) && (passwordBytes < 1 || passwordBytes > 255))) return 'SOCKS5 username and password must each be 1–255 UTF-8 bytes.'
  }
  if ((password || hasStoredPassword) && !username) return 'A password requires a username. Enter a username or remove the stored password.'
  return null
}
