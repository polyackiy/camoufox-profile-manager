import type { Metadata } from 'next'

import { AppShell } from '@/components/app-shell'
import { LoginGate } from '@/components/login-gate'
import { ToastProvider } from '@/components/toast'
import './fonts.css'
import './globals.css'

export const metadata: Metadata = {
  title: 'Camoufox Profile Manager',
  description: 'Antidetect browser profile management',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ToastProvider>
          <LoginGate>
            <AppShell>{children}</AppShell>
          </LoginGate>
        </ToastProvider>
      </body>
    </html>
  )
}
