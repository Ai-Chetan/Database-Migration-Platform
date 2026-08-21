import React, { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Copy, Check, KeyRound } from 'lucide-react'
import { Modal, Button, FormField, Input, Select } from '@/components/common'
import { usersApi, CreateUserResult } from '@/api/users'
import { Role } from '@/types'
import { ROLE_LABELS } from '@/utils/permissions'
import { useAuthStore } from '@/store/auth'

const ROLES: Role[] = ['tenant_admin', 'migration_admin', 'migration_operator', 'read_only', 'auditor', 'api_client']

// CHANGE: this replaces InviteUserModal.tsx. Instead of sending an invite
// email the person has to click and accept, the admin fills in the new
// user's details directly and the account is created immediately - the
// admin can either set the password themselves or leave it blank to have
// the backend generate a temporary one, shown once here so it can be
// relayed to the new user.
export function AddUserModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const tenantId = useAuthStore((s) => s.user?.tenant_id)

  const emptyForm = {
    full_name: '',
    email: '',
    phone: '',
    role: 'migration_operator' as Role,
    password: '',
    setPasswordManually: false,
  }
  const [form, setForm] = useState(emptyForm)
  const [result, setResult] = useState<CreateUserResult | null>(null)
  const [copied, setCopied] = useState(false)

  const createMutation = useMutation({
    mutationFn: () => {
      if (!tenantId) throw new Error('Missing tenant context - please sign in again.')
      return usersApi.create(tenantId, {
        email: form.email,
        full_name: form.full_name,
        role: form.role,
        phone: form.phone || undefined,
        password: form.setPasswordManually ? form.password : undefined,
      })
    },
    onSuccess: (created) => {
      toast.success(`${created.name} was added`)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setResult(created)
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || 'Failed to create user'),
  })

  function handleClose() {
    setForm(emptyForm)
    setResult(null)
    setCopied(false)
    onClose()
  }

  function copyPassword() {
    if (!result?.temporary_password) return
    navigator.clipboard.writeText(result.temporary_password)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const canSubmit = form.full_name && form.email && (!form.setPasswordManually || form.password.length >= 8)

  // ── Success state: show the temp password once (if one was generated) ────
  if (result) {
    return (
      <Modal
        isOpen={isOpen}
        onClose={handleClose}
        title="User created"
        footer={<Button onClick={handleClose}>Done</Button>}
      >
        <p className="text-body text-text-secondary">
          <strong className="text-text-primary">{result.name}</strong> ({result.email}) has been added
          as <strong className="text-text-primary">{ROLE_LABELS[result.role]}</strong>.
        </p>

        {result.email_sent && (
          <p className="mt-3 text-small text-text-secondary">
            A welcome email with sign-in instructions was sent to {result.email}.
          </p>
        )}

        {result.temporary_password && (
          <div className="mt-4 rounded border border-border bg-input p-4">
            <div className="mb-2 flex items-center gap-2 text-small font-medium text-text-primary">
              <KeyRound className="h-4 w-4" />
              Temporary password
            </div>
            <p className="mb-3 text-tiny text-text-tertiary">
              {result.email_sent
                ? 'Also included in the welcome email. '
                : "Email wasn't sent - "}
              This is shown once. The user will be asked to set their own password on first login.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded border border-border bg-white px-3 py-2 font-mono text-small text-text-primary">
                {result.temporary_password}
              </code>
              <Button variant="secondary" size="sm" onClick={copyPassword} leftIcon={copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}>
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
          </div>
        )}
      </Modal>
    )
  }

  // ── Form state ──────────────────────────────────────────────────────────
  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title="Add user"
      footer={
        <>
          <Button variant="secondary" onClick={handleClose}>Cancel</Button>
          <Button disabled={!canSubmit} isLoading={createMutation.isPending} onClick={() => createMutation.mutate()}>
            Create user
          </Button>
        </>
      }
    >
      <FormField label="Full name" required>
        <Input placeholder="Jane Doe" value={form.full_name} onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))} autoFocus />
      </FormField>

      <FormField label="Email address" required>
        <Input type="email" placeholder="jane@company.com" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
      </FormField>

      <FormField label="Phone number" hint="Optional">
        <Input type="tel" placeholder="+1 555 000 0000" value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
      </FormField>

      <FormField label="Role" required>
        <Select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as Role }))}>
          {ROLES.map((r) => (
            <option key={r} value={r}>{ROLE_LABELS[r]}</option>
          ))}
        </Select>
      </FormField>

      <FormField label="Password">
        <label className="mb-2 flex items-center gap-2 text-small text-text-secondary">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-border"
            checked={form.setPasswordManually}
            onChange={(e) => setForm((f) => ({ ...f, setPasswordManually: e.target.checked }))}
          />
          Set the password myself
        </label>
        {form.setPasswordManually ? (
          <Input
            type="password"
            placeholder="At least 8 characters"
            value={form.password}
            onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
            autoComplete="new-password"
          />
        ) : (
          <p className="text-tiny text-text-tertiary">
            A secure temporary password will be generated and shown once (and emailed, if delivery is configured).
          </p>
        )}
      </FormField>
    </Modal>
  )
}
