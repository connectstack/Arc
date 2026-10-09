import { Navigate, Route, Routes } from 'react-router-dom'
import { Shell } from '@/components/shell/Shell'
import { TooltipProvider, Toaster } from '@/components/ui'
import { AudioPage } from '@/features/audio/AudioPage'
import { LibraryPage } from '@/features/library/LibraryPage'
import { HealthPage } from '@/features/health/HealthPage'
import { ProjectsPage } from '@/features/projects/ProjectsPage'
import { WizardPage } from '@/features/wizard/WizardPage'
import { ProjectLayout } from '@/features/studio/ProjectLayout'
import { StudioPage } from '@/features/studio/StudioPage'

export function App() {
  return (
    <TooltipProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<ProjectsPage />} />
          <Route path="new" element={<WizardPage />} />
          <Route path="p/:id" element={<ProjectLayout />}>
            <Route index element={<StudioPage />} />
            <Route path="audio" element={<AudioPage />} />
          </Route>
          <Route path="library" element={<LibraryPage />} />
          <Route path="health" element={<HealthPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <Toaster />
    </TooltipProvider>
  )
}
