import React from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/utils/cn'

export interface WizardStepDef {
  key: string
  label: string
}

export function WizardStepper({ steps, activeIndex }: { steps: WizardStepDef[]; activeIndex: number }) {
  return (
    <ol className="mb-8 flex items-center">
      {steps.map((step, i) => {
        const state = i < activeIndex ? 'done' : i === activeIndex ? 'active' : 'upcoming'
        return (
          <li key={step.key} className="flex flex-1 items-center last:flex-none">
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={cn(
                  'flex h-8 w-8 items-center justify-center rounded-full text-small font-semibold transition-colors',
                  state === 'done' && 'bg-action text-white',
                  state === 'active' && 'bg-action/10 text-action ring-2 ring-action',
                  state === 'upcoming' && 'bg-surface text-text-tertiary'
                )}
              >
                {state === 'done' ? <Check className="h-4 w-4" /> : i + 1}
              </div>
              <span
                className={cn(
                  'whitespace-nowrap text-tiny font-medium',
                  state === 'upcoming' ? 'text-text-tertiary' : 'text-text-primary'
                )}
              >
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className={cn('mx-3 mb-5 h-0.5 flex-1', i < activeIndex ? 'bg-action' : 'bg-border')} />
            )}
          </li>
        )
      })}
    </ol>
  )
}
