import React, { useEffect } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Drawer, Button, FormField, Input, Select } from '@/components/common'
import { schedulerApi } from '@/api/scheduler'
import { ScheduledJob, ScheduleJobType } from '@/types'

const JOB_TYPES: ScheduleJobType[] = ['intelligence_scan', 'data_quality_scan', 'benchmark', 'report', 'migration']

const PRESETS = [
  { label: 'Every hour', cron: '0 * * * *' },
  { label: 'Every day at 2 AM', cron: '0 2 * * *' },
  { label: 'Every Monday at 9 AM', cron: '0 9 * * 1' },
  { label: 'First of month', cron: '0 0 1 * *' },
]

interface Props {
  isOpen: boolean
  onClose: () => void
  job?: ScheduledJob | null
}

export function ScheduledJobDrawer({ isOpen, onClose, job }: Props) {
  const isEdit = !!job
  const queryClient = useQueryClient()
  const { register, handleSubmit, control, reset, setValue } = useForm({
    defaultValues: { name: '', job_type: 'intelligence_scan' as ScheduleJobType, cron_expression: '0 2 * * *', timezone: 'UTC' },
  })

  useEffect(() => {
    if (isOpen) {
      reset(
        job
          ? { name: job.name, job_type: job.job_type, cron_expression: job.cron_expression, timezone: job.timezone }
          : { name: '', job_type: 'intelligence_scan', cron_expression: '0 2 * * *', timezone: 'UTC' }
      )
    }
  }, [isOpen, job, reset])

  const saveMutation = useMutation({
    mutationFn: (values: any) => (isEdit ? schedulerApi.update(job!.id, values) : schedulerApi.create(values)),
    onSuccess: () => {
      toast.success(isEdit ? 'Schedule updated' : 'Schedule created')
      queryClient.invalidateQueries({ queryKey: ['scheduler', 'jobs'] })
      onClose()
    },
    onError: () => toast.error('Failed to save schedule'),
  })

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={isEdit ? 'Edit scheduled job' : 'New scheduled job'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSubmit((v) => saveMutation.mutate(v))} isLoading={saveMutation.isPending}>Save</Button>
        </>
      }
    >
      <form>
        <FormField label="Name" required>
          <Input placeholder="Nightly data quality scan" {...register('name')} />
        </FormField>
        <FormField label="Job type" required>
          <Controller
            control={control}
            name="job_type"
            render={({ field }) => (
              <Select {...field}>
                {JOB_TYPES.map((t) => (
                  <option key={t} value={t}>{t.replace('_', ' ')}</option>
                ))}
              </Select>
            )}
          />
        </FormField>
        <FormField label="Cron expression" required hint="Standard 5-field cron syntax (minute hour day month weekday)">
          <Input className="mono" {...register('cron_expression')} />
        </FormField>
        <div className="mb-4 flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.cron}
              type="button"
              onClick={() => setValue('cron_expression', p.cron)}
              className="rounded-pill border border-border px-2.5 py-1 text-tiny text-text-secondary hover:border-action hover:text-action"
            >
              {p.label}
            </button>
          ))}
        </div>
        <FormField label="Timezone" required>
          <Input {...register('timezone')} />
        </FormField>
      </form>
    </Drawer>
  )
}
