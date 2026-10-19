import { useSearchParams } from 'react-router'
import { PageHeader } from '../components/ui'
import { AccessTab } from './settings/AccessTab'
import { ScheduleTab } from './settings/ScheduleTab'
import { StaffTab } from './settings/StaffTab'
import { ThresholdsTab } from './settings/ThresholdsTab'
import { VisionTab } from './settings/VisionTab'
import { WebhooksTab } from './settings/WebhooksTab'

const TABS = {
  umbrales: ['Umbrales y alertas', ThresholdsTab],
  horarios: ['Horarios y turnos', ScheduleTab],
  personal: ['Perfil de personal', StaffTab],
  vision: ['Visión', VisionTab],
  accesos: ['Accesos', AccessTab],
  webhooks: ['Webhooks', WebhooksTab],
} as const

type TabKey = keyof typeof TABS

export function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as TabKey) in TABS ? (params.get('tab') as TabKey) : 'umbrales'
  const Comp = TABS[tab][1]
  return (
    <>
      <PageHeader title="Configuración" />
      <div className="mb-4 flex flex-wrap gap-1 border-b border-slate-800">
        {(Object.keys(TABS) as TabKey[]).map((k) => (
          <button
            key={k}
            onClick={() => setParams({ tab: k })}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${k === tab ? 'border-sky-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
          >
            {TABS[k][0]}
          </button>
        ))}
      </div>
      <Comp />
    </>
  )
}
