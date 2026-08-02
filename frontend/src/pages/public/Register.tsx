import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { ArrowLeft, Database } from 'lucide-react'
import { Button, Input, FormField } from '@/components/common'
import { useAuth } from '@/hooks/useAuth'
import { AuthVisualPanel } from '@/components/features/auth/AuthVisualPanel'

function slugify(value: string) {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 50)
}

const schema = z
  .object({
    tenant_name: z.string().min(2, 'Workspace name is required'),
    tenant_slug: z
      .string()
      .min(2, 'URL slug is required')
      .regex(/^[a-z0-9-]+$/, 'Lowercase letters, numbers, and hyphens only'),
    full_name: z.string().min(1, 'Your name is required'),
    email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
    password: z.string().min(8, 'Password must be at least 8 characters'),
    confirm_password: z.string(),
  })
  .refine((data) => data.password === data.confirm_password, {
    message: "Passwords don't match",
    path: ['confirm_password'],
  })

type FormValues = z.infer<typeof schema>

export default function Register() {
  const { register: signUp, isRegistering } = useAuth()
  const [slugEdited, setSlugEdited] = useState(false)
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const tenantName = watch('tenant_name')

  const onSubmit = (values: FormValues) => {
    signUp({
      tenant_name: values.tenant_name,
      tenant_slug: values.tenant_slug,
      full_name: values.full_name,
      email: values.email,
      password: values.password,
    })
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
          <h1 className="mt-5 text-h1 text-text-primary">Create your workspace</h1>
          <p className="mt-2 text-body text-text-secondary">
            Start migrating in minutes. No credit card required.
          </p>

          <form onSubmit={handleSubmit(onSubmit)} className="mt-8" noValidate>
            <FormField label="Workspace name" error={errors.tenant_name?.message} required>
              <Input
                placeholder="Acme Inc."
                hasError={!!errors.tenant_name}
                autoFocus
                {...register('tenant_name', {
                  onChange: (e) => {
                    if (!slugEdited) setValue('tenant_slug', slugify(e.target.value))
                  },
                })}
              />
            </FormField>

            <FormField
              label="Workspace URL"
              error={errors.tenant_slug?.message}
              hint="Used to identify your workspace. Lowercase letters, numbers, hyphens."
              required
            >
              <Input
                placeholder="acme-inc"
                hasError={!!errors.tenant_slug}
                {...register('tenant_slug', {
                  onChange: () => setSlugEdited(true),
                })}
              />
            </FormField>

            <FormField label="Your name" error={errors.full_name?.message} required>
              <Input placeholder="Jane Doe" hasError={!!errors.full_name} {...register('full_name')} />
            </FormField>

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
                placeholder="At least 8 characters"
                hasError={!!errors.password}
                autoComplete="new-password"
                {...register('password')}
              />
            </FormField>

            <FormField label="Confirm password" error={errors.confirm_password?.message} required>
              <Input
                type="password"
                placeholder="••••••••"
                hasError={!!errors.confirm_password}
                autoComplete="new-password"
                {...register('confirm_password')}
              />
            </FormField>

            <Button type="submit" className="mt-2 w-full" size="lg" isLoading={isRegistering}>
              Create workspace
            </Button>
          </form>

          <p className="mt-6 text-center text-small text-text-secondary">
            Already have a workspace?{' '}
            <Link to="/login" className="font-medium text-action hover:underline">
              Log in
            </Link>
          </p>
        </div>

        <p className="text-center text-tiny text-text-tertiary lg:text-left">
          By signing up, you agree to our Terms of Service and Privacy Policy.
        </p>
      </div>

      {/* Visual side */}
      <AuthVisualPanel className="hidden lg:block" />
    </div>
  )
}
