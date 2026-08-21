import React, { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Modal, Button, FormField, Input, Select } from '@/components/common'
import { usersApi } from '@/api/users'
import { Role } from '@/types'
import { ROLE_LABELS } from '@/utils/permissions'
import { useAuthStore } from '@/store/auth'

const ROLES: Role[] = ['tenant_admin', 'migration_admin', 'migration_operator', 'read_only', 'auditor', 'api_client']

export function InviteUserModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('migration_operator')
  const tenantId = useAuthStore((s) => s.user?.tenant_id)

  const inviteMutation = useMutation({
    mutationFn: () => {
      if (!tenantId) {
        throw new Error('Missing tenant context - please sign in again.')
      }

      return usersApi.create(tenantId, {
        email,
        full_name: email.split('@')[0],
        role,
      })
    },
    onSuccess: () => {
      toast.success(`User created: ${email}`)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setEmail('')
      onClose()
    },
    onError: (err: any) =>
      toast.error(err?.response?.data?.detail || 'Failed to create user'),
  })

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Invite team member"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button disabled={!email} isLoading={inviteMutation.isPending} onClick={() => inviteMutation.mutate()}>
            Send invite
          </Button>
        </>
      }
    >
      <FormField label="Email address" required>
        <Input type="email" placeholder="teammate@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
      </FormField>
      <FormField label="Role" required>
        <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>{ROLE_LABELS[r]}</option>
          ))}
        </Select>
      </FormField>
    </Modal>
  )
}
