'use client'

import { useState, useMemo, useEffect } from 'react'
import { cn, formatDate, getStatusColor, getStatusBadgeColor } from '@/lib/utils'
import type { Profile, TableState, TableColumn } from '@/types'
import {
  PlayIcon,
  PauseIcon,
  PencilIcon,
  DocumentDuplicateIcon,
  TrashIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  EyeIcon,
  ComputerDesktopIcon,
  DevicePhoneMobileIcon,
} from '@heroicons/react/24/outline'

interface ProfilesTableProps {
  profiles: Profile[]
  tableState: TableState
  onTableStateChange: (state: Partial<TableState>) => void
  onProfileAction: (profileId: string, action: string) => void
  onBulkAction: (profileIds: string[], action: string) => void
  loading?: boolean
}

const defaultColumns: TableColumn[] = [
  { key: 'select', label: '', sortable: false, width: 50, visible: true },
  { key: 'id', label: 'ID', sortable: true, width: 80, visible: true },
  { key: 'name', label: 'Name', sortable: true, width: 200, visible: true },
  { key: 'platform', label: 'Platform', sortable: true, width: 120, visible: true },
  { key: 'proxy', label: 'Proxy', sortable: false, width: 150, visible: true },
  { key: 'os', label: 'OS', sortable: true, width: 100, visible: true },
  { key: 'browser', label: 'Browser', sortable: false, width: 100, visible: true },
  { key: 'status', label: 'Status', sortable: true, width: 100, visible: true },
  { key: 'last_opened', label: 'Last Opened', sortable: true, width: 150, visible: true },
  { key: 'group', label: 'Group', sortable: true, width: 120, visible: true },
  { key: 'actions', label: 'Actions', sortable: false, width: 150, visible: true },
]

