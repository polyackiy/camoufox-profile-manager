'use client'

interface SidebarProps {
  isCollapsed: boolean
  onToggle: () => void
  profileCount?: number
  groupCount?: number
}

export function Sidebar({ isCollapsed, onToggle, profileCount = 0, groupCount = 0 }: SidebarProps) {
  return (
    <div
      style={{
        position: 'fixed',
        left: 0,
        top: 0,
        height: '100vh',
        width: isCollapsed ? '64px' : '256px',
        backgroundColor: '#1a1a1a',
        borderRight: '1px solid #404040',
        transition: 'all 0.3s ease',
        zIndex: 40,
      }}
    >
      {/* Header */}
      <div style={{ 
        height: '64px', 
        padding: '16px', 
        borderBottom: '1px solid #404040',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        {!isCollapsed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ 
              width: '32px', 
              height: '32px', 
              backgroundColor: '#ff6b35', 
              borderRadius: '6px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontWeight: 'bold'
            }}>
              C
            </div>
            <span style={{ color: 'white', fontWeight: '600', fontSize: '18px' }}>Camoufox</span>
          </div>
        )}
        <button
          onClick={onToggle}
          style={{
            padding: '6px',
            borderRadius: '6px',
            backgroundColor: 'transparent',
            border: 'none',
            color: '#a3a3a3',
            cursor: 'pointer'
          }}
        >
          {isCollapsed ? '→' : '←'}
        </button>
      </div>

      {/* Navigation */}
      <nav style={{ marginTop: '16px', padding: '0 8px' }}>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          <li style={{ marginBottom: '4px' }}>
            <a
              href="/dashboard"
              style={{
                display: 'flex',
                alignItems: 'center',
                padding: '10px 12px',
                color: '#ff6b35',
                backgroundColor: '#ff6b35',
                borderRadius: '6px',
                textDecoration: 'none',
                fontSize: '14px',
                fontWeight: '500'
              }}
            >
              📊 {!isCollapsed && 'Dashboard'}
            </a>
          </li>
          <li style={{ marginBottom: '4px' }}>
            <a
              href="/profiles"
              style={{
                display: 'flex',
                alignItems: 'center',
                padding: '10px 12px',
                color: '#d4d4d4',
                borderRadius: '6px',
                textDecoration: 'none',
                fontSize: '14px',
                fontWeight: '500'
              }}
            >
              👥 {!isCollapsed && `Profiles (${profileCount})`}
            </a>
          </li>
        </ul>
      </nav>
    </div>
  )
}

// Mobile sidebar placeholder
interface MobileSidebarProps {
  isOpen: boolean
  onClose: () => void
  profileCount?: number
  groupCount?: number
}

export function MobileSidebar({ isOpen, onClose, profileCount = 0, groupCount = 0 }: MobileSidebarProps) {
  if (!isOpen) return null

  return (
    <div>
      <div 
        style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          zIndex: 40
        }}
        onClick={onClose} 
      />
      <div style={{
        position: 'fixed',
        left: 0,
        top: 0,
        height: '100vh',
        width: '256px',
        backgroundColor: '#1a1a1a',
        borderRight: '1px solid #404040',
        zIndex: 50
      }}>
        <div style={{ padding: '16px', color: 'white' }}>
          <h2>Mobile Menu</h2>
          <button onClick={onClose} style={{ color: 'white', marginTop: '16px' }}>Close</button>
        </div>
      </div>
    </div>
  )
} 