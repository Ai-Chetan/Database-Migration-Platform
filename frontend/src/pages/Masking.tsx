import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import { PlusCircle, ShieldCheck, Wand2, Pencil, Trash2 } from 'lucide-react'
import { schemaApi } from '@/api/schema'
import { maskingApi } from '@/api/masking'
import { MaskingRule } from '@/types'
import { PageHeader, Button, Select, DataTable, Badge, EmptyState, ConfirmDialog } from '@/components/common'
import { useDisclosure } from '@/hooks/useDisclosure'
import { usePermission } from '@/hooks/usePermission'
import { MaskingRuleDrawer } from '@/components/features/masking/MaskingRuleDrawer'

export default function Masking() {
  const queryClient = useQueryClient()
  const canWrite = usePermission('masking:*')
  const drawer = useDisclosure()
  const [projectId, setProjectId] = useState('')
  const [editingRule, setEditingRule] = useState<MaskingRule | null>(null)
  const [deletingRule, setDeletingRule] = useState<MaskingRule | null>(null)

  const { data: projects = [] } = useQuery({ queryKey: ['schema', 'projects'], queryFn: schemaApi.listProjects })

  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['masking', 'rules', projectId],
    queryFn: () => maskingApi.listRules(projectId),
    enabled: !!projectId,
  })

  const suggestMutation = useMutation({
    mutationFn: () => maskingApi.suggestRules(projectId),
    onSuccess: () => {
      toast.success('Suggested rules added — review before saving your migration')
      queryClient.invalidateQueries({ queryKey: ['masking', 'rules', projectId] })
    },
    onError: () => toast.error('Failed to generate suggestions'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => maskingApi.deleteRule(id),
    onSuccess: () => {
      toast.success('Rule deleted')
      queryClient.invalidateQueries({ queryKey: ['masking', 'rules', projectId] })
      setDeletingRule(null)
    },
  })

  const columns: ColumnDef<MaskingRule>[] = [
    { header: 'Table', accessorKey: 'table_name', cell: ({ getValue }) => <span className="mono text-small">{getValue<string>()}</span> },
    { header: 'Column', accessorKey: 'column_name', cell: ({ getValue }) => <span className="mono text-small">{getValue<string>()}</span> },
    { header: 'Strategy', accessorKey: 'strategy', cell: ({ getValue }) => <Badge tone="info">{String(getValue()).replace('_', ' ')}</Badge> },
    { header: 'Status', accessorKey: 'is_active', cell: ({ getValue }) => <Badge tone={getValue() ? 'success' : 'neutral'}>{getValue() ? 'Active' : 'Disabled'}</Badge> },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) =>
        canWrite ? (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="sm" onClick={() => { setEditingRule(row.original); drawer.open() }}>
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDeletingRule(row.original)}>
              <Trash2 className="h-3.5 w-3.5 text-error" />
            </Button>
          </div>
        ) : null,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Data Masking"
        description="Protect sensitive columns before they land in the target system."
        actions={
          projectId &&
          canWrite && (
            <div className="flex gap-2">
              <Button variant="secondary" leftIcon={<Wand2 className="h-4 w-4" />} isLoading={suggestMutation.isPending} onClick={() => suggestMutation.mutate()}>
                Suggest rules
              </Button>
              <Button leftIcon={<PlusCircle className="h-4 w-4" />} onClick={() => { setEditingRule(null); drawer.open() }}>
                Add rule
              </Button>
            </div>
          )
        }
      />

      <div className="mb-5 max-w-sm">
        <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          <option value="">Select a mapping project…</option>
          {projects.map((p: any) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </Select>
      </div>

      {!projectId ? (
        <EmptyState icon={ShieldCheck} title="No project selected" description="Choose a mapping project to manage its masking rules." />
      ) : (
        <DataTable columns={columns} data={rules} isLoading={isLoading} emptyMessage="No masking rules configured for this project yet" />
      )}

      <MaskingRuleDrawer
        isOpen={drawer.isOpen}
        onClose={() => { drawer.close(); setEditingRule(null) }}
        rule={editingRule}
        projectId={projectId}
      />

      <ConfirmDialog
        isOpen={!!deletingRule}
        onClose={() => setDeletingRule(null)}
        onConfirm={() => deletingRule && deleteMutation.mutate(deletingRule.id)}
        title="Delete masking rule?"
        description={`${deletingRule?.table_name}.${deletingRule?.column_name} will no longer be masked during migration.`}
        confirmLabel="Delete"
        isLoading={deleteMutation.isPending}
      />
    </div>
  )
}
