import React from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Plug,
  Workflow,
  PlusCircle,
  GitCompare,
  SlidersHorizontal,
  Gauge,
  ShieldCheck,
  CalendarClock,
  FileBarChart,
  BookOpen,
  Settings as SettingsIcon,
  Users,
  ScrollText,
  ChevronsLeft,
  ChevronsRight,
  Database,
} from 'lucide-react'
import { cn } from '@/utils/cn'
import { useUIStore } from '@/store/ui'
import { usePermission } from '@/hooks/usePermission'

interface NavItem {
  to: string
  label: string
  icon: React.ElementType
  permission: string
}

interface NavGroup {
  label: string
  items: NavItem[]
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Overview',
    items: [{ to: '/app/dashboard', label: 'Dashboard', icon: LayoutDashboard, permission: 'jobs:read' }],
  },
  {
    label: 'Migrate',
    items: [
      { to: '/app/connections', label: 'Connections', icon: Plug, permission: 'connections:read' },
      { to: '/app/jobs', label: 'Migration Jobs', icon: Workflow, permission: 'jobs:read' },
      { to: '/app/jobs/new', label: 'New Migration', icon: PlusCircle, permission: 'jobs:start' },
      { to: '/app/schema', label: 'Schema Mapping', icon: GitCompare, permission: 'schema:read' },
    ],
  },
  {
    label: 'Run',
    items: [
      { to: '/app/operations', label: 'Operations Console', icon: SlidersHorizontal, permission: 'operations:read' },
      { to: '/app/simulation', label: 'Simulation', icon: Gauge, permission: 'schema:read' },
      { to: '/app/masking', label: 'Data Masking', icon: ShieldCheck, permission: 'masking:read' },
      { to: '/app/scheduler', label: 'Scheduler', icon: CalendarClock, permission: 'scheduler:read' },
    ],
  },
  {
    label: 'Insights',
    items: [
      { to: '/app/reports', label: 'Reports', icon: FileBarChart, permission: 'reports:read' },
      { to: '/app/knowledge-base', label: 'Knowledge Base', icon: BookOpen, permission: 'knowledge:read' },
    ],
  },
  {
    label: 'Admin',
    items: [
      { to: '/app/settings', label: 'Settings', icon: SettingsIcon, permission: 'users:read' },
      { to: '/app/admin/users', label: 'User Management', icon: Users, permission: 'users:read' },
      { to: '/app/admin/audit-log', label: 'Audit Log', icon: ScrollText, permission: 'audit:read' },
    ],
  },
]

function NavItemLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const allowed = usePermission(item.permission)
  if (!allowed) return null

  return (
    <NavLink
      to={item.to}
      end={item.to === '/app/dashboard'}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-3 rounded px-3 py-2 text-small font-medium transition-colors',
          isActive ? 'bg-sidebar-active text-sidebar-activeText' : 'text-sidebar-text hover:bg-sidebar-active/60 hover:text-sidebar-activeText'
        )
      }
      title={collapsed ? item.label : undefined}
    >
      <item.icon className="h-4 w-4 shrink-0" />
      {!collapsed && <span className="truncate">{item.label}</span>}
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
