import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { connectionsApi } from '@/api/connections'
import { FormField, Input, Select, EngineIcon, SkeletonCard } from '@/components/common'
import { WizardState } from '../wizardState'

interface Props {
  state: WizardState
  update: (patch: Partial<WizardState>) => void
}

export function Step1SelectConnections({ state, update }: Props) {
  const { data: connections = [], isLoading } = useQuery({
    queryKey: ['connections', 'list'],
    queryFn: connectionsApi.list,
  })

  if (isLoading) return <SkeletonCard />

  const source = connections.find((c) => c.id === state.sourceConnectionId)
  const target = connections.find((c) => c.id === state.targetConnectionId)

  return (
    <div className="max-w-xl">
      <FormField label="Migration name" required>
        <Input
          placeholder="e.g. Orders DB → PostgreSQL Cutover"
          value={state.jobName}
          onChange={(e) => update({ jobName: e.target.value })}
        />
      </FormField>

      <div className="mb-4 flex items-center gap-4">
        <div className="flex-1">
          <FormField label="Source connection" required>
            <Select
              value={state.sourceConnectionId}
              onChange={(e) => update({ sourceConnectionId: e.target.value })}
            >
              <option value="">Select source…</option>
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </FormField>
        </div>
        <ArrowRight className="mt-2 h-4 w-4 shrink-0 text-text-tertiary" />
        <div className="flex-1">
          <FormField label="Target connection" required>
            <Select
              value={state.targetConnectionId}
              onChange={(e) => update({ targetConnectionId: e.target.value })}
            >
              <option value="">Select target…</option>
              {connections
                .filter((c) => c.id !== state.sourceConnectionId)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </Select>
          </FormField>
        </div>
      </div>

      {source && target && (
        <div className="flex items-center gap-4 rounded border border-border bg-surface p-4">
          <EngineIcon engine={source.engine} showLabel />
          <ArrowRight className="h-4 w-4 shrink-0 text-text-tertiary" />
          <EngineIcon engine={target.engine} showLabel />
        </div>
      )}

      {connections.length === 0 && (
        <p className="text-small text-text-secondary">
          No connections found — add one from the Connections page first.
        </p>
      )}
    </div>
  )
}
