import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ColumnDef } from '@tanstack/react-table'
import { ScrollText } from 'lucide-react'
import { usersApi, AuditLogEntry } from '@/api/users'
import { PageHeader, Input, DataTable, Badge, EmptyState } from '@/components/common'
import { useDebounce } from '@/hooks/useDebounce'
import { formatDateTime } from '@/utils/format'

export default function AuditLog() {
  const [actionFilter, setActionFilter] = useState('')
  const debouncedAction = useDebounce(actionFilter, 300)

  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['audit-log', debouncedAction],
    queryFn: () => usersApi.auditLog({ action: debouncedAction || undefined, limit: 200 }),
  })

  const columns: ColumnDef<AuditLogEntry>[] = [
    { header: 'Time', accessorKey: 'created_at', cell: ({ getValue }) => <span className="text-small text-text-secondary">{formatDateTime(getValue<string>())}</span> },
    { header: 'User', accessorKey: 'user_email' },
    { header: 'Action', accessorKey: 'action', cell: ({ getValue }) => <Badge tone="info">{String(getValue())}</Badge> },
    { header: 'Resource', accessorFn: (row) => `${row.resource_type}${row.resource_id ? ` · ${row.resource_id.slice(0, 8)}` : ''}`, cell: ({ getValue }) => <span className="mono text-small">{getValue<string>()}</span> },
    { header: 'IP address', accessorKey: 'ip_address', cell: ({ getValue }) => <span className="text-small text-text-secondary">{getValue<string | null>() ?? '—'}</span> },
  ]

  return (
    <div>
      <PageHeader title="Audit Log" description="Every meaningful action taken across your organization." />

      <div className="mb-5 max-w-sm">
        <Input placeholder="Filter by action (e.g. job.start)…" value={actionFilter} onChange={(e) => setActionFilter(e.target.value)} />
      </div>

      {!isLoading && entries.length === 0 ? (
        <EmptyState icon={ScrollText} title="No audit entries" description="Nothing matches this filter yet." />
      ) : (
        <DataTable columns={columns} data={entries} isLoading={isLoading} />
      )}
    </div>
  )
}
