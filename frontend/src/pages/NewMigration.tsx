import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ArrowLeft, ArrowRight, Rocket } from 'lucide-react'
import { PageHeader, Button } from '@/components/common'
import { WizardStepper, WizardStepDef } from '@/components/features/wizard/WizardStepper'
import { WizardState, initialWizardState } from '@/components/features/wizard/wizardState'
import { Step1SelectConnections } from '@/components/features/wizard/steps/Step1SelectConnections'
import { Step2Assessment } from '@/components/features/wizard/steps/Step2Assessment'
import { Step3SchemaMapping } from '@/components/features/wizard/steps/Step3SchemaMapping'
import { Step4Simulation } from '@/components/features/wizard/steps/Step4Simulation'
import { Step5Review } from '@/components/features/wizard/steps/Step5Review'
import { jobsApi } from '@/api/jobs'

const STEPS: WizardStepDef[] = [
  { key: 'connections', label: 'Connections' },
  { key: 'assessment', label: 'Assessment' },
  { key: 'schema', label: 'Schema Mapping' },
  { key: 'simulation', label: 'Simulation' },
  { key: 'review', label: 'Review & Launch' },
]

export default function NewMigration() {
  const navigate = useNavigate()
  const [stepIndex, setStepIndex] = useState(0)
  const [state, setState] = useState<WizardState>(initialWizardState)

  const update = (patch: Partial<WizardState>) => setState((s) => ({ ...s, ...patch }))

  const launchMutation = useMutation({
    mutationFn: async () => {
      const job = await jobsApi.create({
        name: state.jobName,
        source_connection_id: state.sourceConnectionId,
        target_connection_id: state.targetConnectionId,
        mapping_project_id: state.projectId,
        worker_count: state.workerCount,
      })
      await jobsApi.start(job.id)
      return job
    },
    onSuccess: (job) => {
      toast.success('Migration launched')
      navigate(`/app/jobs/${job.id}`)
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || 'Failed to launch migration'),
  })

  const canGoNext = (() => {
    switch (STEPS[stepIndex].key) {
      case 'connections':
        return !!state.jobName && !!state.sourceConnectionId && !!state.targetConnectionId
      case 'assessment':
        return !!state.assessment && state.assessment.blocking_issues.length === 0
      case 'schema':
        return state.tablesAutoMapped
      case 'simulation':
        return !!state.sweepResult
      default:
        return true
    }
  })()

  const isLastStep = stepIndex === STEPS.length - 1

  return (
    <div>
      <PageHeader title="New Migration" description="Set up and launch a new database migration in 5 steps." />

      <WizardStepper steps={STEPS} activeIndex={stepIndex} />

      <div className="min-h-[360px]">
        {STEPS[stepIndex].key === 'connections' && <Step1SelectConnections state={state} update={update} />}
        {STEPS[stepIndex].key === 'assessment' && <Step2Assessment state={state} update={update} />}
        {STEPS[stepIndex].key === 'schema' && <Step3SchemaMapping state={state} update={update} />}
        {STEPS[stepIndex].key === 'simulation' && <Step4Simulation state={state} update={update} />}
        {STEPS[stepIndex].key === 'review' && <Step5Review state={state} />}
      </div>

      <div className="mt-8 flex max-w-2xl items-center justify-between border-t border-border pt-6">
        <Button
          variant="secondary"
          leftIcon={<ArrowLeft className="h-4 w-4" />}
          disabled={stepIndex === 0}
          onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
        >
          Back
        </Button>

        {isLastStep ? (
          <Button
            leftIcon={<Rocket className="h-4 w-4" />}
            isLoading={launchMutation.isPending}
            onClick={() => launchMutation.mutate()}
          >
            Launch migration
          </Button>
        ) : (
          <Button
            rightIcon={<ArrowRight className="h-4 w-4" />}
            disabled={!canGoNext}
            onClick={() => setStepIndex((i) => Math.min(STEPS.length - 1, i + 1))}
          >
            Continue
          </Button>
        )}
      </div>
    </div>
  )
}
