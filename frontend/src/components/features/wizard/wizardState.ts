import { Assessment, SimulationSweepResult } from '@/types'

export interface WizardState {
  jobName: string
  sourceConnectionId: string
  targetConnectionId: string
  assessment: Assessment | null
  projectId: string | null
  tablesDiscovered: boolean
  tablesAutoMapped: boolean
  sweepResult: SimulationSweepResult | null
  workerCount: number
}

export const initialWizardState: WizardState = {
  jobName: '',
  sourceConnectionId: '',
  targetConnectionId: '',
  assessment: null,
  projectId: null,
  tablesDiscovered: false,
  tablesAutoMapped: false,
  sweepResult: null,
  workerCount: 4,
}
