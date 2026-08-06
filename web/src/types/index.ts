// Base profile types matching our Python backend
export interface Profile {
  id: string
  name: string
  group: string
  status: 'active' | 'inactive' | 'error' | 'pending'
  platform?: string
  browser_settings: BrowserSettings
  proxy: ProxyConfig | null
  storage_path: string
  notes: string
  created_at: string
  last_opened?: string
  custom_number?: number
  tags?: string[]
}

export interface BrowserSettings {
  os: 'windows' | 'macos' | 'linux' | 'android' | 'ios'
  user_agent: string
  screen_resolution: string
  timezone: string
  language: string
  window_width?: number
  window_height?: number
  geolocation?: {
    latitude: number
    longitude: number
  }
  hardware: {
    cores: number
    memory: number
  }
  webgl_renderer: string
  canvas_fingerprint: string
}

export interface ProxyConfig {
  type: 'http' | 'https' | 'socks4' | 'socks5'
  host: string
  port: number
  username?: string
  password?: string
}

export interface ProfileGroup {
  id: string
  name: string
  color: string
  profile_count: number
}

// UI state types
export interface TableColumn {
  key: string
  label: string
  sortable: boolean
  width?: number
  visible: boolean
}

export interface TableState {
  sortBy: string
  sortOrder: 'asc' | 'desc'
  selectedRows: string[]
  currentPage: number
  itemsPerPage: number
  columns: TableColumn[]
}

export interface FilterState {
  search: string
  group: string
  status: string
  platform: string
  dateRange: {
    start?: string
    end?: string
  }
}

// API response types
export interface APIResponse<T> {
  success: boolean
  data?: T
  error?: string
  pagination?: {
    total: number
    page: number
    pages: number
    per_page: number
  }
}

export interface CreateProfileRequest {
  name: string
  group: string
  browser_settings?: Partial<BrowserSettings>
  proxy?: ProxyConfig
  notes?: string
  platform?: string
  tags?: string[]
}

export interface UpdateProfileRequest extends Partial<CreateProfileRequest> {
  id: string
}

// Dashboard statistics
export interface DashboardStats {
  total_profiles: number
  active_profiles: number
  inactive_profiles: number
  error_profiles: number
  total_groups: number
  recent_activity: RecentActivity[]
}

export interface RecentActivity {
  id: string
  profile_name: string
  action: string
  timestamp: string
  status: 'success' | 'error' | 'warning'
} 