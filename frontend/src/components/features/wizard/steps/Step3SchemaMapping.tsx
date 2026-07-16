import React, { useEffect } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, GitCompare, Wand2 } from 'lucide-react'
import { schemaApi } from '@/api/schema'
import { Button, Spinner, Badge } from '@/components/common'
import { WizardState } from '../wizardState'

interface Props {
  state: WizardState
  update: (patch: Partial<WizardState>) => void
}

export function Step3SchemaMapping({ state, update }: Props) {
  const createProjectMutation = useMutation({
    mutationFn: () =>
      schemaApi.createProject({
        name: `${state.jobName} — mapping`,
        source_connection_id: state.sourceConnectionId,
        target_connection_id: state.targetConnectionId,
      }),
    onSuccess: (project) => update({ projectId: project.id }),
  })

  useEffect(() => {
    if (!state.projectId) createProjectMutation.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const discoverMutation = useMutation({
    mutationFn: () => schemaApi.discover(state.projectId as string, state.sourceConnectionId),
    onSuccess: () => update({ tablesDiscovered: true }),
  })

  const autoMapMutation = useMutation({
    mutationFn: () => schemaApi.autoMapTables(state.projectId as string),
    onSuccess: () => update({ tablesAutoMapped: true }),
  })

  const { data: tables = [], refetch } = useQuery({
    queryKey: ['schema', state.projectId, 'tables'],
    queryFn: () => schemaApi.listTables(state.projectId as string),
    enabled: !!state.projectId && state.tablesDiscovered,
  })

  if (createProjectMutation.isPending || !state.projectId) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Spinner size="lg" />
        <p className="text-body text-text-secondary">Setting up the mapping project…</p>
      </div>
    )
  }

  return (
    <div className="max-w-2xl space-y-5">
      <div className="rounded border border-border bg-white p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <GitCompare className="h-4 w-4 text-action" />
            <p className="text-body font-medium text-text-primary">Discover source schema</p>
          </div>
          {state.tablesDiscovered ? (
            <Badge tone="success">
              <CheckCircle2 className="h-3 w-3" /> Discovered
            </Badge>
          ) : (
            <Button size="sm" isLoading={discoverMutation.isPending} onClick={() => discoverMutation.mutate()}>
              Run discovery
            </Button>
          )}
        </div>
        <p className="mt-1 text-small text-text-secondary">
          Scans every table, column, index, and constraint in the source connection.
        </p>
      </div>

      {state.tablesDiscovered && (
        <div className="rounded border border-border bg-white p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Wand2 className="h-4 w-4 text-action" />
              <p className="text-body font-medium text-text-primary">Auto-map tables & columns</p>
            </div>
            {state.tablesAutoMapped ? (
              <Badge tone="success">
                <CheckCircle2 className="h-3 w-3" /> Mapped
              </Badge>
            ) : (
              <Button
                size="sm"
                isLoading={autoMapMutation.isPending}
                onClick={() => autoMapMutation.mutate(undefined, { onSuccess: () => refetch() })}
              >
                Auto-map
              </Button>
            )}
          </div>
          <p className="mt-1 text-small text-text-secondary">
            Applies suggested type conversions and column-name matching. You can fine-tune every mapping later from
            the Schema Mapping page.
          </p>
        </div>
      )}

      {tables.length > 0 && (
        <div className="overflow-hidden rounded border border-border">
          <table className="w-full text-left text-small">
            <thead className="bg-surface text-tiny uppercase text-text-secondary">
              <tr>
                <th className="px-4 py-2">Source table</th>
                <th className="px-4 py-2">Target table</th>
                <th className="px-4 py-2">Columns</th>
                <th className="px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {tables.map((t) => (
                <tr key={t.id} className="border-t border-border">
                  <td className="px-4 py-2 mono">{t.source_table}</td>
                  <td className="px-4 py-2 mono">{t.target_table}</td>
                  <td className="px-4 py-2">
                    {t.mapped_count}/{t.column_count}
                  </td>
                  <td className="px-4 py-2">
                    <Badge tone={t.status === 'mapped' ? 'success' : 'neutral'}>{t.status}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
