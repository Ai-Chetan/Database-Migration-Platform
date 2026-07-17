import React, { useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { AlertOctagon, AlertTriangle, Lightbulb } from 'lucide-react'
import { intelligenceApi } from '@/api/intelligence'
import { Card, Badge, Spinner } from '@/components/common'
import { COMPLEXITY_META } from '@/utils/meta'
import { formatNumber, formatGB } from '@/utils/format'
import { WizardState } from '../wizardState'

interface Props {
  state: WizardState
  update: (patch: Partial<WizardState>) => void
}

export function Step2Assessment({ state, update }: Props) {
  const assessMutation = useMutation({
    mutationFn: () => intelligenceApi.assess(state.sourceConnectionId),
    onSuccess: (assessment) => update({ assessment, workerCount: assessment.recommended_workers }),
  })

  useEffect(() => {
    if (!state.assessment && state.sourceConnectionId) {
      assessMutation.mutate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.sourceConnectionId])

  if (assessMutation.isPending || (!state.assessment && !assessMutation.isError)) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Spinner size="lg" />
        <p className="text-body text-text-secondary">Scanning source database — analyzing tables, row counts, and data types…</p>
      </div>
    )
  }

  if (assessMutation.isError) {
    return (
      <div className="rounded border border-error/30 bg-red-50 p-6 text-center">
        <p className="text-body text-error">Couldn't complete the assessment.</p>
        <button className="mt-2 text-small text-action hover:underline" onClick={() => assessMutation.mutate()}>
          Try again
        </button>
      </div>
    )
  }

  const a = state.assessment!
  const complexityMeta = COMPLEXITY_META[a.complexity]

  return (
    <div className="max-w-2xl space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card padding="sm">
          <p className="text-tiny text-text-secondary">Tables</p>
          <p className="mono mt-1 text-h3 tabular-nums text-text-primary">{formatNumber(a.total_tables)}</p>
        </Card>
        <Card padding="sm">
          <p className="text-tiny text-text-secondary">Rows</p>
          <p className="mono mt-1 text-h3 tabular-nums text-text-primary">{formatNumber(a.total_rows)}</p>
        </Card>
        <Card padding="sm">
          <p className="text-tiny text-text-secondary">Size</p>
          <p className="mono mt-1 text-h3 tabular-nums text-text-primary">{formatGB(a.total_size_gb)}</p>
        </Card>
        <Card padding="sm">
          <p className="text-tiny text-text-secondary">Est. duration</p>
          <p className="mono mt-1 text-h3 tabular-nums text-text-primary">{a.estimated_duration}</p>
        </Card>
      </div>

      <div className="flex items-center gap-3">
        <span className="text-small text-text-secondary">Complexity:</span>
        <Badge tone={complexityMeta.color.includes('success') ? 'success' : complexityMeta.color.includes('warning') ? 'warning' : 'error'}>
          {complexityMeta.label}
        </Badge>
        <span className="text-small text-text-secondary">Risk level:</span>
        <span className="text-small font-medium text-text-primary">{a.risk_level}</span>
      </div>

      {a.blocking_issues.length > 0 && (
        <div className="rounded border border-error/30 bg-red-50 p-4">
          <p className="mb-2 flex items-center gap-2 text-small font-semibold text-error">
            <AlertOctagon className="h-4 w-4" /> Blocking issues
          </p>
          <ul className="list-disc space-y-1 pl-5 text-small text-text-secondary">
            {a.blocking_issues.map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
          </ul>
        </div>
      )}

      {a.warnings.length > 0 && (
        <div className="rounded border border-warning/30 bg-amber-50 p-4">
          <p className="mb-2 flex items-center gap-2 text-small font-semibold text-warning">
            <AlertTriangle className="h-4 w-4" /> Warnings
          </p>
          <ul className="list-disc space-y-1 pl-5 text-small text-text-secondary">
            {a.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {a.recommendations.length > 0 && (
        <div className="rounded border border-border bg-surface p-4">
          <p className="mb-2 flex items-center gap-2 text-small font-semibold text-text-primary">
            <Lightbulb className="h-4 w-4 text-action" /> Recommendations
          </p>
          <ul className="list-disc space-y-1 pl-5 text-small text-text-secondary">
            {a.recommendations.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
