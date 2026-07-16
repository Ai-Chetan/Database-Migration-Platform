import React from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/utils/cn'

export function Spinner({ size = 'md', className }: { size?: 'sm' | 'md' | 'lg'; className?: string }) {
  const dims = { sm: 'h-4 w-4', md: 'h-6 w-6', lg: 'h-8 w-8' }[size]
  return <Loader2 className={cn('animate-spin text-action', dims, className)} />
}

export function FullPageSpinner() {
  return (
    <div className="flex h-full min-h-[400px] w-full items-center justify-center">
      <Spinner size="lg" />
    </div>
  )
}
