import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import { UserPlus, Users, Ban, RotateCcw } from 'lucide-react'
import { usersApi } from '@/api/users'
import { User, Role } from '@/types'
import { PageHeader, Button, DataTable, Select, Badge, EmptyState } from '@/components/common'
import { useDisclosure } from '@/hooks/useDisclosure'
import { ROLE_LABELS } from '@/utils/permissions'
import { formatRelativeTime } from '@/utils/format'
import { InviteUserModal } from '@/components/features/admin/InviteUserModal'

const ROLES: Role[] = ['platform_admin', 'tenant_admin', 'migration_admin', 'migration_operator', 'read_only', 'auditor', 'api_client']

export default function UserManagement() {
  const queryClient = useQueryClient()
  const inviteModal = useDisclosure()

  const { data: users = [], isLoading } = useQuery({ queryKey: ['users', 'list'], queryFn: usersApi.list })

  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) => usersApi.updateRole(id, role),
    onSuccess: () => {
      toast.success('Role updated')
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const statusMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'deactivate' | 'reactivate' }) =>
      action === 'deactivate' ? usersApi.deactivate(id) : usersApi.reactivate(id),
    onSuccess: () => {
      toast.success('User updated')
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
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
        <div className="flex justify-end">
          {row.original.is_active === false ? (
            <Button variant="ghost" size="sm" leftIcon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => statusMutation.mutate({ id: row.original.id, action: 'reactivate' })}>
              Reactivate
            </Button>
          ) : (
            <Button variant="ghost" size="sm" leftIcon={<Ban className="h-3.5 w-3.5 text-error" />} onClick={() => statusMutation.mutate({ id: row.original.id, action: 'deactivate' })}>
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
        actions={<Button leftIcon={<UserPlus className="h-4 w-4" />} onClick={inviteModal.open}>Invite user</Button>}
      />

      {!isLoading && users.length === 0 ? (
        <EmptyState icon={Users} title="No users yet" description="Invite your team to start collaborating." actionLabel="Invite user" onAction={inviteModal.open} />
      ) : (
        <DataTable columns={columns} data={users} isLoading={isLoading} />
      )}

      <InviteUserModal isOpen={inviteModal.isOpen} onClose={inviteModal.close} />
    </div>
  )
}
