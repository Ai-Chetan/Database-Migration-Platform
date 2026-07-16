import React, { useEffect } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Drawer, Button, FormField, Input, Select } from '@/components/common'
import { maskingApi } from '@/api/masking'
import { MaskingRule, MaskingStrategy } from '@/types'

const STRATEGIES: MaskingStrategy[] = ['hash', 'redact', 'partial', 'encrypt', 'nullify', 'fixed_value', 'format_preserve', 'synthesize']

const STRATEGY_HINTS: Record<MaskingStrategy, string> = {
  hash: 'One-way hash — irreversible, deterministic (same input → same output).',
  redact: 'Replaces the entire value with a fixed mask, e.g. "***".',
  partial: 'Masks part of the value, e.g. keeps last 4 digits of a card number.',
  encrypt: 'Reversible encryption — recoverable with the platform key.',
  nullify: 'Replaces the value with NULL.',
  fixed_value: 'Replaces the value with a constant you specify.',
  format_preserve: 'Masks the value but keeps its format (e.g. valid-looking email).',
  synthesize: 'Replaces the value with realistic synthetic data.',
}

interface Props {
  isOpen: boolean
  onClose: () => void
  rule?: MaskingRule | null
  projectId: string
}

export function MaskingRuleDrawer({ isOpen, onClose, rule, projectId }: Props) {
  const isEdit = !!rule
  const queryClient = useQueryClient()
  const { register, handleSubmit, control, reset, watch } = useForm({
    defaultValues: {
      table_name: '',
      column_name: '',
      strategy: 'hash' as MaskingStrategy,
      is_active: true,
    },
  })

  useEffect(() => {
    if (isOpen) {
      reset(
        rule
          ? { table_name: rule.table_name, column_name: rule.column_name, strategy: rule.strategy, is_active: rule.is_active }
          : { table_name: '', column_name: '', strategy: 'hash', is_active: true }
      )
    }
  }, [isOpen, rule, reset])

  const strategy = watch('strategy')

  const saveMutation = useMutation({
    mutationFn: (values: any) =>
      isEdit
        ? maskingApi.updateRule(rule!.id, values)
        : maskingApi.createRule({ ...values, project_id: projectId, strategy_config: {} }),
    onSuccess: () => {
      toast.success(isEdit ? 'Rule updated' : 'Rule created')
      queryClient.invalidateQueries({ queryKey: ['masking', 'rules', projectId] })
      onClose()
    },
    onError: () => toast.error('Failed to save rule'),
  })

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={isEdit ? 'Edit masking rule' : 'Add masking rule'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit((v) => saveMutation.mutate(v))} isLoading={saveMutation.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form>
        <FormField label="Table name" required>
          <Input placeholder="customers" className="mono" {...register('table_name')} />
        </FormField>
        <FormField label="Column name" required>
          <Input placeholder="email" className="mono" {...register('column_name')} />
        </FormField>
        <FormField label="Masking strategy" required hint={STRATEGY_HINTS[strategy]}>
          <Controller
            control={control}
            name="strategy"
            render={({ field }) => (
              <Select {...field}>
                {STRATEGIES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace('_', ' ')}
                  </option>
                ))}
              </Select>
            )}
          />
        </FormField>
      </form>
    </Drawer>
  )
}
