'use client'

import { useState } from 'react'
import { Sidebar, MobileSidebar } from './Sidebar'

interface LayoutProps {
  children: React.ReactNode
  profileCount?: number
  groupCount?: number
}

export function Layout({ children, profileCount = 0, groupCount = 0 }: LayoutProps) {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false)

  return (
    <div style={{ 
      minHeight: '100vh',
      backgroundColor: '#0f0f0f',
      display: 'flex'
    }}>
      {/* Desktop Sidebar */}
      <Sidebar
        isCollapsed={isSidebarCollapsed}
        onToggle={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
        profileCount={profileCount}
        groupCount={groupCount}
      />

      {/* Mobile Sidebar */}
      <MobileSidebar
        isOpen={isMobileSidebarOpen}
        onClose={() => setIsMobileSidebarOpen(false)}
        profileCount={profileCount}
        groupCount={groupCount}
      />

      {/* Main Content */}
      <div style={{
        flex: 1,
        marginLeft: isSidebarCollapsed ? '64px' : '256px',
        transition: 'margin-left 0.3s ease',
        minHeight: '100vh'
      }}>
        <main style={{ 
          padding: '24px',
          color: 'white'
        }}>
          {children}
        </main>
      </div>

      {/* Mobile Menu Button */}
      <button
        onClick={() => setIsMobileSidebarOpen(true)}
        style={{
          position: 'fixed',
          top: '16px',
          left: '16px',
          zIndex: 30,
          padding: '8px',
          backgroundColor: '#1a1a1a',
          border: '1px solid #404040',
          borderRadius: '6px',
          color: 'white',
          cursor: 'pointer',
          display: 'none'
        }}
        className="lg:hidden"
      >
        ☰
      </button>
    </div>
  )
} 