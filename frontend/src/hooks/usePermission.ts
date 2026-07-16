import { useAuthStore } from '@/store/auth'
import { hasPermission } from '@/utils/permissions'

export function usePermission(permission: string): boolean {
  const role = useAuthStore((s) => s.user?.role)
  return hasPermission(role, permission)
}
