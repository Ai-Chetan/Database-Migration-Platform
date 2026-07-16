import React, { useEffect } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Drawer, Button, FormField, Input, Select } from '@/components/common'
import { schemaApi } from '@/api/schema'
import { MappingColumn, MappingKind } from '@/types'

const MAPPING_KINDS: MappingKind[] = ['direct', 'rename', 'constant', 'expression', 'transform', 'lookup', 'mask', 'synthesize']

interface Props {
  isOpen: boolean
  onClose: () => void
  column: MappingColumn | null
  tableId: string
}

export function ColumnMappingDrawer({ isOpen, onClose, column, tableId }: Props) {
  const queryClient = useQueryClient()
  const { register, handleSubmit, control, reset } = useForm({
    defaultValues: {
      target_column: '',
      target_type: '',
      mapping_kind: 'direct' as MappingKind,
      is_active: true,
    },
  })

  useEffect(() => {
    if (column) {
      reset({
        target_column: column.target_column,
        target_type: column.target_type,
        mapping_kind: column.mapping_kind,
        is_active: column.is_active,
      })
    }
  }, [column, reset])

  const saveMutation = useMutation({
    mutationFn: (values: any) => schemaApi.updateColumn(column!.id, values),
    onSuccess: () => {
      toast.success('Mapping updated')
      queryClient.invalidateQueries({ queryKey: ['schema', 'columns', tableId] })
      onClose()
    },
    onError: () => toast.error('Failed to update mapping'),
  })

  if (!column) return null

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title="Edit column mapping"
      subtitle={`${column.source_column} (${column.source_type})`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit((v) => saveMutation.mutate(v))} isLoading={saveMutation.isPending}>
            Save mapping
          </Button>
        </>
      }
    >
      <form>
        <FormField label="Target column" required>
          <Input {...register('target_column')} />
        </FormField>
        <FormField label="Target type" required>
          <Input {...register('target_type')} className="mono" />
        </FormField>
        <FormField label="Mapping kind" required>
          <Controller
            control={control}
            name="mapping_kind"
            render={({ field }) => (
              <Select {...field}>
                {MAPPING_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </Select>
            )}
          />
        </FormField>
        <div className="rounded border border-border bg-surface p-3 text-tiny text-text-secondary">
          Conversion safety: <span className="font-medium text-text-primary">{column.conversion_safety}</span>
          {column.conversion_safety !== 'safe' && (
            <p className="mt-1">
              This type conversion may {column.conversion_safety === 'lossy' ? 'lose precision' : 'require a custom transform'} — review before launching.
            </p>
          )}
        </div>
      </form>
    </Drawer>
  )
}
