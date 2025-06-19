'use client'

import { useState, useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { profilesAPI } from '@/lib/api'

// Компонент навигации
function NavigationSidebar() {
  const pathname = usePathname()
  
  return (
    <div style={{
      width: '250px',
      backgroundColor: '#1a1a1a',
      height: '100vh',
      borderRight: '1px solid #333',
      padding: '20px 0',
      position: 'fixed',
      left: 0,
      top: 0
    }}>
      <div style={{
        padding: '0 20px',
        marginBottom: '30px'
      }}>
        <h1 style={{
          color: '#ff6b35',
          fontSize: '24px',
          fontWeight: 'bold',
          margin: 0
        }}>
          🦊 Camoufox
        </h1>
        <p style={{
          color: '#888',
          fontSize: '14px',
          margin: '5px 0 0 0'
        }}>
          Profile Manager
        </p>
      </div>
      
      <nav>
        <a
          href="/dashboard"
          style={{
            display: 'block',
            padding: '12px 20px',
            color: pathname === '/dashboard' ? '#ff6b35' : '#ccc',
            backgroundColor: pathname === '/dashboard' ? 'rgba(255, 107, 53, 0.1)' : 'transparent',
            textDecoration: 'none',
            borderRight: pathname === '/dashboard' ? '3px solid #ff6b35' : 'none',
            transition: 'all 0.2s'
          }}
        >
          📊 Dashboard
        </a>
        <a
          href="/profiles"
          style={{
            display: 'block',
            padding: '12px 20px',
            color: pathname === '/profiles' ? '#ff6b35' : '#ccc',
            backgroundColor: pathname === '/profiles' ? 'rgba(255, 107, 53, 0.1)' : 'transparent',
            textDecoration: 'none',
            borderRight: pathname === '/profiles' ? '3px solid #ff6b35' : 'none',
            transition: 'all 0.2s'
          }}
        >
          👥 Profiles
        </a>
        <a
          href="/profiles2"
          style={{
            display: 'block',
            padding: '12px 20px',
            color: pathname === '/profiles2' ? '#ff6b35' : '#ccc',
            backgroundColor: pathname === '/profiles2' ? 'rgba(255, 107, 53, 0.1)' : 'transparent',
            textDecoration: 'none',
            borderRight: pathname === '/profiles2' ? '3px solid #ff6b35' : 'none',
            transition: 'all 0.2s'
          }}
        >
          👥 Profiles2
        </a>
      </nav>
    </div>
  )
}

export default function Dashboard() {
  const [stats, setStats] = useState({
    total: 0,
    active: 0,
    inactive: 0,
    error: 0,
    by_os: {} as Record<string, number>,
    by_group: {} as Record<string, number>
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Загрузка статистики
  const loadStats = async () => {
    try {
      setLoading(true)
      setError(null)
      
      // Получаем общую статистику через список профилей
      const response = await profilesAPI.getProfiles({ per_page: 100 })
      
      const total = response.total
      const profiles = response.profiles
      
      // Подсчитываем статистику
      const statusCounts = profiles.reduce((acc, profile) => {
        acc[profile.status] = (acc[profile.status] || 0) + 1
        return acc
      }, {} as Record<string, number>)
      
      const osCounts = profiles.reduce((acc, profile) => {
        const os = profile.browser_settings.os
        acc[os] = (acc[os] || 0) + 1
        return acc
      }, {} as Record<string, number>)
      
      const groupCounts = profiles.reduce((acc, profile) => {
        acc[profile.group] = (acc[profile.group] || 0) + 1
        return acc
      }, {} as Record<string, number>)
      
      setStats({
        total,
        active: statusCounts.active || 0,
        inactive: statusCounts.inactive || 0,
        error: statusCounts.error || 0,
        by_os: osCounts,
        by_group: groupCounts
      })
      
    } catch (err) {
      console.error('Ошибка загрузки статистики:', err)
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadStats()
  }, [])

  if (loading) {
    return (
      <div style={{ display: 'flex', height: '100vh', backgroundColor: '#0f0f0f' }}>
        <NavigationSidebar />
        <div style={{ marginLeft: '250px', padding: '40px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ color: '#ccc', fontSize: '18px' }}>Загрузка статистики...</div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ display: 'flex', height: '100vh', backgroundColor: '#0f0f0f' }}>
        <NavigationSidebar />
        <div style={{ marginLeft: '250px', padding: '40px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ color: '#ef4444', fontSize: '18px' }}>
            Ошибка: {error}
            <br />
            <button 
              onClick={loadStats}
              style={{
                marginTop: '20px',
                padding: '10px 20px',
                backgroundColor: '#ff6b35',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer'
              }}
            >
              Попробовать снова
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', height: '100vh', backgroundColor: '#0f0f0f' }}>
      <NavigationSidebar />
      
      <div style={{ marginLeft: '250px', padding: '40px', flex: 1 }}>
        <div style={{ marginBottom: '30px' }}>
          <h1 style={{ color: '#fff', fontSize: '32px', fontWeight: 'bold', margin: '0 0 10px 0' }}>
            Dashboard
          </h1>
          <p style={{ color: '#888', fontSize: '16px', margin: 0 }}>
            Обзор системы управления профилями
          </p>
        </div>

        {/* Статистические карточки */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
          gap: '20px',
          marginBottom: '40px'
        }}>
          <div style={{
            backgroundColor: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: '8px',
            padding: '24px'
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '12px'
            }}>
              <h3 style={{ color: '#ccc', fontSize: '14px', fontWeight: '500', margin: 0, textTransform: 'uppercase' }}>
                Всего профилей
              </h3>
              <span style={{ fontSize: '24px' }}>📊</span>
            </div>
            <div style={{ color: '#fff', fontSize: '32px', fontWeight: 'bold' }}>
              {stats.total}
            </div>
          </div>

          <div style={{
            backgroundColor: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: '8px',
            padding: '24px'
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '12px'
            }}>
              <h3 style={{ color: '#ccc', fontSize: '14px', fontWeight: '500', margin: 0, textTransform: 'uppercase' }}>
                Активные
              </h3>
              <span style={{ fontSize: '24px' }}>✅</span>
            </div>
            <div style={{ color: '#10b981', fontSize: '32px', fontWeight: 'bold' }}>
              {stats.active}
            </div>
          </div>

          <div style={{
            backgroundColor: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: '8px',
            padding: '24px'
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '12px'
            }}>
              <h3 style={{ color: '#ccc', fontSize: '14px', fontWeight: '500', margin: 0, textTransform: 'uppercase' }}>
                Неактивные
              </h3>
              <span style={{ fontSize: '24px' }}>⏸️</span>
            </div>
            <div style={{ color: '#6b7280', fontSize: '32px', fontWeight: 'bold' }}>
              {stats.inactive}
            </div>
          </div>

          <div style={{
            backgroundColor: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: '8px',
            padding: '24px'
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '12px'
            }}>
              <h3 style={{ color: '#ccc', fontSize: '14px', fontWeight: '500', margin: 0, textTransform: 'uppercase' }}>
                С ошибками
              </h3>
              <span style={{ fontSize: '24px' }}>❌</span>
            </div>
            <div style={{ color: '#ef4444', fontSize: '32px', fontWeight: 'bold' }}>
              {stats.error}
            </div>
          </div>
        </div>

        {/* Распределение по ОС */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '20px',
          marginBottom: '40px'
        }}>
          <div style={{
            backgroundColor: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: '8px',
            padding: '24px'
          }}>
            <h3 style={{ color: '#fff', fontSize: '18px', fontWeight: '600', marginBottom: '20px' }}>
              Распределение по ОС
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {Object.entries(stats.by_os).map(([os, count]) => (
                <div key={os} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span>{os === 'windows' ? '🖥️' : os === 'macos' ? '💻' : '🖥️'}</span>
                    <span style={{ color: '#ccc', textTransform: 'capitalize' }}>{os}</span>
                  </div>
                  <div style={{
                    backgroundColor: '#ff6b35',
                    color: '#fff',
                    padding: '4px 8px',
                    borderRadius: '12px',
                    fontSize: '12px',
                    fontWeight: '600'
                  }}>
                    {count}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{
            backgroundColor: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: '8px',
            padding: '24px'
          }}>
            <h3 style={{ color: '#fff', fontSize: '18px', fontWeight: '600', marginBottom: '20px' }}>
              Топ групп
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {Object.entries(stats.by_group)
                .sort(([,a], [,b]) => b - a)
                .slice(0, 5)
                .map(([group, count]) => (
                <div key={group} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span>📁</span>
                    <span style={{ color: '#ccc' }}>{group.slice(0, 8)}...</span>
                  </div>
                  <div style={{
                    backgroundColor: '#ff6b35',
                    color: '#fff',
                    padding: '4px 8px',
                    borderRadius: '12px',
                    fontSize: '12px',
                    fontWeight: '600'
                  }}>
                    {count}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Добро пожаловать */}
        <div style={{
          backgroundColor: '#1a1a1a',
          border: '1px solid #333',
          borderRadius: '8px',
          padding: '32px',
          textAlign: 'center'
        }}>
          <h2 style={{ color: '#fff', fontSize: '24px', fontWeight: 'bold', marginBottom: '12px' }}>
            🦊 Добро пожаловать в Camoufox Profile Manager
          </h2>
          <p style={{ color: '#888', fontSize: '16px', marginBottom: '24px' }}>
            Мощная система управления профилями антидетект браузера
          </p>
          <div style={{ display: 'flex', gap: '16px', justifyContent: 'center' }}>
            <a
              href="/profiles"
              style={{
                display: 'inline-block',
                padding: '12px 24px',
                backgroundColor: '#ff6b35',
                color: 'white',
                textDecoration: 'none',
                borderRadius: '6px',
                fontWeight: '500',
                transition: 'background-color 0.2s'
              }}
              onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#e55a2b'}
              onMouseLeave={(e) => e.currentTarget.style.backgroundColor = '#ff6b35'}
            >
              Управление профилями
            </a>
            <button
              onClick={loadStats}
              style={{
                padding: '12px 24px',
                backgroundColor: 'transparent',
                color: '#ccc',
                border: '1px solid #333',
                borderRadius: '6px',
                fontWeight: '500',
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = '#333'
                e.currentTarget.style.color = '#fff'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.color = '#ccc'
              }}
            >
              Обновить статистику
            </button>
          </div>
        </div>
      </div>
    </div>
  )
} 