import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import './index.css'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ApiProvider } from '@/api/context'
import { App } from './App'
import { applyTheme, useUi } from '@/store/ui'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } },
})

applyTheme(useUi.getState().theme)
document.documentElement.setAttribute('data-density', useUi.getState().density)
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => useUi.getState().theme === 'system' && applyTheme('system'))

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ApiProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ApiProvider>
  </StrictMode>,
)
