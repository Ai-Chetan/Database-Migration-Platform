import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import { Download, FileBarChart, PlusCircle, Trash2 } from 'lucide-react'
import { reportsApi, Report } from '@/api/reports'
import { jobsApi } from '@/api/jobs'
import { ReportType } from '@/types'
import { PageHeader, Button, DataTable, Badge, EmptyState, Modal, FormField, Select, ConfirmDialog } from '@/components/common'
import { useDisclosure } from '@/hooks/useDisclosure'
import { formatDateTime } from '@/utils/format'

const REPORT_TYPES: ReportType[] = [
  'migration_summary',
  'validation_report',
  'performance_report',
  'audit_report',
  'data_quality_report',
  'compliance_report',
]

function GenerateReportModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [reportType, setReportType] = useState<ReportType>('migration_summary')
  const [jobId, setJobId] = useState('')
  const [format, setFormat] = useState('pdf')

  const { data: jobs = [] } = useQuery({ queryKey: ['jobs', 'list'], queryFn: () => jobsApi.list({ limit: 50 }) })

  const generateMutation = useMutation({
    mutationFn: () => reportsApi.generate({ report_type: reportType, job_id: jobId || undefined, format }),
    onSuccess: () => {
      toast.success('Report generation started')
      queryClient.invalidateQueries({ queryKey: ['reports'] })
      onClose()
    },
    onError: () => toast.error('Failed to generate report'),
  })

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Generate report"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button isLoading={generateMutation.isPending} onClick={() => generateMutation.mutate()}>Generate</Button>
        </>
      }
    >
      <FormField label="Report type" required>
        <Select value={reportType} onChange={(e) => setReportType(e.target.value as ReportType)}>
          {REPORT_TYPES.map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
          ))}
        </Select>
      </FormField>
      <FormField label="Related job" hint="Optional — leave blank for a platform-wide report">
        <Select value={jobId} onChange={(e) => setJobId(e.target.value)}>
          <option value="">None</option>
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>{j.name}</option>
          ))}
        </Select>
      </FormField>
      <FormField label="Format" required>
        <Select value={format} onChange={(e) => setFormat(e.target.value)}>
          <option value="pdf">PDF</option>
          <option value="html">HTML</option>
          <option value="json">JSON</option>
        </Select>
      </FormField>
    </Modal>
  )
}

export default function Reports() {
  const queryClient = useQueryClient()
  const generateModal = useDisclosure()
  const [deletingReport, setDeletingReport] = useState<Report | null>(null)

  const { data: reports = [], isLoading } = useQuery({ queryKey: ['reports', 'list'], queryFn: () => reportsApi.list() })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => reportsApi.remove(id),
    onSuccess: () => {
      toast.success('Report deleted')
      queryClient.invalidateQueries({ queryKey: ['reports'] })
      setDeletingReport(null)
    },
  })

  const columns: ColumnDef<Report>[] = [
    { header: 'Title', accessorKey: 'title' },
    { header: 'Type', accessorKey: 'report_type', cell: ({ getValue }) => <Badge tone="neutral">{String(getValue()).replace(/_/g, ' ')}</Badge> },
    { header: 'Format', accessorKey: 'format', cell: ({ getValue }) => <span className="uppercase text-small">{getValue<string>()}</span> },
    { header: 'Status', accessorKey: 'status', cell: ({ getValue }) => <Badge tone={getValue() === 'ready' ? 'success' : getValue() === 'failed' ? 'error' : 'info'}>{String(getValue())}</Badge> },
    { header: 'Created', accessorKey: 'created_at', cell: ({ getValue }) => <span className="text-small text-text-secondary">{formatDateTime(getValue<string>())}</span> },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <div className="flex justify-end gap-1">
          {row.original.download_url && (
            <a href={row.original.download_url} target="_blank" rel="noreferrer">
              <Button variant="ghost" size="sm"><Download className="h-3.5 w-3.5" /></Button>
            </a>
          )}
          <Button variant="ghost" size="sm" onClick={() => setDeletingReport(row.original)}>
            <Trash2 className="h-3.5 w-3.5 text-error" />
          </Button>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Reports"
        description="Migration summaries, validation, performance, audit, and compliance reports."
        actions={<Button leftIcon={<PlusCircle className="h-4 w-4" />} onClick={generateModal.open}>Generate report</Button>}
      />

      {!isLoading && reports.length === 0 ? (
        <EmptyState icon={FileBarChart} title="No reports yet" description="Generate your first report to see it here." actionLabel="Generate report" onAction={generateModal.open} />
      ) : (
        <DataTable columns={columns} data={reports} isLoading={isLoading} />
      )}

      <GenerateReportModal isOpen={generateModal.isOpen} onClose={generateModal.close} />

      <ConfirmDialog
        isOpen={!!deletingReport}
        onClose={() => setDeletingReport(null)}
        onConfirm={() => deletingReport && deleteMutation.mutate(deletingReport.id)}
        title="Delete report?"
        description={`"${deletingReport?.title}" will be permanently removed.`}
        confirmLabel="Delete"
        isLoading={deleteMutation.isPending}
      />
    </div>
  )
}
