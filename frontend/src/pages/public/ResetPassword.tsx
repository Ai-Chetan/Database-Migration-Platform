import React, { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { ArrowLeft, Database, CheckCircle2, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import { Button, Input, FormField } from '@/components/common'
import { authApi } from '@/api/auth'
import { AuthVisualPanel } from '@/components/features/auth/AuthVisualPanel'

const schema = z
  .object({
    password: z.string().min(8, 'Password must be at least 8 characters'),
    confirm_password: z.string(),
  })
  .refine((data) => data.password === data.confirm_password, {
    message: "Passwords don't match",
    path: ['confirm_password'],
  })

type FormValues = z.infer<typeof schema>

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[1fr_1.1fr]">
      <div className="flex flex-col justify-between px-6 py-8 sm:px-12 lg:px-16">
        <Link to="/" className="flex w-fit items-center gap-2 text-small text-text-tertiary hover:text-text-primary">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to home
        </Link>
        <div className="mx-auto w-full max-w-sm">{children}</div>
        <div />
      </div>
      <AuthVisualPanel className="hidden lg:block" />
    </div>
  )
}

export default function ResetPassword() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const navigate = useNavigate()
  const [done, setDone] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = async (values: FormValues) => {
    try {
      await authApi.resetPassword(token, values.password)
      setDone(true)
    } catch (err: any) {
      toast.error(
        err?.response?.data?.detail ||
          'This reset link is invalid or has expired. Request a new one.'
      )
    }
  }

  // No token in the URL at all - the link was malformed or opened without
  // its query string. Show a clear dead-end instead of a blank/broken form.
  if (!token) {
    return (
      <Shell>
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-red-50">
          <AlertTriangle className="h-5 w-5 text-error" />
        </div>
        <h1 className="text-h1 text-text-primary">Invalid reset link</h1>
        <p className="mt-2 text-body text-text-secondary">
          This link is missing its reset token. Request a new password reset link.
        </p>
        <Link to="/forgot-password" className="mt-6 inline-block text-small text-action hover:underline">
          ← Request a new link
        </Link>
      </Shell>
    )
  }

  if (done) {
    return (
      <Shell>
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-green-50">
          <CheckCircle2 className="h-5 w-5 text-success" />
        </div>
        <h1 className="text-h1 text-text-primary">Password reset</h1>
        <p className="mt-2 text-body text-text-secondary">
          Your password has been changed. You can now sign in with your new password.
        </p>
        <Button className="mt-6 w-full" size="lg" onClick={() => navigate('/login')}>
          Go to sign in
        </Button>
      </Shell>
    )
  }

  return (
    <Shell>
      <div className="mb-2 flex h-9 w-9 items-center justify-center rounded bg-action">
        <Database className="h-[18px] w-[18px] text-white" />
      </div>
      <h1 className="mt-5 text-h1 text-text-primary">Set a new password</h1>
      <p className="mt-2 text-body text-text-secondary">Choose a new password for your account.</p>

      <form onSubmit={handleSubmit(onSubmit)} className="mt-8" noValidate>
        <FormField label="New password" error={errors.password?.message} required>
          <Input
            type="password"
            placeholder="At least 8 characters"
            hasError={!!errors.password}
            autoComplete="new-password"
            autoFocus
            {...register('password')}
          />
        </FormField>

        <FormField label="Confirm new password" error={errors.confirm_password?.message} required>
          <Input
            type="password"
            placeholder="••••••••"
            hasError={!!errors.confirm_password}
            autoComplete="new-password"
            {...register('confirm_password')}
          />
        </FormField>

        <Button type="submit" className="w-full" size="lg" isLoading={isSubmitting}>
          Reset password
        </Button>
      </form>

      <p className="mt-6 text-center text-small text-text-tertiary">
        <Link to="/login" className="hover:text-text-primary hover:underline">
          ← Back to login
        </Link>
      </p>
    </Shell>
  )
}
