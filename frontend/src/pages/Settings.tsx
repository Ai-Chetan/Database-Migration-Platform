import React, { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { PageHeader, Card, Tabs, FormField, Input, Button } from '@/components/common'
import { useAuthStore } from '@/store/auth'
import { authApi } from '@/api/auth'
import { ROLE_LABELS } from '@/utils/permissions'

const passwordSchema = z
  .object({
    current_password: z.string().min(1, 'Current password is required'),
    new_password: z.string().min(8, 'New password must be at least 8 characters'),
    confirm_password: z.string().min(1, 'Please confirm your new password'),
  })
  .refine((data) => data.new_password === data.confirm_password, {
    message: "Passwords don't match",
    path: ['confirm_password'],
  })

type PasswordForm = z.infer<typeof passwordSchema>

function ProfileTab() {
  const user = useAuthStore((s) => s.user)
  return (
    <Card className="max-w-lg">
      <FormField label="Full name">
        <Input value={user?.name ?? ''} disabled />
      </FormField>
      <FormField label="Email">
        <Input value={user?.email ?? ''} disabled />
      </FormField>
      <FormField label="Role">
        <Input value={user ? ROLE_LABELS[user.role] : ''} disabled />
      </FormField>
      <p className="text-tiny text-text-tertiary">
        Contact a Tenant Admin to update your name or role.
      </p>
    </Card>
  )
}

function SecurityTab() {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<PasswordForm>({ resolver: zodResolver(passwordSchema) })

  const mutation = useMutation({
    mutationFn: (values: PasswordForm) => authApi.changePassword(values.current_password, values.new_password),
    onSuccess: () => {
      toast.success('Password updated')
      reset()
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || 'Failed to update password'),
  })

  return (
    <Card className="max-w-lg">
      <form onSubmit={handleSubmit((v) => mutation.mutate(v))} noValidate>
        <FormField label="Current password" error={errors.current_password?.message} required>
          <Input type="password" hasError={!!errors.current_password} {...register('current_password')} />
        </FormField>
        <FormField label="New password" error={errors.new_password?.message} required>
          <Input type="password" hasError={!!errors.new_password} {...register('new_password')} />
        </FormField>
        <FormField label="Confirm new password" error={errors.confirm_password?.message} required>
          <Input type="password" hasError={!!errors.confirm_password} {...register('confirm_password')} />
        </FormField>
        <Button type="submit" isLoading={mutation.isPending}>
          Update password
        </Button>
      </form>
    </Card>
  )
}

export default function Settings() {
  const [tab, setTab] = useState('profile')

  return (
    <div>
      <PageHeader title="Settings" description="Manage your account and security preferences." />
      <div className="mb-6">
        <Tabs
          tabs={[
            { key: 'profile', label: 'Profile' },
            { key: 'security', label: 'Security' },
          ]}
          active={tab}
          onChange={setTab}
        />
      </div>
      {tab === 'profile' ? <ProfileTab /> : <SecurityTab />}
    </div>
  )
}
