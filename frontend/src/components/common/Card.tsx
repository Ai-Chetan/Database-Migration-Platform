import React from 'react'
import { cn } from '@/utils/cn'

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  padding?: 'none' | 'sm' | 'md'
  hoverable?: boolean
}

export function Card({ padding = 'md', hoverable, className, children, ...rest }: CardProps) {
  const paddingClasses = { none: '', sm: 'p-4', md: 'p-6' }[padding]
  return (
    <div
      className={cn(
        'bg-white border border-border rounded shadow-sm',
        hoverable && 'hover:border-border-strong transition-colors',
        paddingClasses,
        className
      )}
      {...rest}
    >
      {children}
    </div>
  )
}

export function CardHeader({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('flex items-center justify-between mb-4', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardTitle({ className, children, ...rest }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3 className={cn('text-h4 text-text-primary', className)} {...rest}>
      {children}
    </h3>
  )
}
