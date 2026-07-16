import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { GitCompare, FileCode2, CheckCircle2, Pencil } from 'lucide-react'
import { schemaApi } from '@/api/schema'
import { MappingTable, MappingColumn } from '@/types'
import { PageHeader, Button, Select, Badge, EmptyState, Spinner, Card } from '@/components/common'
import { cn } from '@/utils/cn'
import { ColumnMappingDrawer } from '@/components/features/schema/ColumnMappingDrawer'
import { useDisclosure } from '@/hooks/useDisclosure'

export default function SchemaExplorer() {
  const queryClient = useQueryClient()
  const drawer = useDisclosure()
  const [projectId, setProjectId] = useState('')
  const [selectedTable, setSelectedTable] = useState<MappingTable | null>(null)
  const [editingColumn, setEditingColumn] = useState<MappingColumn | null>(null)

  const { data: projects = [] } = useQuery({ queryKey: ['schema', 'projects'], queryFn: schemaApi.listProjects })

  const { data: tables = [], isLoading: tablesLoading } = useQuery({
    queryKey: ['schema', 'tables', projectId],
    queryFn: () => schemaApi.listTables(projectId),
    enabled: !!projectId,
  })

  const { data: columns = [], isLoading: columnsLoading } = useQuery({
    queryKey: ['schema', 'columns', selectedTable?.id],
    queryFn: () => schemaApi.listColumns(selectedTable!.id),
    enabled: !!selectedTable,
  })

  const validateMutation = useMutation({
    mutationFn: () => schemaApi.validateMappings(projectId),
    onSuccess: (result: any) => {
      toast.success(result?.valid ? 'All mappings are valid' : 'Validation completed — check for warnings')
    },
    onError: () => toast.error('Validation failed'),
  })

  const generateScriptMutation = useMutation({
    mutationFn: () => schemaApi.generateScript(projectId),
    onSuccess: () => toast.success('Migration script generated'),
    onError: () => toast.error('Failed to generate script'),
  })

  return (
    <div>
      <PageHeader
        title="Schema Mapping"
        description="Review and fine-tune how source tables and columns map to the target schema."
        actions={
          projectId && (
            <div className="flex gap-2">
              <Button variant="secondary" leftIcon={<CheckCircle2 className="h-4 w-4" />} isLoading={validateMutation.isPending} onClick={() => validateMutation.mutate()}>
                Validate
              </Button>
              <Button leftIcon={<FileCode2 className="h-4 w-4" />} isLoading={generateScriptMutation.isPending} onClick={() => generateScriptMutation.mutate()}>
                Generate script
              </Button>
            </div>
          )
        }
      />

      <div className="mb-5 max-w-sm">
        <Select value={projectId} onChange={(e) => { setProjectId(e.target.value); setSelectedTable(null) }}>
          <option value="">Select a mapping project…</option>
          {projects.map((p: any) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </Select>
      </div>

      {!projectId ? (
        <EmptyState icon={GitCompare} title="No project selected" description="Choose a mapping project above, or create one from the New Migration wizard." />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Table list */}
          <Card padding="none" className="lg:col-span-1">
            <div className="border-b border-border px-5 py-3">
              <p className="text-h4 text-text-primary">Tables</p>
            </div>
            {tablesLoading ? (
              <div className="p-6"><Spinner /></div>
            ) : (
              <div className="max-h-[600px] divide-y divide-border overflow-y-auto scrollbar-thin">
                {tables.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setSelectedTable(t)}
                    className={cn(
                      'flex w-full items-center justify-between px-5 py-3 text-left hover:bg-surface',
                      selectedTable?.id === t.id && 'bg-action/5'
                    )}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-small font-medium text-text-primary mono">{t.source_table}</p>
                      <p className="truncate text-tiny text-text-tertiary">→ {t.target_table}</p>
                    </div>
                    <Badge tone={t.status === 'mapped' ? 'success' : 'neutral'}>
                      {t.mapped_count}/{t.column_count}
                    </Badge>
                  </button>
                ))}
              </div>
            )}
          </Card>

          {/* Column mapping editor */}
          <Card padding="none" className="lg:col-span-2">
            <div className="border-b border-border px-5 py-3">
              <p className="text-h4 text-text-primary">
                {selectedTable ? `${selectedTable.source_table} → ${selectedTable.target_table}` : 'Column mappings'}
              </p>
            </div>
            {!selectedTable ? (
              <div className="p-10 text-center text-body text-text-secondary">Select a table to view its column mappings.</div>
            ) : columnsLoading ? (
              <div className="p-6"><Spinner /></div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-small">
                  <thead className="bg-surface text-tiny uppercase text-text-secondary">
                    <tr>
                      <th className="px-4 py-2">Source</th>
                      <th className="px-4 py-2">Target</th>
                      <th className="px-4 py-2">Kind</th>
                      <th className="px-4 py-2">Safety</th>
                      <th className="px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {columns.map((c) => (
                      <tr key={c.id} className="border-t border-border">
                        <td className="px-4 py-2">
                          <p className="mono">{c.source_column}</p>
                          <p className="text-tiny text-text-tertiary">{c.source_type}</p>
                        </td>
                        <td className="px-4 py-2">
                          <p className="mono">{c.target_column}</p>
                          <p className="text-tiny text-text-tertiary">{c.target_type}</p>
                        </td>
                        <td className="px-4 py-2">
                          <Badge tone="neutral">{c.mapping_kind}</Badge>
                        </td>
                        <td className="px-4 py-2">
                          <Badge tone={c.conversion_safety === 'safe' ? 'success' : c.conversion_safety === 'lossy' ? 'warning' : 'error'}>
                            {c.conversion_safety}
                          </Badge>
                        </td>
                        <td className="px-4 py-2 text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setEditingColumn(c)
                              drawer.open()
                            }}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      <ColumnMappingDrawer
        isOpen={drawer.isOpen}
        onClose={() => {
          drawer.close()
          setEditingColumn(null)
        }}
        column={editingColumn}
        tableId={selectedTable?.id ?? ''}
      />
    </div>
  )
}
