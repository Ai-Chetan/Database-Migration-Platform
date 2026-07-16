import React from 'react'
import { cn } from '@/utils/cn'

export function FormField({
  label,
  error,
  hint,
  required,
  children,
}: {
  label: string
  error?: string
  hint?: string
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="mb-4">
      <label className="mb-1.5 block text-small font-medium text-text-primary">
        {label}
        {required && <span className="text-error"> *</span>}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-tiny text-text-tertiary">{hint}</p>}
      {error && <p className="mt-1 text-tiny text-error">{error}</p>}
    </div>
  )
}

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement> & { hasError?: boolean }>(
  ({ className, hasError, ...rest }, ref) => (
    <input
      ref={ref}
      className={cn(
        'h-10 w-full rounded border bg-input px-3 text-body text-text-primary placeholder:text-text-tertiary',
        'focus:bg-white focus:outline-none focus:ring-2 focus:ring-action/30 focus:border-action',
        hasError ? 'border-error' : 'border-border',
        className
      )}
      {...rest}
    />
  )
)
Input.displayName = 'Input'

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement> & { hasError?: boolean }>(
  ({ className, hasError, children, ...rest }, ref) => (
    <select
      ref={ref}
      className={cn(
        'h-10 w-full rounded border bg-input px-3 text-body text-text-primary',
        'focus:bg-white focus:outline-none focus:ring-2 focus:ring-action/30 focus:border-action',
        hasError ? 'border-error' : 'border-border',
        className
      )}
      {...rest}
    >
      {children}
    </select>
  )
)
Select.displayName = 'Select'

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement> & { hasError?: boolean }>(
  ({ className, hasError, ...rest }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        'w-full rounded border bg-input px-3 py-2 text-body text-text-primary placeholder:text-text-tertiary',
        'focus:bg-white focus:outline-none focus:ring-2 focus:ring-action/30 focus:border-action',
        hasError ? 'border-error' : 'border-border',
        className
      )}
      {...rest}
    />
  )
)
Textarea.displayName = 'Textarea'