export function ProfilesTable({
  profiles,
  tableState,
  onTableStateChange,
  onProfileAction,
  onBulkAction,
  loading = false,
}: ProfilesTableProps) {
  const [resizingColumn, setResizingColumn] = useState<string | null>(null)

  // Merge default columns with table state columns
  const columns = useMemo(() => {
    return defaultColumns.map(defaultCol => {
      const stateCol = tableState.columns.find(col => col.key === defaultCol.key)
      return stateCol ? { ...defaultCol, ...stateCol } : defaultCol
    }).filter(col => col.visible)
  }, [tableState.columns])

  // Sort profiles
  const sortedProfiles = useMemo(() => {
    if (!tableState.sortBy) return profiles

    return [...profiles].sort((a, b) => {
      let aVal: any = a[tableState.sortBy as keyof Profile]
      let bVal: any = b[tableState.sortBy as keyof Profile]

      // Handle nested properties
      if (tableState.sortBy === 'os') {
        aVal = a.browser_settings.os
        bVal = b.browser_settings.os
      }

      // Handle different data types
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        aVal = aVal.toLowerCase()
        bVal = bVal.toLowerCase()
      }

      if (aVal < bVal) return tableState.sortOrder === 'asc' ? -1 : 1
      if (aVal > bVal) return tableState.sortOrder === 'asc' ? 1 : -1
      return 0
    })
  }, [profiles, tableState.sortBy, tableState.sortOrder])

  const handleSort = (columnKey: string) => {
    const column = columns.find(col => col.key === columnKey)
    if (!column?.sortable) return

    onTableStateChange({
      sortBy: columnKey,
      sortOrder: tableState.sortBy === columnKey && tableState.sortOrder === 'asc' ? 'desc' : 'asc',
    })
  }

  const handleSelectAll = () => {
    const allSelected = tableState.selectedRows.length === profiles.length
    onTableStateChange({
      selectedRows: allSelected ? [] : profiles.map(p => p.id),
    })
  }

  const handleSelectRow = (profileId: string) => {
    const isSelected = tableState.selectedRows.includes(profileId)
    const newSelected = isSelected
      ? tableState.selectedRows.filter(id => id !== profileId)
      : [...tableState.selectedRows, profileId]
    
    onTableStateChange({ selectedRows: newSelected })
  }

  return (
    <div className="bg-dark-800 border border-dark-700 rounded-adspower overflow-hidden">
      {/* Bulk actions bar */}
      {tableState.selectedRows.length > 0 && (
        <div className="flex items-center justify-between px-4 py-3 bg-dark-700 border-b border-dark-600">
          <div className="flex items-center space-x-4">
            <span className="text-sm text-white">
              {tableState.selectedRows.length} selected
            </span>
            <div className="flex space-x-2">
              <button
                onClick={() => onBulkAction(tableState.selectedRows, 'start')}
                className="px-3 py-1.5 bg-success-500 hover:bg-success-600 text-white text-sm rounded-adspower transition-colors"
              >
                Start
              </button>
              <button
                onClick={() => onBulkAction(tableState.selectedRows, 'stop')}
                className="px-3 py-1.5 bg-warning-500 hover:bg-warning-600 text-white text-sm rounded-adspower transition-colors"
              >
                Stop
              </button>
              <button
                onClick={() => onBulkAction(tableState.selectedRows, 'delete')}
                className="px-3 py-1.5 bg-error-500 hover:bg-error-600 text-white text-sm rounded-adspower transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
          <button
            onClick={() => onTableStateChange({ selectedRows: [] })}
            className="text-dark-400 hover:text-white transition-colors"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-dark-700 border-b border-dark-600">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className="relative px-4 py-3 text-left text-xs font-medium text-dark-300 uppercase tracking-wider select-none"
                  style={{ width: column.width }}
                >
                  <div className="flex items-center justify-between">
                    {column.key === 'select' ? (
                      <input
                        type="checkbox"
                        checked={tableState.selectedRows.length === profiles.length && profiles.length > 0}
                        onChange={handleSelectAll}
                        className="w-4 h-4 text-primary-500 bg-dark-600 border-dark-500 rounded focus:ring-primary-500 focus:ring-2"
                      />
                    ) : (
                      <button
                        onClick={() => handleSort(column.key)}
                        className={cn(
                          'flex items-center space-x-1 text-left',
                          column.sortable ? 'hover:text-white cursor-pointer' : 'cursor-default'
                        )}
                      >
                        <span>{column.label}</span>
                        {column.sortable && tableState.sortBy === column.key && (
                          tableState.sortOrder === 'asc' ? (
                            <ChevronUpIcon className="w-4 h-4" />
                          ) : (
                            <ChevronDownIcon className="w-4 h-4" />
                          )
                        )}
                      </button>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="bg-dark-800 divide-y divide-dark-700">
            {loading ? (
              // Loading skeleton
              Array.from({ length: 5 }).map((_, index) => (
                <tr key={index} className="animate-pulse">
                  {columns.map((column) => (
                    <td key={column.key} className="px-4 py-4">
                      <div className="h-4 bg-dark-700 rounded"></div>
                    </td>
                  ))}
                </tr>
              ))
            ) : sortedProfiles.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-4 py-12 text-center text-dark-400">
                  No profiles found
                </td>
              </tr>
            ) : (
              sortedProfiles.map((profile, rowIndex) => (
                <tr
                  key={profile.id}
                  className={cn(
                    'hover:bg-dark-700 transition-colors',
                    rowIndex % 2 === 0 ? 'bg-dark-800' : 'bg-dark-750'
                  )}
                >
                  {columns.map((column) => (
                    <td key={column.key} className="px-4 py-3 text-sm">
                      {(() => {
                        switch (column.key) {
                          case 'select':
                            return (
                              <input
                                type="checkbox"
                                checked={tableState.selectedRows.includes(profile.id)}
                                onChange={() => handleSelectRow(profile.id)}
                                className="w-4 h-4 text-primary-500 bg-dark-600 border-dark-500 rounded focus:ring-primary-500 focus:ring-2"
                              />
                            )
                          case 'id':
                            return (
                              <div className="text-dark-300 font-mono">
                                {profile.custom_number || profile.id.slice(-6)}
                              </div>
                            )
                          case 'name':
                            return (
                              <div className="text-white font-medium">
                                {profile.name || 'Unnamed Profile'}
                              </div>
                            )
                          case 'platform':
                            return (
                              <div className="text-dark-300">
                                {profile.platform || 'None'}
                              </div>
                            )
                          case 'proxy':
                            return (
                              <div className="text-dark-300">
                                {profile.proxy ? (
                                  <span className="text-success-500">
                                    {profile.proxy.host}:{profile.proxy.port}
                                  </span>
                                ) : (
                                  <span className="text-dark-500">No proxy</span>
                                )}
                              </div>
                            )
                          case 'os':
                            return (
                              <div className="flex items-center space-x-2">
                                {profile.browser_settings.os === 'android' || profile.browser_settings.os === 'ios' ? (
                                  <DevicePhoneMobileIcon className="w-4 h-4" />
                                ) : (
                                  <ComputerDesktopIcon className="w-4 h-4" />
                                )}
                                <span className="text-dark-300 capitalize">
                                  {profile.browser_settings.os}
                                </span>
                              </div>
                            )
                          case 'browser':
                            return (
                              <div className="text-dark-300">
                                {profile.browser_settings.user_agent.includes('Chrome') ? 'Chrome' : 'Firefox'}
                              </div>
                            )
                          case 'status':
                            return (
                              <div className="flex items-center space-x-2">
                                <div
                                  className={cn(
                                    'w-2 h-2 rounded-full',
                                    getStatusBadgeColor(profile.status)
                                  )}
                                />
                                <span className={cn('capitalize', getStatusColor(profile.status))}>
                                  {profile.status}
                                </span>
                              </div>
                            )
                          case 'last_opened':
                            return (
                              <div className="text-dark-300">
                                {profile.last_opened ? formatDate(profile.last_opened) : 'Never'}
                              </div>
                            )
                          case 'group':
                            return (
                              <div className="text-dark-300">
                                {profile.group || 'Default'}
                              </div>
                            )
                          case 'actions':
                            return (
                              <div className="flex items-center space-x-1">
                                <button
                                  onClick={() => onProfileAction(profile.id, profile.status === 'active' ? 'stop' : 'start')}
                                  className="p-1.5 rounded-adspower text-dark-400 hover:text-white hover:bg-dark-600 transition-colors"
                                  title={profile.status === 'active' ? 'Stop' : 'Start'}
                                >
                                  {profile.status === 'active' ? (
                                    <PauseIcon className="w-4 h-4" />
                                  ) : (
                                    <PlayIcon className="w-4 h-4" />
                                  )}
                                </button>
                                <button
                                  onClick={() => onProfileAction(profile.id, 'edit')}
                                  className="p-1.5 rounded-adspower text-dark-400 hover:text-white hover:bg-dark-600 transition-colors"
                                  title="Edit"
                                >
                                  <PencilIcon className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => onProfileAction(profile.id, 'clone')}
                                  className="p-1.5 rounded-adspower text-dark-400 hover:text-white hover:bg-dark-600 transition-colors"
                                  title="Clone"
                                >
                                  <DocumentDuplicateIcon className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => onProfileAction(profile.id, 'view')}
                                  className="p-1.5 rounded-adspower text-dark-400 hover:text-white hover:bg-dark-600 transition-colors"
                                  title="View Details"
                                >
                                  <EyeIcon className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => onProfileAction(profile.id, 'delete')}
                                  className="p-1.5 rounded-adspower text-dark-400 hover:text-error-500 hover:bg-dark-600 transition-colors"
                                  title="Delete"
                                >
                                  <TrashIcon className="w-4 h-4" />
                                </button>
                              </div>
                            )
                          default:
                            return null
                        }
                      })()}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
} 