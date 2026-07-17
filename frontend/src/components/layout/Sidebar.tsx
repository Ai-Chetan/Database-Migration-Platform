import React from 'react'
import { NavLink } from 'react-router-dom'
import { ChevronsLeft, ChevronsRight, Database } from 'lucide-react'
import { cn } from '@/utils/cn'
import { useUIStore } from '@/store/ui'
import { usePermission } from '@/hooks/usePermission'
import { NAV_GROUPS, type NavItem } from '@/utils/navigation'

function NavItemLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const allowed = usePermission(item.permission)
  if (!allowed) return null

  return (
    <NavLink
      to={item.to}
      end={item.to === '/app/dashboard'}
      className={({ isActive }) =>
        cn(
          'group relative flex items-center gap-3 rounded px-3 py-2 text-small font-medium transition-colors',
          isActive ? 'bg-sidebar-active text-sidebar-activeText' : 'text-sidebar-text hover:bg-sidebar-active/60 hover:text-sidebar-activeText'
        )
      }
      title={collapsed ? item.label : undefined}
    >
      {({ isActive }) => (
        <>
          <span
            className={cn(
              'absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-action transition-opacity',
              isActive ? 'opacity-100' : 'opacity-0'
            )}
          />
          <item.icon className="h-4 w-4 shrink-0" />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  )
}

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useUIStore()

  return (
    <aside
      className={cn(
        'flex h-screen shrink-0 flex-col bg-sidebar-bg transition-all duration-200',
        sidebarCollapsed ? 'w-16' : 'w-64'
      )}
    >
      <div className="flex h-14 items-center gap-2 px-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-action">
          <Database className="h-4 w-4 text-white" />
        </div>
        {!sidebarCollapsed && (
          <span className="truncate text-body font-semibold text-white">Migration Platform</span>
        )}
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-2 scrollbar-thin">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            {!sidebarCollapsed && (
              <p className="mb-1.5 px-3 text-tiny font-semibold uppercase tracking-wide text-sidebar-text/60">
                {group.label}
              </p>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavItemLink key={item.to} item={item} collapsed={sidebarCollapsed} />
              ))}
            </div>
          </div>
        ))}
      </nav>

      <button
        onClick={toggleSidebar}
        className="flex h-11 items-center justify-center gap-2 border-t border-white/10 text-sidebar-text hover:text-sidebar-activeText"
      >
        {sidebarCollapsed ? <ChevronsRight className="h-4 w-4" /> : (
          <>
            <ChevronsLeft className="h-4 w-4" /> <span className="text-small">Collapse</span>
          </>
        )}
      </button>
    </aside>
  )
}
