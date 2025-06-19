'use client'

import { useState, useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { profilesAPI, type Profile, type ProfilesResponse, formatProxyString, formatLastUsed, getStatusColor } from '@/lib/api'
import { OSIcon } from '@/components/OSIcon'
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
          Antidetect browser profile management
        </p>
      </div>
      
      <nav>
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
  const [allProfiles, setAllProfiles] = useState<Profile[]>([])
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
  const [perPage, setPerPage] = useState(() => {
    if (typeof window !== 'undefined') {
      return Number(localStorage.getItem('profiles2_per_page') || 20)
    }
    return 20
  })
  const [sortBy, setSortBy] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('profiles2_sort_by') || 'name'
    }
    return 'name'
  })
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(() => {
    if (typeof window !== 'undefined') {
      return (localStorage.getItem('profiles2_sort_order') as 'asc' | 'desc') || 'asc'
    }
    return 'asc'
  })
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [activeBrowsers, setActiveBrowsers] = useState<Set<string>>(new Set())
  const [pageInput, setPageInput] = useState<string>('1')

  const totalPages = Math.max(1, Math.ceil(totalProfiles / perPage))

  // Полная загрузка всех профилей
  const fetchAllProfiles = async () => {
    try {
      setLoading(true)
      setError(null)

      let page = 1
      const perPageFetch = 100
      let fetched: Profile[] = []
      let hasNextPage = true

      while (hasNextPage) {
        const params: Record<string, any> = {
          page,
          per_page: perPageFetch
        }

        if (statusFilter !== 'all') {
          params.status = statusFilter
        }

        const response: ProfilesResponse = await profilesAPI.getProfiles(params)
        fetched = fetched.concat(response.profiles)
        hasNextPage = response.has_next
        page += 1
      }

      setAllProfiles(fetched)

    } catch (err) {
      console.error('Ошибка загрузки профилей:', err)
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка')
    } finally {
      setLoading(false)
    }
  }

  // Сортировка и пагинация на клиенте
  const applySortingAndPaging = () => {
    // Клиентский фильтр по имени
    const filtered = searchTerm.trim()
      ? allProfiles.filter(p => p.name.toLowerCase().includes(searchTerm.toLowerCase()))
      : allProfiles

    const sorted = [...filtered].sort((a, b) => {
      const dir = sortOrder === 'asc' ? 1 : -1
      switch (sortBy) {
        case 'name':
          return a.name.localeCompare(b.name) * dir
        case 'status':
          return a.status.localeCompare(b.status) * dir
        case 'os':
          return a.browser_settings.os.localeCompare(b.browser_settings.os) * dir
        case 'last_used':
          return (new Date(a.last_used || 0).getTime() - new Date(b.last_used || 0).getTime()) * dir
        case 'group':
          return a.group.localeCompare(b.group) * dir
        case 'id':
          return a.id.localeCompare(b.id) * dir
        case 'created_at':
        default:
          return (new Date(a.created_at).getTime() - new Date(b.created_at).getTime()) * dir
      }
    })

    setTotalProfiles(sorted.length)
    setHasNext(currentPage * perPage < sorted.length)

    const startIdx = (currentPage - 1) * perPage
    const pageSlice = sorted.slice(startIdx, startIdx + perPage)
    setProfiles(pageSlice)
  }

  // Загружаем все профили при изменении фильтров поиска/статуса
  useEffect(() => {
    fetchAllProfiles()
  }, [statusFilter])

  // Применяем сортировку и пагинацию при изменении входных данных
  useEffect(() => {
    applySortingAndPaging()
  }, [allProfiles, currentPage, perPage, sortBy, sortOrder, searchTerm])

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

  // Сортировка по колонкам
  const handleSort = (column: string) => {
    if (sortBy === column) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(column)
      setSortOrder('asc')
    }
    setCurrentPage(1)
  }

  const handleOpenProfile = async (profileId: string) => {
    try {
      await profilesAPI.startProfile(profileId)
      // Обновляем список профилей и активных браузеров после запуска
      fetchAllProfiles()
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
        fetchAllProfiles()
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
        fetchAllProfiles()
      } catch (err) {
        console.error('Ошибка удаления профиля:', err)
        alert('Ошибка удаления: ' + (err instanceof Error ? err.message : 'Неизвестная ошибка'))
      }
    }
  }

  // Поиск без кнопки: фильтруем на лету, страницу сбрасываем
  useEffect(() => {
    setCurrentPage(1)
  }, [searchTerm])

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

  // Удалить выбранные профили
  const handleBulkDelete = async () => {
    if (selectedProfiles.size === 0) {
      alert('No profiles selected')
      return
    }
    if (!confirm(`Delete ${selectedProfiles.size} selected profiles?`)) {
      return
    }
    try {
      for (const id of selectedProfiles) {
        await profilesAPI.deleteProfile(id)
      }
      setSelectedProfiles(new Set())
      fetchAllProfiles()
    } catch (err) {
      console.error('Bulk delete error', err)
      alert('Error deleting profiles')
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
        fetchAllProfiles() // Перезагружаем список профилей
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

  // Синхронизируем поле ввода страницы с текущей страницей
  useEffect(() => {
    setPageInput(currentPage.toString())
  }, [currentPage])

  // Сохраняем настройки в localStorage
  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('profiles2_sort_by', sortBy)
      localStorage.setItem('profiles2_sort_order', sortOrder)
      localStorage.setItem('profiles2_per_page', perPage.toString())
    }
  }, [sortBy, sortOrder, perPage])

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
              onClick={fetchAllProfiles}
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
      
      <div style={{ marginLeft: '250px', padding: '20px', flex: 1, minHeight: '100vh', overflow: 'visible' }}>
        <div>
          <h1 style={{ color: '#fff', fontSize: '20px', fontWeight: 'bold', margin: 0 }}>
            Profiles ({totalProfiles})
          </h1>
        </div>

        {/* Фильтры и поиск */}
        <div style={{ 
          display: 'flex', 
          gap: '20px', 
          marginBottom: '15px',
          alignItems: 'center'
        }}>
          <input
            type="text"
            placeholder="Filter..."
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
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
            <option value="error">Error</option>
            <option value="pending">Pending</option>
          </select>

          {/* Кнопки управления */}
          <div style={{ display: 'flex', gap: '10px', marginLeft: 'auto' }}>
            <button
              onClick={handleCloseAllBrowsers}
              style={{
                padding: '1px 10px',
                backgroundColor: '#dc3545',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '14px'
              }}
            >
              🔒 Close all browsers
            </button>
            <button
              onClick={handleBulkDelete}
              style={{
                padding: '5px 10px',
                backgroundColor: '#814280',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '14px'
              }}
            >
              🗑️ Delete selected
            </button>
            <button
              onClick={handleExportToExcel}
              style={{
                padding: '5px 10px',
                backgroundColor: '#28a745',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '14px'
              }}
            >
              📊 Export to Excel
            </button>
            <label
              style={{
                padding: '5px 10px',
                backgroundColor: '#007bff',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '14px'
              }}
            >
              📥 Import from Excel
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
          overflow: 'visible',
          border: '1px solid #333'
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ backgroundColor: '#222' }}>
                <th
                  style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}
                >
                  <input
                    type="checkbox"
                    checked={selectedProfiles.size === profiles.length && profiles.length > 0}
                    onChange={handleSelectAll}
                    style={{ marginRight: '6px', cursor: 'pointer' }}
                    title="Select all on current page"
                  />
                  <span
                    onClick={() => handleSort('name')}
                    style={{ cursor: 'pointer' }}
                  >
                    NAME {sortBy === 'name' ? (sortOrder === 'asc' ? '▲' : '▼') : ''}
                  </span>
                </th>
                <th
                  onClick={() => handleSort('id')}
                  style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600', cursor: 'pointer' }}
                >
                  ID {sortBy === 'id' ? (sortOrder === 'asc' ? '▲' : '▼') : ''}
                </th>
                <th
                  onClick={() => handleSort('group')}
                  style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600', cursor: 'pointer' }}
                >
                  GROUP {sortBy === 'group' ? (sortOrder === 'asc' ? '▲' : '▼') : ''}
                </th>
                <th
                  onClick={() => handleSort('os')}
                  style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600', cursor: 'pointer' }}
                >
                  OS {sortBy === 'os' ? (sortOrder === 'asc' ? '▲' : '▼') : ''}
                </th>
                <th style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>PROXY</th>
                <th
                  onClick={() => handleSort('status')}
                  style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600', cursor: 'pointer' }}
                >
                  STATUS {sortBy === 'status' ? (sortOrder === 'asc' ? '▲' : '▼') : ''}
                </th>
                <th
                  onClick={() => handleSort('last_used')}
                  style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600', cursor: 'pointer' }}
                >
                  LAST USED {sortBy === 'last_used' ? (sortOrder === 'asc' ? '▲' : '▼') : ''}
                </th>
                <th style={{ padding: '10px', textAlign: 'left', color: '#ccc', fontWeight: '600' }}>ACTION</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((profile, idx) => (
                <tr key={profile.id} style={{ borderTop: '1px solid #333' }}>
                  <td style={{ padding: '6px', color: '#fff', fontSize: '13px' }}>
                    <input
                      type="checkbox"
                      checked={selectedProfiles.has(profile.id)}
                      onChange={() => handleSelectProfile(profile.id)}
                      style={{ marginRight: '6px' }}
                    />
                    {profile.name}
                  </td>
                  <td style={{ padding: '6px', color: '#fff', fontSize: '13px' }}>
                    {profile.id}
                  </td>
                  <td style={{ padding: '6px', color: '#fff', fontSize: '13px' }}>
                    {profile.group}
                  </td>
                  <td style={{ padding: '6px', color: '#fff', fontSize: '16px' }}>
                    <OSIcon os={profile.browser_settings.os} />
                  </td>
                  <td style={{ padding: '6px', color: '#888', fontSize: '13px' }}>
                    {formatProxyString(profile.proxy_config)}
                  </td>
                  <td style={{ padding: '6px' }}>
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
                  <td style={{ padding: '6px', color: '#888', fontSize: '13px' }}>
                    {formatLastUsed(profile.last_used)}
                  </td>
                  <td style={{ padding: '6px' }}>
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
                              ...(idx >= profiles.length - 3 
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
        {totalProfiles > perPage && (
          <div style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: '16px',
            marginTop: '30px',
            flexWrap: 'wrap'
          }}>
            <span style={{ color: '#ccc', fontSize: '14px' }}>
              Total: {totalProfiles}
            </span>

            <button
              onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
              disabled={currentPage === 1}
              style={{
                padding: '6px 10px',
                backgroundColor: currentPage === 1 ? '#333' : '#ff6b35',
                color: currentPage === 1 ? '#666' : 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: currentPage === 1 ? 'not-allowed' : 'pointer',
                fontSize: '14px'
              }}
            >
              ‹
            </button>

            <input
              type="number"
              min={1}
              max={totalPages}
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const num = Number(pageInput)
                  if (!isNaN(num)) {
                    const target = Math.min(Math.max(1, num), totalPages)
                    setCurrentPage(target)
                  }
                }
              }}
              onBlur={() => {
                const num = Number(pageInput)
                if (!isNaN(num)) {
                  const target = Math.min(Math.max(1, num), totalPages)
                  setCurrentPage(target)
                }
              }}
              style={{
                width: '60px',
                textAlign: 'center',
                padding: '6px 8px',
                backgroundColor: '#1a1a1a',
                border: '1px solid #555',
                borderRadius: '4px',
                color: '#fff',
                fontSize: '14px'
              }}
            />

            <span style={{ color: '#ccc', fontSize: '14px' }}>
              / {totalPages}
            </span>

            <button
              onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
              disabled={currentPage === totalPages}
              style={{
                padding: '6px 10px',
                backgroundColor: currentPage === totalPages ? '#333' : '#ff6b35',
                color: currentPage === totalPages ? '#666' : 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: currentPage === totalPages ? 'not-allowed' : 'pointer',
                fontSize: '14px'
              }}
            >
              ›
            </button>

            <select
              value={perPage}
              onChange={(e) => {
                setPerPage(Number(e.target.value))
                setCurrentPage(1)
              }}
              style={{
                padding: '6px 10px',
                backgroundColor: '#1a1a1a',
                border: '1px solid #333',
                borderRadius: '4px',
                color: '#fff',
                fontSize: '14px'
              }}
            >
              <option value={10}>10/page</option>
              <option value={20}>20/page</option>
              <option value={50}>50/page</option>
              <option value={100}>100/page</option>
            </select>
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