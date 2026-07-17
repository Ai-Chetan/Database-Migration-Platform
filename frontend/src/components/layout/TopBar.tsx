import React, { useState } from 'react'
import { Bell, ChevronDown, LogOut, User as UserIcon, Search } from 'lucide-react'
import { useAuth } from '@/hooks/useAuth'
import { ROLE_LABELS } from '@/utils/permissions'
import { cn } from '@/utils/cn'
import { useUIStore } from '@/store/ui'

export function TopBar() {
  const { user, logout } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const setCommandPaletteOpen = useUIStore((s) => s.setCommandPaletteOpen)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-white px-6">
      <button
        onClick={() => setCommandPaletteOpen(true)}
        className="flex items-center gap-2 rounded border border-border bg-surface px-3 py-1.5 text-small text-text-tertiary transition-colors hover:border-border-strong hover:text-text-secondary"
      >
        <Search className="h-3.5 w-3.5" />
        <span>Search…</span>
        <kbd className="ml-6 rounded border border-border bg-white px-1.5 py-0.5 text-tiny text-text-tertiary">
          ⌘K
        </kbd>
      </button>

      <div className="flex items-center gap-4">
        <button className="relative text-text-secondary hover:text-text-primary">
          <Bell className="h-5 w-5" />
        </button>

        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="flex items-center gap-2 rounded px-2 py-1.5 hover:bg-surface"
          >
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-action/10 text-small font-semibold text-action">
              {user?.name?.[0]?.toUpperCase() ?? <UserIcon className="h-4 w-4" />}
            </div>
            <div className="hidden text-left sm:block">
              <p className="text-small font-medium text-text-primary leading-tight">{user?.name}</p>
              <p className="text-tiny text-text-tertiary leading-tight">
                {user ? ROLE_LABELS[user.role] : ''}
              </p>
            </div>
            <ChevronDown className="h-4 w-4 text-text-tertiary" />
          </button>

          {menuOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
              <div
                className={cn(
                  'absolute right-0 z-20 mt-2 w-48 rounded border border-border bg-white py-1 shadow-sm'
                )}
              >
                <button
                  onClick={logout}
                  className="flex w-full items-center gap-2 px-4 py-2 text-left text-small text-text-primary hover:bg-surface"
                >
                  <LogOut className="h-4 w-4" /> Log out
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
