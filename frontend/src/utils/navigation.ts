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
} from 'lucide-react'
import type React from 'react'

export interface NavItem {
  to: string
  label: string
  icon: React.ElementType
  permission: string
  keywords?: string
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Overview',
    items: [{ to: '/app/dashboard', label: 'Dashboard', icon: LayoutDashboard, permission: 'jobs:read' }],
  },
  {
    label: 'Migrate',
    items: [
      { to: '/app/connections', label: 'Connections', icon: Plug, permission: 'connections:read' },
      { to: '/app/jobs', label: 'Migration Jobs', icon: Workflow, permission: 'jobs:read' },
      {
        to: '/app/jobs/new',
        label: 'New Migration',
        icon: PlusCircle,
        permission: 'jobs:start',
        keywords: 'create start wizard',
      },
      { to: '/app/schema', label: 'Schema Mapping', icon: GitCompare, permission: 'schema:read' },
    ],
  },
  {
    label: 'Run',
    items: [
      {
        to: '/app/operations',
        label: 'Operations Console',
        icon: SlidersHorizontal,
        permission: 'operations:read',
        keywords: 'workers live monitor',
      },
      { to: '/app/simulation', label: 'Simulation', icon: Gauge, permission: 'schema:read', keywords: 'sweep workers' },
      { to: '/app/masking', label: 'Data Masking', icon: ShieldCheck, permission: 'masking:read', keywords: 'pii redact' },
      { to: '/app/scheduler', label: 'Scheduler', icon: CalendarClock, permission: 'scheduler:read', keywords: 'cron' },
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
      { to: '/app/admin/users', label: 'User Management', icon: Users, permission: 'users:read', keywords: 'invite role' },
      { to: '/app/admin/audit-log', label: 'Audit Log', icon: ScrollText, permission: 'audit:read' },
    ],
  },
]
