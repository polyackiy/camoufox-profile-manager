'use client'

import { useState, useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { profilesAPI, type Profile, type ProfilesResponse, formatProxyString, formatLastUsed, getStatusColor, getOSIcon } from '@/lib/api'
import EditProfileModal from '@/components/EditProfileModal'

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
      </nav>
    </div>
  )
}

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedProfiles, setSelectedProfiles] = useState<Set<string>>(new Set())
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [currentPage, setCurrentPage] = useState(1)
  const [totalProfiles, setTotalProfiles] = useState(0)
  const [hasNext, setHasNext] = useState(false)
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [activeBrowsers, setActiveBrowsers] = useState<Set<string>>(new Set())

  // Загрузка профилей
  const loadProfiles = async () => {
    try {
      setLoading(true)
      setError(null)
      
      const params: Record<string, any> = {
        page: currentPage,
        per_page: 20
      }
      
      if (statusFilter !== 'all') {
        params.status = statusFilter
      }
      
      if (searchTerm.trim()) {
        params.search = searchTerm.trim()
      }
      
      const response: ProfilesResponse = await profilesAPI.getProfiles(params)
      
      setProfiles(response.profiles)
      setTotalProfiles(response.total)
      setHasNext(response.has_next)
      
    } catch (err) {
      console.error('Ошибка загрузки профилей:', err)
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка')
    } finally {
      setLoading(false)
    }
  }

  // Загрузка активных браузеров
  const loadActiveBrowsers = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/browsers/active')
      if (response.ok) {
        const result = await response.json()
        const activeProfileIds = new Set<string>(result.active_browsers.map((browser: { profile_id: string }) => browser.profile_id))
        setActiveBrowsers(activeProfileIds)
      }
    } catch (err) {
      console.error('Ошибка загрузки активных браузеров:', err)
    }
  }

  // Загружаем профили при изменении параметров
  useEffect(() => {
    loadProfiles()
  }, [currentPage, statusFilter, searchTerm])

  // Загружаем активные браузеры при загрузке компонента и периодически обновляем
  useEffect(() => {
    loadActiveBrowsers()
    
    // Обновляем активные браузеры каждые 5 секунд
    const interval = setInterval(loadActiveBrowsers, 5000)
    
    return () => clearInterval(interval)
  }, [])

  // Обработчики событий
  const handleSelectProfile = (profileId: string) => {
    const newSelected = new Set(selectedProfiles)
    if (newSelected.has(profileId)) {
      newSelected.delete(profileId)
    } else {
      newSelected.add(profileId)
    }
    setSelectedProfiles(newSelected)
  }

  const handleSelectAll = () => {
    if (selectedProfiles.size === profiles.length) {
      setSelectedProfiles(new Set())
    } else {
      setSelectedProfiles(new Set(profiles.map(p => p.id)))
    }
  }

  const handleOpenProfile = async (profileId: string) => {
    try {
      await profilesAPI.startProfile(profileId)
      // Обновляем список профилей и активных браузеров после запуска
      loadProfiles()
      loadActiveBrowsers()
    } catch (err) {
      console.error('Ошибка запуска профиля:', err)
      alert('Ошибка запуска профиля: ' + (err instanceof Error ? err.message : 'Неизвестная ошибка'))
    }
  }

  const handleCloneProfile = async (profileId: string) => {
    try {
      const profile = profiles.find(p => p.id === profileId)
      const newName = prompt('Введите имя для клона:', profile?.name + ' (Clone)')
      if (newName) {
        await profilesAPI.cloneProfile(profileId, newName)
        loadProfiles()
      }
    } catch (err) {
      console.error('Ошибка клонирования профиля:', err)
      alert('Ошибка клонирования: ' + (err instanceof Error ? err.message : 'Неизвестная ошибка'))
    }
  }

  const handleDeleteProfile = async (profileId: string) => {
    if (confirm('Вы уверены, что хотите удалить этот профиль?')) {
      try {
        await profilesAPI.deleteProfile(profileId)
        loadProfiles()
      } catch (err) {
        console.error('Ошибка удаления профиля:', err)
        alert('Ошибка удаления: ' + (err instanceof Error ? err.message : 'Неизвестная ошибка'))
      }
    }
  }

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setCurrentPage(1)
    loadProfiles()
  }

  const handleEditProfile = (profileId: string) => {
    const profile = profiles.find(p => p.id === profileId)
    if (profile) {
      setEditingProfile(profile)
      setIsEditModalOpen(true)
    }
  }

  const handleEditSave = (updatedProfile: Profile) => {
    // Обновляем профиль в списке
    setProfiles(profiles.map(p => p.id === updatedProfile.id ? updatedProfile : p))
  }

  const handleEditClose = () => {
    setIsEditModalOpen(false)
    setEditingProfile(null)
  }

  // Обработчики управления браузерами
  const handleCloseProfileBrowser = async (profileId: string) => {
    try {
      const response = await fetch(`http://localhost:8000/api/profiles/${profileId}/close`, {
        method: 'POST'
      })
      const result = await response.json()
      
      if (response.ok) {
        console.log(`✅ ${result.message}`)
        // Обновляем активные браузеры после закрытия
        loadActiveBrowsers()
      } else {
        console.error(`❌ Ошибка: ${result.detail || result.message}`)
      }
    } catch (err) {
      console.error('Ошибка закрытия браузера:', err)
    }
  }

  const handleCloseAllBrowsers = async () => {
    if (!confirm('Вы уверены, что хотите закрыть ВСЕ активные браузеры?')) {
      return
    }

    try {
      const response = await fetch('http://localhost:8000/api/browsers/close-all', {
        method: 'POST'
      })
      const result = await response.json()
      
      if (response.ok) {
        console.log(`✅ ${result.message}`)
        // Обновляем активные браузеры после закрытия всех
        loadActiveBrowsers()
      } else {
        console.error(`❌ Ошибка: ${result.detail || result.message}`)
      }
    } catch (err) {
      console.error('Ошибка закрытия всех браузеров:', err)
    }
  }

  // Обработчики Excel функций
  const handleExportToExcel = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/profiles/export/excel')
      if (!response.ok) {
        throw new Error('Ошибка экспорта')
      }
      
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'camoufox_profiles.xlsx'
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      
      alert('✅ Профили успешно экспортированы в Excel!')
    } catch (err) {
      console.error('Ошибка экспорта:', err)
      alert('❌ Ошибка экспорта: ' + (err instanceof Error ? err.message : 'Неизвестная ошибка'))
    }
  }

  const handleImportFromExcel = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    try {
      const formData = new FormData()
      formData.append('file', file)
      
      const response = await fetch('http://localhost:8000/api/profiles/import/excel', {
        method: 'POST',
        body: formData
      })
      
      const result = await response.json()
      
      if (result.success) {
        alert(`✅ Импорт успешен!\n\nСоздано профилей: ${result.data.created_count}\n\n💡 Все профили создаются с новыми автоматически генерируемыми ID`)
        loadProfiles() // Перезагружаем список профилей
      } else {
        let errorMessage = `❌ Импорт завершен с ошибками:\n\n${result.message}`
        if (result.data.errors && result.data.errors.length > 0) {
          errorMessage += '\n\nОшибки:\n' + result.data.errors.slice(0, 5).join('\n')
          if (result.data.errors.length > 5) {
            errorMessage += `\n... и еще ${result.data.errors.length - 5} ошибок`
          }
        }
        alert(errorMessage)
      }
    } catch (err) {
      console.error('Ошибка импорта:', err)
      alert('❌ Ошибка импорта: ' + (err instanceof Error ? err.message : 'Неизвестная ошибка'))
    }
    
    // Сбрасываем значение input для возможности повторного выбора того же файла
    event.target.value = ''
  }

  // Закрытие меню при клике вне его
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Element
      // Проверяем что клик был не по кнопке меню и не по самому меню
      if (!target.closest('[data-menu-button]') && !target.closest('[data-menu-dropdown]')) {
        setOpenMenuId(null)
      }
    }
    document.addEventListener('click', handleClickOutside)
    return () => document.removeEventListener('click', handleClickOutside)
  }, [])

  if (loading) {
    return (
      <div style={{ display: 'flex', height: '100vh', backgroundColor: '#0f0f0f' }}>
        <NavigationSidebar />
        <div style={{ marginLeft: '250px', padding: '40px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ color: '#ccc', fontSize: '18px' }}>Загрузка профилей...</div>
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
              onClick={loadProfiles}
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
      
      <div style={{ marginLeft: '250px', padding: '40px', flex: 1, minHeight: '100vh', overflow: 'visible' }}>
        <div style={{ marginBottom: '30px' }}>
          <h1 style={{ color: '#fff', fontSize: '32px', fontWeight: 'bold', margin: '0 0 10px 0' }}>
            Profiles ({totalProfiles})
          </h1>
          <p style={{ color: '#888', fontSize: '16px', margin: 0 }}>
            Управление профилями антидетект браузера
          </p>
        </div>

        {/* Фильтры и поиск */}
        <div style={{ 
          display: 'flex', 
          gap: '20px', 
          marginBottom: '30px',
          alignItems: 'center'
        }}>
          <form onSubmit={handleSearch} style={{ display: 'flex', gap: '10px' }}>
            <input
              type="text"
              placeholder="Поиск профилей..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                padding: '10px 15px',
                backgroundColor: '#1a1a1a',
                border: '1px solid #333',
                borderRadius: '6px',
                color: '#fff',
                width: '300px'
              }}
            />
            <button
              type="submit"
              style={{
                padding: '10px 20px',
                backgroundColor: '#ff6b35',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer'
              }}
            >
              Поиск
            </button>
          </form>

          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value)
              setCurrentPage(1)
            }}
            style={{
              padding: '10px 15px',
              backgroundColor: '#1a1a1a',
              border: '1px solid #333',
              borderRadius: '6px',
              color: '#fff'
            }}
          >
            <option value="all">Все статусы</option>
            <option value="active">Активные</option>
            <option value="inactive">Неактивные</option>
            <option value="error">С ошибками</option>
            <option value="pending">Ожидание</option>
          </select>

          {/* Кнопки управления */}
          <div style={{ display: 'flex', gap: '10px', marginLeft: 'auto' }}>
            <button
              onClick={handleCloseAllBrowsers}
              style={{
                padding: '10px 20px',
                backgroundColor: '#dc3545',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px'
              }}
            >
              🔒 Закрыть все браузеры
            </button>
            <button
              onClick={handleExportToExcel}
              style={{
                padding: '10px 20px',
                backgroundColor: '#28a745',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px'
              }}
            >
              📊 Экспорт в Excel
            </button>
            <label
              style={{
                padding: '10px 20px',
                backgroundColor: '#007bff',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px'
              }}
            >
              📥 Импорт из Excel
              <input
                type="file"
                accept=".xlsx,.xls"
                onChange={handleImportFromExcel}
                style={{ display: 'none' }}
              />
            </label>
          </div>
        </div>

        {/* Таблица профилей */}
        <div style={{ 
          backgroundColor: '#1a1a1a', 
          borderRadius: '8px', 
          overflow: 'visible', // Изменено чтобы меню не обрезалось
          border: '1px solid #333'
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ backgroundColor: '#222' }}>
                <th style={{ padding: '15px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>
                  <input
                    type="checkbox"
                    checked={selectedProfiles.size === profiles.length && profiles.length > 0}
                    onChange={handleSelectAll}
                    style={{ marginRight: '10px' }}
                  />
                  SELECT
                </th>
                <th style={{ padding: '15px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>NAME</th>
                <th style={{ padding: '15px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>STATUS</th>
                <th style={{ padding: '15px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>OS</th>
                <th style={{ padding: '15px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>PROXY</th>
                <th style={{ padding: '15px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>LAST USED</th>
                <th style={{ padding: '15px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>ACTION</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((profile) => (
                <tr key={profile.id} style={{ borderTop: '1px solid #333' }}>
                  <td style={{ padding: '15px', color: '#fff' }}>
                    <input
                      type="checkbox"
                      checked={selectedProfiles.has(profile.id)}
                      onChange={() => handleSelectProfile(profile.id)}
                      style={{ marginRight: '10px' }}
                    />
                    {profile.id.slice(0, 8)}...
                  </td>
                  <td style={{ padding: '15px', color: '#fff', fontWeight: '500' }}>
                    {profile.name}
                  </td>
                  <td style={{ padding: '15px' }}>
                    <span style={{
                      display: 'inline-block',
                      padding: '4px 8px',
                      borderRadius: '4px',
                      fontSize: '12px',
                      fontWeight: '500',
                      backgroundColor: getStatusColor(profile.status) + '20',
                      color: getStatusColor(profile.status),
                      textTransform: 'uppercase'
                    }}>
                      {profile.status}
                    </span>
                  </td>
                  <td style={{ padding: '15px', color: '#fff' }}>
                    {getOSIcon(profile.browser_settings.os)} {profile.browser_settings.os}
                  </td>
                  <td style={{ padding: '15px', color: '#888' }}>
                    {formatProxyString(profile.proxy_config)}
                  </td>
                  <td style={{ padding: '15px', color: '#888' }}>
                    {formatLastUsed(profile.last_used)}
                  </td>
                  <td style={{ padding: '15px' }}>
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                      <button
                        onClick={() => {
                          if (activeBrowsers.has(profile.id)) {
                            handleCloseProfileBrowser(profile.id)
                          } else {
                            handleOpenProfile(profile.id)
                          }
                        }}
                        style={{
                          padding: '8px 16px',
                          backgroundColor: activeBrowsers.has(profile.id) ? '#dc3545' : '#ff6b35',
                          color: 'white',
                          border: 'none',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontWeight: '500',
                          transition: 'background-color 0.2s'
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.backgroundColor = activeBrowsers.has(profile.id) ? '#c82333' : '#e55a2b'
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.backgroundColor = activeBrowsers.has(profile.id) ? '#dc3545' : '#ff6b35'
                        }}
                      >
                        {activeBrowsers.has(profile.id) ? 'Close' : 'Open'}
                      </button>
                      <div style={{ position: 'relative' }}>
                        <button
                          data-menu-button
                          onClick={(e) => {
                            e.stopPropagation()
                            setOpenMenuId(openMenuId === profile.id ? null : profile.id)
                          }}
                          style={{
                            padding: '8px 12px',
                            backgroundColor: openMenuId === profile.id ? '#444' : '#333',
                            color: '#ccc',
                            border: 'none',
                            borderRadius: '6px',
                            cursor: 'pointer',
                            fontSize: '16px',
                            transition: 'background-color 0.2s'
                          }}
                          onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#444'}
                          onMouseLeave={(e) => e.currentTarget.style.backgroundColor = openMenuId === profile.id ? '#444' : '#333'}
                        >
                          ⋮
                        </button>
                        {openMenuId === profile.id && (
                          <div 
                            data-menu-dropdown
                            style={{
                              position: 'absolute',
                              // Показываем меню сверху для последних 3 строк
                              ...(profiles.indexOf(profile) >= profiles.length - 3 
                                ? { bottom: '100%', marginBottom: '5px' } 
                                : { top: '100%', marginTop: '5px' }
                              ),
                              right: 0,
                              backgroundColor: '#2a2a2a',
                              border: '1px solid #444',
                              borderRadius: '6px',
                              padding: '8px 0',
                              minWidth: '120px',
                              zIndex: 1000,
                              boxShadow: '0 4px 12px rgba(0,0,0,0.3)'
                            }}>
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                setOpenMenuId(null)
                                handleEditProfile(profile.id)
                              }}
                              style={{
                                display: 'block',
                                width: '100%',
                                padding: '8px 16px',
                                backgroundColor: 'transparent',
                                color: '#ccc',
                                border: 'none',
                                textAlign: 'left',
                                cursor: 'pointer',
                                transition: 'background-color 0.2s'
                              }}
                              onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#333'}
                              onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                            >
                              ✏️ Edit
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                setOpenMenuId(null)
                                handleCloneProfile(profile.id)
                              }}
                              style={{
                                display: 'block',
                                width: '100%',
                                padding: '8px 16px',
                                backgroundColor: 'transparent',
                                color: '#ccc',
                                border: 'none',
                                textAlign: 'left',
                                cursor: 'pointer',
                                transition: 'background-color 0.2s'
                              }}
                              onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#333'}
                              onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                            >
                              📋 Clone
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                setOpenMenuId(null)
                                handleDeleteProfile(profile.id)
                              }}
                              style={{
                                display: 'block',
                                width: '100%',
                                padding: '8px 16px',
                                backgroundColor: 'transparent',
                                color: '#ef4444',
                                border: 'none',
                                textAlign: 'left',
                                cursor: 'pointer',
                                transition: 'background-color 0.2s'
                              }}
                              onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#333'}
                              onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                            >
                              🗑️ Delete
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Пагинация */}
        {totalProfiles > 20 && (
          <div style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: '20px',
            marginTop: '30px'
          }}>
            <button
              onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
              disabled={currentPage === 1}
              style={{
                padding: '10px 20px',
                backgroundColor: currentPage === 1 ? '#333' : '#ff6b35',
                color: currentPage === 1 ? '#666' : 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: currentPage === 1 ? 'not-allowed' : 'pointer'
              }}
            >
              ← Предыдущая
            </button>
            
            <span style={{ color: '#ccc' }}>
              Страница {currentPage}
            </span>
            
            <button
              onClick={() => setCurrentPage(prev => prev + 1)}
              disabled={!hasNext}
              style={{
                padding: '10px 20px',
                backgroundColor: !hasNext ? '#333' : '#ff6b35',
                color: !hasNext ? '#666' : 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: !hasNext ? 'not-allowed' : 'pointer'
              }}
            >
              Следующая →
            </button>
          </div>
        )}
      </div>
      
      {/* Модальное окно редактирования */}
      <EditProfileModal
        profile={editingProfile}
        isOpen={isEditModalOpen}
        onClose={handleEditClose}
        onSave={handleEditSave}
      />
    </div>
  )
} 