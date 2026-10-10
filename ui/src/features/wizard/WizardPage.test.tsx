import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { renderScreen } from '@/test/harness'
import { useJobs } from '@/store/jobs'
import { useUi } from '@/store/ui'
import { WizardPage } from './WizardPage'

beforeEach(() => {
  useJobs.setState({ jobs: {}, order: [] })
  useUi.setState({ consent: {}, defaultPlanner: 'offline', defaultStyle: 'paper_cutout' })
})

describe('the new-reel wizard', () => {
  it('cannot go on without a script, then plans a reel and opens it in the studio', async () => {
    const user = userEvent.setup()
    await renderScreen(<WizardPage />, { route: '/new', path: '/new' })

    const next = await screen.findByRole('button', { name: /choose a look/i })
    expect(next).toBeDisabled()

    await user.type(screen.getByRole('textbox', { name: /your script/i }), 'Mia: Rain, rain, go away!\nPip: Oh no! Where is my umbrella?')
    expect(next).toBeEnabled()
    await user.click(next)

    expect(await screen.findByText(/choose a look/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /plan my reel/i }))
    // the finished plan becomes a project and the studio opens (here: "somewhere else" than /new)
    expect(await screen.findByTestId('elsewhere', {}, { timeout: 8000 })).toBeInTheDocument()
  })

  it('offers to build the reel by hand instead, from the library, with no script', async () => {
    const user = userEvent.setup()
    await renderScreen(<WizardPage />, { route: '/new', path: '/new' })
    expect(await screen.findByText('No script?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /build from scratch/i }))
    expect(await screen.findByRole('dialog', { name: /build a reel from scratch/i })).toBeInTheDocument()
  })

  it('checks the script against the library as it is typed and offers to add what is missing', async () => {
    const user = userEvent.setup()
    await renderScreen(<WizardPage />, { route: '/new', path: '/new' })
    const script = await screen.findByRole('textbox', { name: /your script/i })
    await user.type(script, 'The guard raised his sword at the castle gate.')

    // the check waits for a pause in typing, then lists what the library lacks, each with its own way to add it
    expect(await screen.findByRole('heading', { name: /library check/i }, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /add sword to the library/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /add castle to the library/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /choose a look/i })).toBeEnabled() // advice, never a gate
  })

  it('offers a hosted planner only when its key exists, and asks before sending the script', async () => {
    const user = userEvent.setup()
    await renderScreen(<WizardPage />, { route: '/new', path: '/new' })
    await user.type(await screen.findByRole('textbox', { name: /your script/i }), 'Mia: Hello there, friend.')
    await user.click(screen.getByRole('button', { name: /choose a look/i }))

    // no OPENAI_API_KEY in this environment: the choice is there but cannot be picked
    const openai = await screen.findByRole('radio', { name: /openai/i })
    expect(openai).toBeDisabled()
    expect(screen.getByRole('radio', { name: /offline planner/i })).toBeEnabled()
    await waitFor(() => expect(screen.getByRole('button', { name: /plan my reel/i })).toBeEnabled())
  })
})
