import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { connectionsApi } from '@/api/connections'
import { Card, EngineIcon } from '@/components/common'
import { formatNumber, formatGB } from '@/utils/format'
import { WizardState } from '../wizardState'

export function Step5Review({ state }: { state: WizardState }) {
  const { data: connections = [] } = useQuery({ queryKey: ['connections', 'list'], queryFn: connectionsApi.list })
  const source = connections.find((c) => c.id === state.sourceConnectionId)
  const target = connections.find((c) => c.id === state.targetConnectionId)

  return (
    <div className="max-w-2xl space-y-4">
      <Card>
        <p className="mb-3 text-h4 text-text-primary">{state.jobName || 'Untitled migration'}</p>
        {source && target && (
          <div className="flex items-center gap-4">
            <EngineIcon engine={source.engine} showLabel />
            <span className="text-text-tertiary">→</span>
            <EngineIcon engine={target.engine} showLabel />
          </div>
        )}
      </Card>

      {state.assessment && (
        <Card>
          <p className="mb-2 text-small font-semibold text-text-primary">Assessment</p>
          <div className="grid grid-cols-2 gap-y-1.5 text-small text-text-secondary sm:grid-cols-4">
            <p>Tables: <span className="font-medium text-text-primary">{formatNumber(state.assessment.total_tables)}</span></p>
            <p>Rows: <span className="font-medium text-text-primary">{formatNumber(state.assessment.total_rows)}</span></p>
            <p>Size: <span className="font-medium text-text-primary">{formatGB(state.assessment.total_size_gb)}</span></p>
            <p>Complexity: <span className="font-medium text-text-primary">{state.assessment.complexity}</span></p>
          </div>
        </Card>
      )}

      <Card>
        <p className="mb-2 text-small font-semibold text-text-primary">Execution plan</p>
        <div className="grid grid-cols-2 gap-y-1.5 text-small text-text-secondary">
          <p>Worker count: <span className="font-medium text-text-primary">{state.workerCount}</span></p>
          <p>
            Estimated duration:{' '}
            <span className="font-medium text-text-primary">{state.assessment?.estimated_duration ?? '—'}</span>
          </p>
          <p>Schema mapping: <span className="font-medium text-text-primary">{state.tablesAutoMapped ? 'Auto-mapped' : 'Pending'}</span></p>
        </div>
      </Card>

      <p className="text-small text-text-secondary">
        Clicking <span className="font-medium text-text-primary">Launch migration</span> will create the job and start
        execution immediately with {state.workerCount} workers. You can pause or cancel at any time from the
        Operations Console.
      </p>
    </div>
  )
}
