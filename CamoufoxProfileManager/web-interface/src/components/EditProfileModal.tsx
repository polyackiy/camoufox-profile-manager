'use client'

import React, { useState, useEffect } from 'react'
import { Profile, profilesAPI } from '@/lib/api'

interface EditProfileModalProps {
  profile: Profile | null
  isOpen: boolean
  onClose: () => void
  onSave: (updatedProfile: Profile) => void
}

export default function EditProfileModal({ profile, isOpen, onClose, onSave }: EditProfileModalProps) {
  const [formData, setFormData] = useState({
    name: '',
    group: '',
    notes: '',
    status: 'inactive' as Profile['status'],
    proxy_config: {
      type: 'http' as 'http' | 'socks5',
      server: '',
      username: '',
      password: ''
    },
    browser_settings: {
      os: 'windows' as 'windows' | 'macos' | 'linux',
      screen: '1920x1080',
      user_agent: '',
      languages: ['en-US'],
      timezone: 'UTC',
      locale: 'en_US',
      webrtc_mode: 'replace' as 'forward' | 'replace' | 'real' | 'none',
      canvas_noise: true,
      webgl_noise: true,
      audio_noise: true,
      hardware_concurrency: 4,
      device_memory: 8,
      max_touch_points: 0,
      window_width: 1280,
      window_height: 720
    },
    geolocation: {
      mode: 'auto' as 'auto' | 'manual',
      latitude: 0,
      longitude: 0,
      accuracy: 10
    }
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resettingFingerprint, setResettingFingerprint] = useState(false)

  useEffect(() => {
    if (profile && isOpen) {
      setFormData({
        name: profile.name,
        group: profile.group || '',
        notes: profile.notes || '',
        status: profile.status,
        proxy_config: {
          type: profile.proxy_config?.type || 'http',
          server: profile.proxy_config?.server || '',
          username: profile.proxy_config?.username || '',
          password: profile.proxy_config?.password || ''
        },
        browser_settings: {
          os: profile.browser_settings?.os || 'windows',
          screen: profile.browser_settings?.screen || '1920x1080',
          user_agent: profile.browser_settings?.user_agent || '',
          languages: profile.browser_settings?.languages || ['en-US'],
          timezone: profile.browser_settings?.timezone || 'UTC',
          locale: profile.browser_settings?.locale || 'en_US',
          webrtc_mode: profile.browser_settings?.webrtc_mode || 'replace',
          canvas_noise: profile.browser_settings?.canvas_noise ?? true,
          webgl_noise: profile.browser_settings?.webgl_noise ?? true,
          audio_noise: profile.browser_settings?.audio_noise ?? true,
          hardware_concurrency: profile.browser_settings?.hardware_concurrency || 4,
          device_memory: profile.browser_settings?.device_memory || 8,
          max_touch_points: profile.browser_settings?.max_touch_points || 0,
          window_width: profile.browser_settings?.window_width || 1280,
          window_height: profile.browser_settings?.window_height || 720
        },
        geolocation: {
          mode: 'auto',
          latitude: profile.browser_settings?.geolocation?.lat || 0,
          longitude: profile.browser_settings?.geolocation?.lon || 0,
          accuracy: 10
        }
      })
      setError(null)
    }
  }, [profile, isOpen])

  const handleResetFingerprint = async () => {
    if (!profile) return

    setResettingFingerprint(true)
    setError(null)

    try {
      const updatedProfile = await profilesAPI.resetFingerprint(profile.id)
      onSave(updatedProfile)
      
      setFormData(prev => ({
        ...prev,
        browser_settings: {
          ...prev.browser_settings,
          os: updatedProfile.browser_settings?.os || 'windows',
          screen: updatedProfile.browser_settings?.screen || '1920x1080',
          user_agent: updatedProfile.browser_settings?.user_agent || '',
          languages: updatedProfile.browser_settings?.languages || ['en-US'],
          timezone: updatedProfile.browser_settings?.timezone || 'UTC',
          locale: updatedProfile.browser_settings?.locale || 'en_US',
          webrtc_mode: updatedProfile.browser_settings?.webrtc_mode || 'replace',
          canvas_noise: updatedProfile.browser_settings?.canvas_noise ?? true,
          webgl_noise: updatedProfile.browser_settings?.webgl_noise ?? true,
          audio_noise: updatedProfile.browser_settings?.audio_noise ?? true,
          hardware_concurrency: updatedProfile.browser_settings?.hardware_concurrency || 4,
          device_memory: updatedProfile.browser_settings?.device_memory || 8,
          max_touch_points: updatedProfile.browser_settings?.max_touch_points || 0,
          window_width: updatedProfile.browser_settings?.window_width || 1280,
          window_height: updatedProfile.browser_settings?.window_height || 720
        }
      }))
    } catch (err) {
      console.error('Ошибка сброса отпечатка:', err)
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка')
    } finally {
      setResettingFingerprint(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!profile) return

    setLoading(true)
    setError(null)

    try {
      const updateData: Record<string, any> = {
        name: formData.name,
        group: formData.group || null,
        notes: formData.notes || null,
        status: formData.status,
        
        // Настройки браузера
        browser_os: formData.browser_settings.os,
        browser_screen: formData.browser_settings.screen,
        browser_user_agent: formData.browser_settings.user_agent || null,
        browser_languages: formData.browser_settings.languages,
        browser_timezone: formData.browser_settings.timezone,
        browser_locale: formData.browser_settings.locale,
        browser_webrtc_mode: formData.browser_settings.webrtc_mode,
        browser_canvas_noise: formData.browser_settings.canvas_noise,
        browser_webgl_noise: formData.browser_settings.webgl_noise,
        browser_audio_noise: formData.browser_settings.audio_noise,
        browser_hardware_concurrency: formData.browser_settings.hardware_concurrency,
        browser_device_memory: formData.browser_settings.device_memory,
        browser_max_touch_points: formData.browser_settings.max_touch_points,
        browser_window_width: formData.browser_settings.window_width,
        browser_window_height: formData.browser_settings.window_height
      }

      // Настройки прокси
      if (formData.proxy_config.server.trim()) {
        updateData.proxy_config = {
          type: formData.proxy_config.type,
          server: formData.proxy_config.server.trim(),
          username: formData.proxy_config.username.trim() || undefined,
          password: formData.proxy_config.password.trim() || undefined
        }
      } else {
        updateData.proxy_config = null
      }

      // Настройки геолокации
      if (formData.geolocation.mode === 'manual') {
        // Обновляем browser_settings с геолокацией
        updateData.browser_settings = {
          ...formData.browser_settings,
          geolocation: {
            lat: formData.geolocation.latitude,
            lon: formData.geolocation.longitude,
            accuracy: formData.geolocation.accuracy
          }
        }
      } else {
        // Режим "auto" - геолокация будет определена автоматически
        updateData.browser_settings = {
          ...formData.browser_settings,
          geolocation: null
        }
      }

      const updatedProfile = await profilesAPI.updateProfile(profile.id, updateData)
      onSave(updatedProfile)
      onClose()
    } catch (err) {
      console.error('Ошибка обновления профиля:', err)
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка')
    } finally {
      setLoading(false)
    }
  }

  if (!isOpen) return null

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.8)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000
    }}>
      <div style={{
        backgroundColor: '#1a1a1a',
        border: '1px solid #333',
        borderRadius: '12px',
        width: '90%',
        maxWidth: '600px',
        maxHeight: '90vh',
        overflow: 'auto',
        boxShadow: '0 20px 40px rgba(0, 0, 0, 0.5)'
      }}>
        <div style={{
          padding: '20px 24px',
          borderBottom: '1px solid #333',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center'
        }}>
          <h2 style={{ color: '#fff', margin: 0, fontSize: '20px', fontWeight: '600' }}>
            Редактировать профиль
          </h2>
          <button
            onClick={onClose}
            disabled={loading}
            style={{
              background: 'none',
              border: 'none',
              color: '#888',
              fontSize: '24px',
              cursor: loading ? 'not-allowed' : 'pointer',
              padding: '0',
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: '6px'
            }}
          >
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ padding: '24px' }}>
          {error && (
            <div style={{
              backgroundColor: '#ff4444',
              color: 'white',
              padding: '12px',
              borderRadius: '6px',
              marginBottom: '20px'
            }}>
              {error}
            </div>
          )}

          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
              Название профиля *
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              required
              disabled={loading}
              style={{
                width: '100%',
                padding: '10px 12px',
                backgroundColor: '#2a2a2a',
                border: '1px solid #444',
                borderRadius: '6px',
                color: '#fff'
              }}
            />
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Группа
              </label>
              <input
                type="text"
                value={formData.group}
                onChange={(e) => setFormData({ ...formData, group: e.target.value })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Операционная система
              </label>
              <select
                value={formData.browser_settings.os}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, os: e.target.value as 'windows' | 'macos' | 'linux' }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              >
                <option value="windows">🖥️ Windows</option>
                <option value="macos">💻 macOS</option>
                <option value="linux">🐧 Linux</option>
              </select>
            </div>
          </div>

          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
              Статус
            </label>
            <select
              value={formData.status}
              onChange={(e) => setFormData({ ...formData, status: e.target.value as Profile['status'] })}
              disabled={loading}
              style={{
                width: '100%',
                padding: '10px 12px',
                backgroundColor: '#2a2a2a',
                border: '1px solid #444',
                borderRadius: '6px',
                color: '#fff'
              }}
            >
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="error">Error</option>
              <option value="pending">Pending</option>
            </select>
          </div>

          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
              Заметки
            </label>
            <textarea
              value={formData.notes}
              onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
              disabled={loading}
              rows={3}
              style={{
                width: '100%',
                padding: '10px 12px',
                backgroundColor: '#2a2a2a',
                border: '1px solid #444',
                borderRadius: '6px',
                color: '#fff',
                resize: 'vertical'
              }}
            />
          </div>

          <h3 style={{ color: '#fff', fontSize: '16px', marginBottom: '16px' }}>
            Настройки прокси
          </h3>
          
          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Тип прокси
              </label>
              <select
                value={formData.proxy_config.type}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  proxy_config: { ...formData.proxy_config, type: e.target.value as 'http' | 'socks5' }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              >
                <option value="http">HTTP</option>
                <option value="socks5">SOCKS5</option>
              </select>
            </div>
            <div style={{ flex: 2 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Сервер (host:port)
              </label>
              <input
                type="text"
                value={formData.proxy_config.server}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  proxy_config: { ...formData.proxy_config, server: e.target.value }
                })}
                placeholder="proxy.example.com:8080"
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Логин
              </label>
              <input
                type="text"
                value={formData.proxy_config.username}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  proxy_config: { ...formData.proxy_config, username: e.target.value }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Пароль
              </label>
              <input
                type="password"
                value={formData.proxy_config.password}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  proxy_config: { ...formData.proxy_config, password: e.target.value }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <h3 style={{ color: '#fff', fontSize: '16px', marginBottom: '16px' }}>
            Настройки геолокации
          </h3>
          
          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
              Режим геолокации
            </label>
            <select
              value={formData.geolocation.mode}
              onChange={(e) => setFormData({ 
                ...formData, 
                geolocation: { ...formData.geolocation, mode: e.target.value as 'auto' | 'manual' }
              })}
              disabled={loading}
              style={{
                width: '100%',
                padding: '10px 12px',
                backgroundColor: '#2a2a2a',
                border: '1px solid #444',
                borderRadius: '6px',
                color: '#fff'
              }}
            >
              <option value="auto">На основе IP</option>
              <option value="manual">Задать вручную</option>
            </select>
          </div>

          {formData.geolocation.mode === 'manual' && (
            <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                  Широта (Latitude)
                </label>
                <input
                  type="number"
                  value={formData.geolocation.latitude}
                  onChange={(e) => setFormData({ 
                    ...formData, 
                    geolocation: { ...formData.geolocation, latitude: parseFloat(e.target.value) || 0 }
                  })}
                  step="0.000001"
                  min="-90"
                  max="90"
                  placeholder="55.7558"
                  disabled={loading}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    backgroundColor: '#2a2a2a',
                    border: '1px solid #444',
                    borderRadius: '6px',
                    color: '#fff'
                  }}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                  Долгота (Longitude)
                </label>
                <input
                  type="number"
                  value={formData.geolocation.longitude}
                  onChange={(e) => setFormData({ 
                    ...formData, 
                    geolocation: { ...formData.geolocation, longitude: parseFloat(e.target.value) || 0 }
                  })}
                  step="0.000001"
                  min="-180"
                  max="180"
                  placeholder="37.6176"
                  disabled={loading}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    backgroundColor: '#2a2a2a',
                    border: '1px solid #444',
                    borderRadius: '6px',
                    color: '#fff'
                  }}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                  Точность (м)
                </label>
                <input
                  type="number"
                  value={formData.geolocation.accuracy}
                  onChange={(e) => setFormData({ 
                    ...formData, 
                    geolocation: { ...formData.geolocation, accuracy: parseInt(e.target.value) || 10 }
                  })}
                  min="1"
                  max="10000"
                  placeholder="10"
                  disabled={loading}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    backgroundColor: '#2a2a2a',
                    border: '1px solid #444',
                    borderRadius: '6px',
                    color: '#fff'
                  }}
                />
              </div>
            </div>
          )}

          <h3 style={{ color: '#fff', fontSize: '16px', marginBottom: '16px' }}>
            Настройки браузера
          </h3>
          
          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Разрешение экрана
              </label>
              <input
                type="text"
                value={formData.browser_settings.screen}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, screen: e.target.value }
                })}
                placeholder="1920x1080"
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                📐 Ширина окна браузера
              </label>
              <input
                type="number"
                value={formData.browser_settings.window_width}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, window_width: parseInt(e.target.value) || 1280 }
                })}
                min="800"
                max="3840"
                placeholder="1280"
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                📏 Высота окна браузера
              </label>
              <input
                type="number"
                value={formData.browser_settings.window_height}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, window_height: parseInt(e.target.value) || 720 }
                })}
                min="600"
                max="2160"
                placeholder="720"
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                User-Agent
              </label>
              <input
                type="text"
                value={formData.browser_settings.user_agent}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, user_agent: e.target.value }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Языки
              </label>
              <input
                type="text"
                value={formData.browser_settings.languages.join(', ')}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, languages: e.target.value.split(',').map(lang => lang.trim()) }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Часовой пояс
              </label>
              <input
                type="text"
                value={formData.browser_settings.timezone}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, timezone: e.target.value }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Локализация
              </label>
              <input
                type="text"
                value={formData.browser_settings.locale}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, locale: e.target.value }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Режим WebRTC
              </label>
              <select
                value={formData.browser_settings.webrtc_mode}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, webrtc_mode: e.target.value as 'forward' | 'replace' | 'real' | 'none' }
                })}
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              >
                <option value="forward">Forward</option>
                <option value="replace">Replace</option>
                <option value="real">Real</option>
                <option value="none">None</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Canvas Noise
              </label>
              <input
                type="checkbox"
                checked={formData.browser_settings.canvas_noise}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, canvas_noise: e.target.checked }
                })}
                disabled={loading}
                style={{ marginRight: '8px' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                WebGL Noise
              </label>
              <input
                type="checkbox"
                checked={formData.browser_settings.webgl_noise}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, webgl_noise: e.target.checked }
                })}
                disabled={loading}
                style={{ marginRight: '8px' }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Audio Noise
              </label>
              <input
                type="checkbox"
                checked={formData.browser_settings.audio_noise}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, audio_noise: e.target.checked }
                })}
                disabled={loading}
                style={{ marginRight: '8px' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Hardware Concurrency
              </label>
              <input
                type="number"
                value={formData.browser_settings.hardware_concurrency}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, hardware_concurrency: parseInt(e.target.value) || 4 }
                })}
                min="1"
                max="16"
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Device Memory
              </label>
              <input
                type="number"
                value={formData.browser_settings.device_memory}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, device_memory: parseInt(e.target.value) || 8 }
                })}
                min="1"
                max="16"
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', color: '#ccc', marginBottom: '6px' }}>
                Max Touch Points
              </label>
              <input
                type="number"
                value={formData.browser_settings.max_touch_points}
                onChange={(e) => setFormData({ 
                  ...formData, 
                  browser_settings: { ...formData.browser_settings, max_touch_points: parseInt(e.target.value) || 0 }
                })}
                min="0"
                max="10"
                disabled={loading}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: '#2a2a2a',
                  border: '1px solid #444',
                  borderRadius: '6px',
                  color: '#fff'
                }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
            <button
              type="button"
              onClick={handleResetFingerprint}
              disabled={loading || resettingFingerprint}
              style={{
                padding: '10px 20px',
                backgroundColor: resettingFingerprint ? '#666' : '#ffa500',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: resettingFingerprint ? 'not-allowed' : 'pointer',
                fontWeight: '500',
                transition: 'background-color 0.2s'
              }}
              onMouseEnter={(e) => {
                if (!resettingFingerprint) {
                  e.currentTarget.style.backgroundColor = '#e5940f'
                }
              }}
              onMouseLeave={(e) => {
                if (!resettingFingerprint) {
                  e.currentTarget.style.backgroundColor = '#ffa500'
                }
              }}
            >
              {resettingFingerprint ? '🔄 Сброс...' : '🔄 Сбросить отпечаток'}
            </button>
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              style={{
                padding: '10px 20px',
                backgroundColor: '#666',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontWeight: '500',
                transition: 'background-color 0.2s'
              }}
              onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#777'}
              onMouseLeave={(e) => e.currentTarget.style.backgroundColor = '#666'}
            >
              Отмена
            </button>
            <button
              type="submit"
              disabled={loading}
              style={{
                padding: '10px 20px',
                backgroundColor: loading ? '#666' : '#ff6b35',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: loading ? 'not-allowed' : 'pointer',
                fontWeight: '500',
                transition: 'background-color 0.2s'
              }}
              onMouseEnter={(e) => {
                if (!loading) {
                  e.currentTarget.style.backgroundColor = '#e55a2b'
                }
              }}
              onMouseLeave={(e) => {
                if (!loading) {
                  e.currentTarget.style.backgroundColor = '#ff6b35'
                }
              }}
            >
              {loading ? 'Сохранение...' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
} 