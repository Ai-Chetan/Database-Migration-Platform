import React from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Database } from 'lucide-react'
import { Button, Input, FormField } from '@/components/common'
import { useAuth } from '@/hooks/useAuth'

const schema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
})

type FormValues = z.infer<typeof schema>

export default function Login() {
  const { login, isLoggingIn } = useAuth()
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = (values: FormValues) => {
    login({ username: values.email, password: values.password })
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center">
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded bg-action">
            <Database className="h-5 w-5 text-white" />
          </div>
          <h1 className="text-h3 text-text-primary">Welcome back</h1>
          <p className="mt-1 text-body text-text-secondary">Log in to your Migration Platform account</p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <FormField label="Email" error={errors.email?.message} required>
            <Input
              type="email"
              placeholder="you@company.com"
              hasError={!!errors.email}
              autoComplete="email"
              {...register('email')}
            />
          </FormField>

          <FormField label="Password" error={errors.password?.message} required>
            <Input
              type="password"
              placeholder="••••••••"
              hasError={!!errors.password}
              autoComplete="current-password"
              {...register('password')}
            />
          </FormField>

          <div className="mb-5 flex justify-end">
            <Link to="/forgot-password" className="text-small text-action hover:underline">
              Forgot password?
            </Link>
          </div>

          <Button type="submit" className="w-full" size="lg" isLoading={isLoggingIn}>
            Log in
          </Button>
        </form>

        <p className="mt-6 text-center text-small text-text-tertiary">
          <Link to="/" className="hover:text-text-primary hover:underline">
            ← Back to home
          </Link>
        </p>
      </div>
    </div>
  )
}
