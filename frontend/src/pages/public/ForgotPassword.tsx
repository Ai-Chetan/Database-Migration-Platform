import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Database, MailCheck } from 'lucide-react'
import { Button, Input, FormField } from '@/components/common'
import apiClient from '@/api/client'

const schema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
})
type FormValues = z.infer<typeof schema>

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
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-sm text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-green-50">
            <MailCheck className="h-6 w-6 text-success" />
          </div>
          <h1 className="text-h3 text-text-primary">Check your inbox</h1>
          <p className="mt-2 text-body text-text-secondary">
            If an account exists for that email, we've sent a link to reset your password.
          </p>
          <Link to="/login" className="mt-6 inline-block text-small text-action hover:underline">
            ← Back to login
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center">
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded bg-action">
            <Database className="h-5 w-5 text-white" />
          </div>
          <h1 className="text-h3 text-text-primary">Reset your password</h1>
          <p className="mt-1 text-center text-body text-text-secondary">
            Enter your email and we'll send you a reset link
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <FormField label="Email" error={errors.email?.message} required>
            <Input type="email" placeholder="you@company.com" hasError={!!errors.email} {...register('email')} />
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
      </div>
    </div>
  )
}
