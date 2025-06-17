'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import {
  MagnifyingGlassIcon,
  PlusIcon,
  ArrowUpTrayIcon,
  ArrowDownTrayIcon,
  BellIcon,
  UserCircleIcon,
  Bars3Icon,
} from '@heroicons/react/24/outline'
import { ChevronDownIcon } from '@heroicons/react/24/solid'

interface TopBarProps {
  title: string
  onMobileMenuToggle: () => void
  sidebarCollapsed: boolean
  searchValue?: string
  onSearchChange?: (value: string) => void
  showNewButton?: boolean
  onNewClick?: () => void
  showBulkCreate?: boolean
  onBulkCreateClick?: () => void
  showImportExport?: boolean
  onImportClick?: () => void
  onExportClick?: () => void
}

export function TopBar({
  title,
  onMobileMenuToggle,
  sidebarCollapsed,
  searchValue = '',
  onSearchChange,
  showNewButton = true,
  onNewClick,
  showBulkCreate = true,
  onBulkCreateClick,
  showImportExport = true,
  onImportClick,
  onExportClick,
}: TopBarProps) {
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)

  return (
    <div
      className={cn(
        'fixed top-0 right-0 z-30 h-16 bg-dark-800 border-b border-dark-700 transition-all duration-300',
        sidebarCollapsed ? 'left-16' : 'left-64'
      )}
    >
      <div className="flex items-center justify-between h-full px-4 lg:px-6">
        {/* Left section */}
        <div className="flex items-center space-x-4">
          {/* Mobile menu button */}
          <button
            onClick={onMobileMenuToggle}
            className="lg:hidden p-2 rounded-adspower text-dark-400 hover:text-white hover:bg-dark-700 transition-colors"
          >
            <Bars3Icon className="w-5 h-5" />
          </button>

          {/* Page title */}
          <h1 className="text-xl font-semibold text-white">{title}</h1>
        </div>

        {/* Center section - Search */}
        {onSearchChange && (
          <div className="hidden md:flex flex-1 max-w-md mx-8">
            <div className="relative w-full">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <MagnifyingGlassIcon className="h-5 w-5 text-dark-400" />
              </div>
              <input
                type="text"
                placeholder="Поиск профилей..."
                value={searchValue}
                onChange={(e) => onSearchChange(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-dark-700 border border-dark-600 rounded-adspower text-white placeholder-dark-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-colors"
              />
            </div>
          </div>
        )}

        {/* Right section */}
        <div className="flex items-center space-x-2">
          {/* Action buttons */}
          <div className="hidden sm:flex items-center space-x-2">
            {showNewButton && (
              <button
                onClick={onNewClick}
                className="inline-flex items-center px-4 py-2 bg-primary-500 hover:bg-primary-600 text-white font-medium rounded-adspower transition-colors shadow-adspower"
              >
                <PlusIcon className="w-4 h-4 mr-2" />
                New Profile
              </button>
            )}

            {showBulkCreate && (
              <button
                onClick={onBulkCreateClick}
                className="inline-flex items-center px-3 py-2 bg-dark-700 hover:bg-dark-600 text-dark-300 hover:text-white border border-dark-600 rounded-adspower transition-colors"
              >
                Bulk Create
              </button>
            )}

            {showImportExport && (
              <div className="flex space-x-1">
                <button
                  onClick={onImportClick}
                  className="p-2 bg-dark-700 hover:bg-dark-600 text-dark-300 hover:text-white border border-dark-600 rounded-adspower transition-colors"
                  title="Import"
                >
                  <ArrowDownTrayIcon className="w-4 h-4" />
                </button>
                <button
                  onClick={onExportClick}
                  className="p-2 bg-dark-700 hover:bg-dark-600 text-dark-300 hover:text-white border border-dark-600 rounded-adspower transition-colors"
                  title="Export"
                >
                  <ArrowUpTrayIcon className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          {/* Notifications */}
          <div className="relative">
            <button
              onClick={() => setNotificationsOpen(!notificationsOpen)}
              className="p-2 rounded-adspower text-dark-400 hover:text-white hover:bg-dark-700 transition-colors relative"
            >
              <BellIcon className="w-5 h-5" />
              <span className="absolute top-1 right-1 w-2 h-2 bg-primary-500 rounded-full"></span>
            </button>

            {/* Notifications dropdown */}
            {notificationsOpen && (
              <div className="absolute right-0 mt-2 w-80 bg-dark-800 border border-dark-700 rounded-adspower shadow-adspower z-50">
                <div className="p-4 border-b border-dark-700">
                  <h3 className="text-sm font-medium text-white">Notifications</h3>
                </div>
                <div className="p-2">
                  <div className="text-center py-8 text-dark-400 text-sm">
                    No new notifications
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* User menu */}
          <div className="relative">
            <button
              onClick={() => setUserMenuOpen(!userMenuOpen)}
              className="flex items-center space-x-2 p-2 rounded-adspower text-dark-400 hover:text-white hover:bg-dark-700 transition-colors"
            >
              <UserCircleIcon className="w-6 h-6" />
              <ChevronDownIcon className="w-4 h-4" />
            </button>

            {/* User dropdown */}
            {userMenuOpen && (
              <div className="absolute right-0 mt-2 w-48 bg-dark-800 border border-dark-700 rounded-adspower shadow-adspower z-50">
                <div className="p-2">
                  <div className="px-3 py-2 border-b border-dark-700 mb-2">
                    <div className="text-sm font-medium text-white">Admin User</div>
                    <div className="text-xs text-dark-400">admin@camoufox.com</div>
                  </div>
                  <a
                    href="/profile"
                    className="block px-3 py-2 text-sm text-dark-300 hover:text-white hover:bg-dark-700 rounded-adspower transition-colors"
                  >
                    Profile Settings
                  </a>
                  <a
                    href="/settings"
                    className="block px-3 py-2 text-sm text-dark-300 hover:text-white hover:bg-dark-700 rounded-adspower transition-colors"
                  >
                    Settings
                  </a>
                  <hr className="my-2 border-dark-700" />
                  <button
                    className="w-full text-left px-3 py-2 text-sm text-error-500 hover:bg-dark-700 rounded-adspower transition-colors"
                    onClick={() => {
                      // Handle logout
                    }}
                  >
                    Sign Out
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Mobile search bar */}
      {onSearchChange && (
        <div className="md:hidden px-4 pb-3">
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <MagnifyingGlassIcon className="h-4 w-4 text-dark-400" />
            </div>
            <input
              type="text"
              placeholder="Search profiles..."
              value={searchValue}
              onChange={(e) => onSearchChange(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-dark-700 border border-dark-600 rounded-adspower text-white placeholder-dark-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent text-sm"
            />
          </div>
        </div>
      )}

      {/* Click outside handlers */}
      {(userMenuOpen || notificationsOpen) && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => {
            setUserMenuOpen(false)
            setNotificationsOpen(false)
          }}
        />
      )}
    </div>
  )
} 