import React from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'
import { useCurrentUser } from '@/hooks/useAuth'
import { usePermission } from '@/hooks/usePermission'
import { FullPageSpinner } from '@/components/common'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const location = useLocation()
  const { isLoading } = useCurrentUser()

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (isLoading) {
    return <FullPageSpinner />
  }

  return <>{children}</>
}

export function RequirePermission({
  permission,
  children,
}: {
  permission: string
  children: React.ReactNode
}) {
  const allowed = usePermission(permission)
  if (!allowed) return <Navigate to="/app/access-denied" replace />
  return <>{children}</>
}
