import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import { Plug, PlusCircle, Zap, Pencil, Trash2, CheckCircle2, XCircle, HelpCircle } from 'lucide-react'
import { connectionsApi } from '@/api/connections'
import { Connection } from '@/types'
import {
  PageHeader,
  Button,
  DataTable,
  EngineIcon,
  Badge,
  EmptyState,
  ConfirmDialog,
} from '@/components/common'
import { useDisclosure } from '@/hooks/useDisclosure'
import { usePermission } from '@/hooks/usePermission'
import { formatRelativeTime } from '@/utils/format'
import { ConnectionFormDrawer } from '@/components/features/connections/ConnectionFormDrawer'

const STATUS_META = {
  healthy: { label: 'Healthy', tone: 'success' as const, icon: CheckCircle2 },
  failed: { label: 'Failed', tone: 'error' as const, icon: XCircle },
  untested: { label: 'Untested', tone: 'neutral' as const, icon: HelpCircle },
}

export default function Connections() {
  const queryClient = useQueryClient()
  const canWrite = usePermission('connections:*')
  const drawer = useDisclosure()
  const [editingConnection, setEditingConnection] = useState<Connection | null>(null)
  const [deletingConnection, setDeletingConnection] = useState<Connection | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)

  const { data: connections = [], isLoading } = useQuery({
    queryKey: ['connections', 'list'],
    queryFn: connectionsApi.list,
  })

  const testMutation = useMutation({
    mutationFn: (id: string) => connectionsApi.test(id),
    onMutate: (id) => setTestingId(id),
    onSuccess: (result) => {
      if (result.success) {
        toast.success(`Connected in ${result.latency_ms}ms`)
      } else {
        toast.error(result.error || 'Connection test failed')
      }
      queryClient.invalidateQueries({ queryKey: ['connections'] })
    },
    onError: () => toast.error('Connection test failed'),
    onSettled: () => setTestingId(null),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => connectionsApi.remove(id),
    onSuccess: () => {
      toast.success('Connection deleted')
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      setDeletingConnection(null)
    },
    onError: () => toast.error('Failed to delete connection'),
  })

  const columns: ColumnDef<Connection>[] = [
    {
      header: 'Name',
      accessorKey: 'name',
      cell: ({ row }) => (
        <div className="flex items-center gap-3">
          <EngineIcon engine={row.original.engine} size="sm" />
          <div>
            <p className="font-medium text-text-primary">{row.original.name}</p>
            <p className="text-tiny text-text-tertiary">{row.original.database}</p>
          </div>
        </div>
      ),
    },
    {
      header: 'Host',
      accessorFn: (row) => `${row.host}:${row.port}`,
      cell: ({ getValue }) => <span className="mono text-small">{getValue<string>()}</span>,
    },
    {
      header: 'Status',
      accessorKey: 'status',
      cell: ({ getValue }) => {
        const meta = STATUS_META[getValue<Connection['status']>()]
        return (
          <Badge tone={meta.tone}>
            <meta.icon className="h-3 w-3" /> {meta.label}
          </Badge>
        )
      },
    },
    {
      header: 'Last tested',
      accessorKey: 'last_tested_at',
      cell: ({ getValue }) => (
        <span className="text-small text-text-secondary">{formatRelativeTime(getValue<string | null>())}</span>
      ),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            isLoading={testingId === row.original.id}
            leftIcon={<Zap className="h-3.5 w-3.5" />}
            onClick={(e) => {
              e.stopPropagation()
              testMutation.mutate(row.original.id)
            }}
          >
            Test
          </Button>
          {canWrite && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation()
                  setEditingConnection(row.original)
                  drawer.open()
                }}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation()
                  setDeletingConnection(row.original)
                }}
              >
                <Trash2 className="h-3.5 w-3.5 text-error" />
              </Button>
            </>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Connections"
        description="Source and target data stores available to your migration jobs."
        actions={
          canWrite && (
            <Button
              leftIcon={<PlusCircle className="h-4 w-4" />}
              onClick={() => {
                setEditingConnection(null)
                drawer.open()
              }}
            >
              Add connection
            </Button>
          )
        }
      />

      {!isLoading && connections.length === 0 ? (
        <EmptyState
          icon={Plug}
          title="No connections yet"
          description="Add your first source or target connection to start building migrations."
          actionLabel={canWrite ? 'Add connection' : undefined}
          onAction={canWrite ? drawer.open : undefined}
        />
      ) : (
        <DataTable columns={columns} data={connections} isLoading={isLoading} emptyMessage="No connections found" />
      )}

      <ConnectionFormDrawer
        isOpen={drawer.isOpen}
        onClose={() => {
          drawer.close()
          setEditingConnection(null)
        }}
        connection={editingConnection}
      />

      <ConfirmDialog
        isOpen={!!deletingConnection}
        onClose={() => setDeletingConnection(null)}
        onConfirm={() => deletingConnection && deleteMutation.mutate(deletingConnection.id)}
        title={`Delete "${deletingConnection?.name}"?`}
        description="This connection will be removed. Migration jobs already using it will be unaffected, but you won't be able to start new ones with it."
        confirmLabel="Delete"
        isLoading={deleteMutation.isPending}
      />
    </div>
  )
}
