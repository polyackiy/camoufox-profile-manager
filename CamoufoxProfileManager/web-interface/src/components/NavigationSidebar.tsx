'use client'

import { usePathname } from 'next/navigation'

interface NavigationSidebarProps {
  profileCount?: number
}

export function NavigationSidebar({ profileCount = 23 }: NavigationSidebarProps) {
  const pathname = usePathname()
  
  const menuItems = [
    {
      href: '/dashboard',
      icon: '📊',
      label: 'Dashboard',
      isActive: pathname === '/dashboard' || pathname === '/'
    },
    {
      href: '/profiles', 
      icon: '👥',
      label: `Profiles (${profileCount})`,
      isActive: pathname === '/profiles'
    }
  ]

  return (
    <div style={{
      width: '256px',
      backgroundColor: '#1a1a1a',
      borderRight: '1px solid #404040',
      padding: '16px'
    }}>
      {/* Header */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        gap: '8px',
        marginBottom: '24px'
      }}>
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
        <span style={{ 
          color: 'white', 
          fontWeight: '600', 
          fontSize: '18px' 
        }}>
          Camoufox
        </span>
      </div>

      {/* Navigation */}
      <nav>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {menuItems.map((item) => (
            <li key={item.href} style={{ marginBottom: '4px' }}>
              <a
                href={item.href}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '10px 12px',
                  backgroundColor: item.isActive ? '#ff6b35' : 'transparent',
                  color: item.isActive ? 'white' : '#d4d4d4',
                  borderRadius: '6px',
                  textDecoration: 'none',
                  fontSize: '14px',
                  fontWeight: '500',
                  transition: 'all 0.2s ease'
                }}
                onMouseEnter={(e) => {
                  if (!item.isActive) {
                    e.currentTarget.style.backgroundColor = '#374151'
                  }
                }}
                onMouseLeave={(e) => {
                  if (!item.isActive) {
                    e.currentTarget.style.backgroundColor = 'transparent'
                  }
                }}
              >
                {item.icon} {item.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  )
} 