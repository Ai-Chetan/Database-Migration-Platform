import React, { Suspense, lazy } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'
import { PublicLayout } from '@/components/layout/PublicLayout'
import { AppLayout } from '@/components/layout/AppLayout'
import { ProtectedRoute, RequirePermission } from './ProtectedRoute'
import { FullPageSpinner } from '@/components/common'

// Public pages are lazy too - keeps the very first paint (before auth is even known)
// as small as possible. AccessDenied/NotFound stay eager: they're tiny and are
// sometimes rendered outside a Suspense boundary (top-level catch-all route).
import AccessDenied from '@/pages/AccessDenied'
import NotFound from '@/pages/NotFound'

const Landing = lazy(() => import('@/pages/public/Landing'))
const Login = lazy(() => import('@/pages/public/Login'))
const ForgotPassword = lazy(() => import('@/pages/public/ForgotPassword'))

const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Connections = lazy(() => import('@/pages/Connections'))
const JobsList = lazy(() => import('@/pages/JobsList'))
const NewMigration = lazy(() => import('@/pages/NewMigration'))
const JobDetail = lazy(() => import('@/pages/jobs/JobDetail'))
const OperationsConsole = lazy(() => import('@/pages/operations/OperationsConsole'))
const SchemaExplorer = lazy(() => import('@/pages/SchemaExplorer'))
const Simulation = lazy(() => import('@/pages/Simulation'))
const Masking = lazy(() => import('@/pages/Masking'))
const Scheduler = lazy(() => import('@/pages/Scheduler'))
const Reports = lazy(() => import('@/pages/Reports'))
const KnowledgeBase = lazy(() => import('@/pages/KnowledgeBase'))
const Settings = lazy(() => import('@/pages/Settings'))
const UserManagement = lazy(() => import('@/pages/admin/UserManagement'))
const AuditLog = lazy(() => import('@/pages/admin/AuditLog'))

function withSuspense(node: React.ReactNode) {
  return <Suspense fallback={<FullPageSpinner />}>{node}</Suspense>
}

export const router = createBrowserRouter([
  {
    element: <PublicLayout />,
    children: [
      { path: '/', element: withSuspense(<Landing />) },
      { path: '/login', element: withSuspense(<Login />) },
      { path: '/forgot-password', element: withSuspense(<ForgotPassword />) },
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
