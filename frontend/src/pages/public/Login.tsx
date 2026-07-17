import React from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { ArrowLeft, Database } from 'lucide-react'
import { Button, Input, FormField } from '@/components/common'
import { useAuth } from '@/hooks/useAuth'
import { AuthVisualPanel } from '@/components/features/auth/AuthVisualPanel'

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
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[1fr_1.1fr]">
      {/* Form side */}
      <div className="flex flex-col justify-between px-6 py-8 sm:px-12 lg:px-16">
        <Link to="/" className="flex w-fit items-center gap-2 text-small text-text-tertiary hover:text-text-primary">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to home
        </Link>

        <div className="mx-auto w-full max-w-sm">
          <div className="mb-2 flex h-9 w-9 items-center justify-center rounded bg-action">
            <Database className="h-[18px] w-[18px] text-white" />
          </div>
          <h1 className="mt-5 text-h1 text-text-primary">Welcome back</h1>
          <p className="mt-2 text-body text-text-secondary">
            Log in to pick up where your migrations left off.
          </p>

          <form onSubmit={handleSubmit(onSubmit)} className="mt-8" noValidate>
            <FormField label="Email" error={errors.email?.message} required>
              <Input
                type="email"
                placeholder="you@company.com"
                hasError={!!errors.email}
                autoComplete="email"
                autoFocus
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

            <div className="mb-6 flex justify-end">
              <Link to="/forgot-password" className="text-small text-action hover:underline">
                Forgot password?
              </Link>
            </div>

            <Button type="submit" className="w-full" size="lg" isLoading={isLoggingIn}>
              Log in
            </Button>
          </form>
        </div>

        <p className="text-center text-tiny text-text-tertiary lg:text-left">
          Protected by role-based access control and full audit logging.
        </p>
      </div>

      {/* Visual side */}
      <AuthVisualPanel className="hidden lg:block" />
    </div>
  )
}
