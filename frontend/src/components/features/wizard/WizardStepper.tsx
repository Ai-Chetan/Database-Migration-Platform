import React from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/utils/cn'

export interface WizardStepDef {
  key: string
  label: string
}

/**
 * Step indicator styled to match the rest of the app's visual language
 * (rounded-DEFAULT squares + mono digits, not generic circle-and-line) rather
 * than the default Bootstrap-style numbered-circle stepper every wizard uses.
 */
export function WizardStepper({ steps, activeIndex }: { steps: WizardStepDef[]; activeIndex: number }) {
  return (
    <ol className="mb-8 flex items-center">
      {steps.map((step, i) => {
        const state = i < activeIndex ? 'done' : i === activeIndex ? 'active' : 'upcoming'
        return (
          <li key={step.key} className="flex flex-1 items-center last:flex-none">
            <div className="flex flex-col items-center gap-2">
              <div
                className={cn(
                  'mono flex h-8 w-8 items-center justify-center rounded text-small font-semibold transition-all duration-300',
                  state === 'done' && 'bg-action text-white',
                  state === 'active' && 'bg-sidebar-bg text-white shadow-sm ring-2 ring-action ring-offset-2',
                  state === 'upcoming' && 'bg-surface text-text-tertiary'
                )}
              >
                {state === 'done' ? <Check className="h-3.5 w-3.5" /> : String(i + 1).padStart(2, '0')}
              </div>
              <span
                className={cn(
                  'whitespace-nowrap text-tiny font-medium transition-colors',
                  state === 'upcoming' ? 'text-text-tertiary' : 'text-text-primary'
                )}
              >
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className="relative mx-3 mb-5 h-0.5 flex-1 overflow-hidden rounded-full bg-border">
                <div
                  className="absolute inset-y-0 left-0 rounded-full bg-action transition-all duration-500 ease-out"
                  style={{ width: i < activeIndex ? '100%' : '0%' }}
                />
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
