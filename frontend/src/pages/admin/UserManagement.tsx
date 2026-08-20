import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import { UserPlus, Users, Ban, RotateCcw, KeyRound } from 'lucide-react'
import { usersApi } from '@/api/users'
import { User, Role } from '@/types'
import { PageHeader, Button, DataTable, Select, Badge, EmptyState, Modal } from '@/components/common'
import { useDisclosure } from '@/hooks/useDisclosure'
import { ROLE_LABELS } from '@/utils/permissions'
import { formatRelativeTime } from '@/utils/format'
import { AddUserModal } from '@/components/features/admin/AddUserModal'
import { useAuthStore } from '@/store/auth'

const ROLES: Role[] = ['platform_admin', 'tenant_admin', 'migration_admin', 'migration_operator', 'read_only', 'auditor', 'api_client']

export default function UserManagement() {
  const queryClient = useQueryClient()
  const addUserModal = useDisclosure()
  const tenantId = useAuthStore((s) => s.user?.tenant_id)
  const currentUserId = useAuthStore((s) => s.user?.id)

  // Password-reset result is shown once in its own small modal, same pattern
  // as AddUserModal's temporary-password display.
  const [resetResult, setResetResult] = useState<{ email: string; temporary_password?: string; email_sent: boolean } | null>(null)

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['users', 'list', tenantId],
    queryFn: () => usersApi.list(tenantId!),
    enabled: !!tenantId,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['users'] })

  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) => usersApi.updateRole(tenantId!, id, role),
    onSuccess: () => {
      toast.success('Role updated')
      invalidate()
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || 'Failed to update role'),
  })

  const statusMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'deactivate' | 'reactivate' }) =>
      action === 'deactivate' ? usersApi.deactivate(tenantId!, id) : usersApi.reactivate(tenantId!, id),
    onSuccess: (_, { action }) => {
      toast.success(action === 'deactivate' ? 'User deactivated' : 'User reactivated')
      invalidate()
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || 'Failed to update user'),
  })

  const resetPasswordMutation = useMutation({
    mutationFn: (user: User) => usersApi.resetPassword(tenantId!, user.id).then((r) => ({ ...r, email: user.email })),
    onSuccess: (r) => {
      setResetResult({ email: r.email, temporary_password: r.temporary_password, email_sent: r.email_sent })
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || 'Failed to reset password'),
  })

  const columns: ColumnDef<User>[] = [
    {
      header: 'User',
      accessorKey: 'name',
      cell: ({ row }) => (
        <div>
          <p className="font-medium text-text-primary">{row.original.name}</p>
          <p className="text-tiny text-text-tertiary">{row.original.email}</p>
        </div>
      ),
    },
    {
      header: 'Role',
      accessorKey: 'role',
      cell: ({ row }) => (
        <Select
          className="h-8 w-48 text-small"
          value={row.original.role}
          disabled={row.original.id === currentUserId}
          onChange={(e) => roleMutation.mutate({ id: row.original.id, role: e.target.value as Role })}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>{ROLE_LABELS[r]}</option>
          ))}
        </Select>
      ),
    },
    { header: 'Status', accessorKey: 'is_active', cell: ({ getValue }) => <Badge tone={getValue() === false ? 'error' : 'success'}>{getValue() === false ? 'Deactivated' : 'Active'}</Badge> },
    { header: 'Last login', accessorKey: 'last_login', cell: ({ getValue }) => <span className="text-small text-text-secondary">{formatRelativeTime(getValue<string | null>())}</span> },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<KeyRound className="h-3.5 w-3.5" />}
            onClick={() => resetPasswordMutation.mutate(row.original)}
            title="Reset password"
          >
            Reset password
          </Button>
          {row.original.is_active === false ? (
            <Button variant="ghost" size="sm" leftIcon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => statusMutation.mutate({ id: row.original.id, action: 'reactivate' })}>
              Reactivate
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              disabled={row.original.id === currentUserId}
              leftIcon={<Ban className="h-3.5 w-3.5 text-error" />}
              onClick={() => statusMutation.mutate({ id: row.original.id, action: 'deactivate' })}
            >
              Deactivate
            </Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="User Management"
        description="Manage team members and their access levels."
        actions={<Button leftIcon={<UserPlus className="h-4 w-4" />} onClick={addUserModal.open}>Add user</Button>}
      />

      {!isLoading && users.length === 0 ? (
        <EmptyState icon={Users} title="No users yet" description="Add your team to start collaborating." actionLabel="Add user" onAction={addUserModal.open} />
      ) : (
        <DataTable columns={columns} data={users} isLoading={isLoading} />
      )}

      <AddUserModal isOpen={addUserModal.isOpen} onClose={addUserModal.close} />

      <Modal
        isOpen={!!resetResult}
        onClose={() => setResetResult(null)}
        title="Password reset"
        footer={<Button onClick={() => setResetResult(null)}>Done</Button>}
      >
        {resetResult && (
          <>
            <p className="text-body text-text-secondary">
              The password for <strong className="text-text-primary">{resetResult.email}</strong> has been reset.
              {resetResult.email_sent && ' They have been notified by email.'}
            </p>
            {resetResult.temporary_password && (
              <div className="mt-4 rounded border border-border bg-input p-4">
                <p className="mb-2 text-small font-medium text-text-primary">Temporary password (shown once)</p>
                <code className="block rounded border border-border bg-white px-3 py-2 font-mono text-small text-text-primary">
                  {resetResult.temporary_password}
                </code>
              </div>
            )}
          </>
        )}
      </Modal>
    </div>
  )
}
