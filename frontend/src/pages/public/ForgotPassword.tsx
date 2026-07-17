import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { ArrowLeft, Database, MailCheck } from 'lucide-react'
import { Button, Input, FormField } from '@/components/common'
import apiClient from '@/api/client'
import { AuthVisualPanel } from '@/components/features/auth/AuthVisualPanel'

const schema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
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

export default function ForgotPassword() {
  const [sent, setSent] = useState(false)
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = async (values: FormValues) => {
    try {
      await apiClient.post('/auth/forgot-password', { email: values.email })
    } finally {
      // Always show the confirmation screen — never reveal whether the email exists.
      setSent(true)
    }
  }

  if (sent) {
    return (
      <Shell>
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-green-50">
          <MailCheck className="h-5 w-5 text-success" />
        </div>
        <h1 className="text-h1 text-text-primary">Check your inbox</h1>
        <p className="mt-2 text-body text-text-secondary">
          If an account exists for that email, we've sent a link to reset your password.
        </p>
        <Link to="/login" className="mt-6 inline-block text-small text-action hover:underline">
          ← Back to login
        </Link>
      </Shell>
    )
  }

  return (
    <Shell>
      <div className="mb-2 flex h-9 w-9 items-center justify-center rounded bg-action">
        <Database className="h-[18px] w-[18px] text-white" />
      </div>
      <h1 className="mt-5 text-h1 text-text-primary">Reset your password</h1>
      <p className="mt-2 text-body text-text-secondary">Enter your email and we'll send you a reset link.</p>

      <form onSubmit={handleSubmit(onSubmit)} className="mt-8" noValidate>
        <FormField label="Email" error={errors.email?.message} required>
          <Input type="email" placeholder="you@company.com" hasError={!!errors.email} autoFocus {...register('email')} />
        </FormField>

        <Button type="submit" className="w-full" size="lg" isLoading={isSubmitting}>
          Send reset link
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
