import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ApiProvider } from '@/api/context'
import type { Api } from '@/api/client'
import { createMockApi } from '@/api/mock'
import { VoiceConfirm } from '@/components/shell/VoiceConfirm'
import { Toaster, TooltipProvider } from '@/components/ui'

/** Mount a screen the way the app does (API, queries, router, tooltips, toasts), on the in-memory mock engine. */
export async function renderScreen(ui: ReactElement, { route = '/', path = '*', withKey = false, api: given }: { route?: string; path?: string; withKey?: boolean; api?: Api } = {}) {
  // the mock pretends an ElevenLabs key exists when the address says `?mock=eleven`
  window.history.replaceState({}, '', withKey ? '/?mock=eleven' : '/')
  const api = given ?? (await createMockApi())
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } })
  const utils = render(
    <ApiProvider api={api}>
      <QueryClientProvider client={queries}>
        <TooltipProvider>
          <MemoryRouter initialEntries={[route]}>
            <Routes>
              <Route path={path} element={ui} />
              <Route path="*" element={<div data-testid="elsewhere" />} />
            </Routes>
          </MemoryRouter>
          <Toaster />
          <VoiceConfirm />
        </TooltipProvider>
      </QueryClientProvider>
    </ApiProvider>,
  )
  return { api, queries, ...utils }
}
