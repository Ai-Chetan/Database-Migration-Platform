import React from 'react'
import { cn } from '@/utils/cn'

interface Tab {
  key: string
  label: string
  count?: number
}

interface TabsProps {
  tabs: Tab[]
  active: string
  onChange: (key: string) => void
}

export function Tabs({ tabs, active, onChange }: TabsProps) {
  return (
    <div className="flex gap-6 border-b border-border">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={cn(
            'relative -mb-px flex items-center gap-2 border-b-2 py-3 text-body font-medium transition-colors',
            active === tab.key
              ? 'border-action text-action'
              : 'border-transparent text-text-secondary hover:text-text-primary'
          )}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span
              className={cn(
                'rounded-pill px-1.5 py-0.5 text-tiny',
                active === tab.key ? 'bg-action/10 text-action' : 'bg-surface text-text-tertiary'
              )}
            >
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
