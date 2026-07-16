import React, { Suspense } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'
import { PublicLayout } from '@/components/layout/PublicLayout'
import { AppLayout } from '@/components/layout/AppLayout'
import { ProtectedRoute, RequirePermission } from './ProtectedRoute'
import { FullPageSpinner } from '@/components/common'

import Landing from '@/pages/public/Landing'
import Login from '@/pages/public/Login'
import ForgotPassword from '@/pages/public/ForgotPassword'
import Dashboard from '@/pages/Dashboard'
import Connections from '@/pages/Connections'
import JobsList from '@/pages/JobsList'
import NewMigration from '@/pages/NewMigration'
import JobDetail from '@/pages/jobs/JobDetail'
import OperationsConsole from '@/pages/operations/OperationsConsole'
import SchemaExplorer from '@/pages/SchemaExplorer'
import Simulation from '@/pages/Simulation'
import Masking from '@/pages/Masking'
import Scheduler from '@/pages/Scheduler'
import Reports from '@/pages/Reports'
import KnowledgeBase from '@/pages/KnowledgeBase'
import Settings from '@/pages/Settings'
import UserManagement from '@/pages/admin/UserManagement'
import AuditLog from '@/pages/admin/AuditLog'
import AccessDenied from '@/pages/AccessDenied'
import NotFound from '@/pages/NotFound'

function withSuspense(node: React.ReactNode) {
  return <Suspense fallback={<FullPageSpinner />}>{node}</Suspense>
}

export const router = createBrowserRouter([
  {
    element: <PublicLayout />,
    children: [
      { path: '/', element: <Landing /> },
      { path: '/login', element: <Login /> },
      { path: '/forgot-password', element: <ForgotPassword /> },
    ],
  },
  {
    path: '/app',
    element: (
      <ProtectedRoute>
        <AppLayout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Navigate to="dashboard" replace /> },
      { path: 'dashboard', element: withSuspense(<Dashboard />) },

      {
        path: 'connections',
        element: (
          <RequirePermission permission="connections:read">{withSuspense(<Connections />)}</RequirePermission>
        ),
      },
      {
        path: 'jobs',
        element: <RequirePermission permission="jobs:read">{withSuspense(<JobsList />)}</RequirePermission>,
      },
      {
        path: 'jobs/new',
        element: <RequirePermission permission="jobs:start">{withSuspense(<NewMigration />)}</RequirePermission>,
      },
      {
        path: 'jobs/:jobId',
        element: <RequirePermission permission="jobs:read">{withSuspense(<JobDetail />)}</RequirePermission>,
      },
      {
        path: 'schema',
        element: <RequirePermission permission="schema:read">{withSuspense(<SchemaExplorer />)}</RequirePermission>,
      },
      {
        path: 'operations',
        element: (
          <RequirePermission permission="operations:read">
            {withSuspense(<OperationsConsole />)}
          </RequirePermission>
        ),
      },
      { path: 'simulation', element: withSuspense(<Simulation />) },
      {
        path: 'masking',
        element: <RequirePermission permission="masking:read">{withSuspense(<Masking />)}</RequirePermission>,
      },
      {
        path: 'scheduler',
        element: <RequirePermission permission="scheduler:read">{withSuspense(<Scheduler />)}</RequirePermission>,
      },
      {
        path: 'reports',
        element: <RequirePermission permission="reports:read">{withSuspense(<Reports />)}</RequirePermission>,
      },
      {
        path: 'knowledge-base',
        element: <RequirePermission permission="knowledge:read">{withSuspense(<KnowledgeBase />)}</RequirePermission>,
      },
      { path: 'settings', element: withSuspense(<Settings />) },
      {
        path: 'admin/users',
        element: <RequirePermission permission="users:read">{withSuspense(<UserManagement />)}</RequirePermission>,
      },
      {
        path: 'admin/audit-log',
        element: <RequirePermission permission="audit:read">{withSuspense(<AuditLog />)}</RequirePermission>,
      },
      { path: 'access-denied', element: <AccessDenied /> },
    ],
  },
  { path: '*', element: <NotFound /> },
])
