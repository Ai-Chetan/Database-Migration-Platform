import React, { useEffect } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Drawer, Button, FormField, Input, Select } from '@/components/common'
import { connectionsApi } from '@/api/connections'
import { Connection, Engine } from '@/types'
import { ENGINE_LABELS } from '@/utils/meta'

const ENGINE_OPTIONS: Engine[] = ['mysql', 'postgresql', 'sqlite', 's3', 'azure', 'gcs', 'kafka', 'rest_api', 'file']

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  engine: z.enum(ENGINE_OPTIONS as [Engine, ...Engine[]]),
  host: z.string().min(1, 'Host is required'),
  port: z.coerce.number().int().min(1, 'Enter a valid port').max(65535),
  database: z.string().min(1, 'Database name is required'),
  username: z.string().min(1, 'Username is required'),
  password: z.string().optional(),
})

type FormValues = z.infer<typeof schema>

const DEFAULT_PORTS: Partial<Record<Engine, number>> = {
  mysql: 3306,
  postgresql: 5432,
  kafka: 9092,
}

interface Props {
  isOpen: boolean
  onClose: () => void
  connection?: Connection | null
}

export function ConnectionFormDrawer({ isOpen, onClose, connection }: Props) {
  const isEdit = !!connection
  const queryClient = useQueryClient()

  const {
    register,
    handleSubmit,
    control,
    watch,
    reset,
    setValue,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: '',
      engine: 'postgresql',
      host: '',
      port: 5432,
      database: '',
      username: '',
      password: '',
    },
  })

  const engine = watch('engine')

  useEffect(() => {
    if (isOpen) {
      if (connection) {
        reset({
          name: connection.name,
          engine: connection.engine,
          host: connection.host,
          port: connection.port,
          database: connection.database,
          username: '',
          password: '',
        })
      } else {
        reset({ name: '', engine: 'postgresql', host: '', port: 5432, database: '', username: '', password: '' })
      }
    }
  }, [isOpen, connection, reset])

  const onEngineChange = (value: Engine) => {
    setValue('engine', value)
    if (DEFAULT_PORTS[value]) setValue('port', DEFAULT_PORTS[value] as number)
  }

  const saveMutation = useMutation({
    mutationFn: (values: FormValues) =>
      isEdit ? connectionsApi.update(connection!.id, values) : connectionsApi.create(values),
    onSuccess: () => {
      toast.success(isEdit ? 'Connection updated' : 'Connection created')
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      onClose()
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to save connection')
    },
  })

  const onSubmit = (values: FormValues) => saveMutation.mutate(values)

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={isEdit ? 'Edit connection' : 'Add connection'}
      subtitle={isEdit ? connection?.name : 'Connect a source or target data store'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit(onSubmit)} isLoading={saveMutation.isPending}>
            {isEdit ? 'Save changes' : 'Create connection'}
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <FormField label="Connection name" error={errors.name?.message} required>
          <Input placeholder="e.g. Production MySQL" hasError={!!errors.name} {...register('name')} />
        </FormField>

        <FormField label="Engine" error={errors.engine?.message} required>
          <Controller
            control={control}
            name="engine"
            render={({ field }) => (
              <Select
                value={field.value}
                onChange={(e) => onEngineChange(e.target.value as Engine)}
                hasError={!!errors.engine}
              >
                {ENGINE_OPTIONS.map((e) => (
                  <option key={e} value={e}>
                    {ENGINE_LABELS[e]}
                  </option>
                ))}
              </Select>
            )}
          />
        </FormField>

        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <FormField label="Host" error={errors.host?.message} required>
              <Input placeholder="db.internal.company.com" hasError={!!errors.host} {...register('host')} />
            </FormField>
          </div>
          <FormField label="Port" error={errors.port?.message} required>
            <Input type="number" hasError={!!errors.port} {...register('port')} />
          </FormField>
        </div>

        <FormField label="Database name" error={errors.database?.message} required>
          <Input placeholder="production_db" hasError={!!errors.database} {...register('database')} />
        </FormField>

        <FormField label="Username" error={errors.username?.message} required>
          <Input placeholder="migration_user" hasError={!!errors.username} {...register('username')} />
        </FormField>

        <FormField
          label="Password"
          hint={isEdit ? 'Leave blank to keep the existing password' : undefined}
          error={errors.password?.message}
        >
          <Input type="password" placeholder="••••••••" hasError={!!errors.password} {...register('password')} />
        </FormField>

        <p className="text-tiny text-text-tertiary">
          Engine selected: <span className="font-medium text-text-secondary">{ENGINE_LABELS[engine]}</span>. Credentials are encrypted at rest via the platform's secrets manager.
        </p>
      </form>
    </Drawer>
  )
}
