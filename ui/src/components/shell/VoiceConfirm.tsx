import { ShieldAlert } from 'lucide-react'
import { Button, Dialog } from '@/components/ui'
import { useUi } from '@/store/ui'
import { useVoice } from '@/store/voice'

/** The one place that asks before an online voice is used: names the service and what it will bill. Nothing paid runs without a yes. */
export function VoiceConfirm() {
  const asking = useVoice((s) => s.asking)
  const answer = useVoice((s) => s.answer)
  const setConsent = useUi((s) => s.setConsent)
  const audition = asking?.what.startsWith('audition')
  return (
    <Dialog
      open={!!asking}
      onOpenChange={(o) => !o && answer(false)}
      size="sm"
      title={`Send text to ${asking?.host || 'an online service'}?`}
      description="This is an online voice: it is billed by the provider."
      footer={
        <>
          <Button onClick={() => answer(false)}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              if (asking?.host) setConsent(asking.host, true)
              answer(true)
            }}
          >
            <ShieldAlert className="size-4" /> Yes, {asking?.what}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2 text-[13px]">
        <p>
          The spoken lines (caption text only) are sent to <b>{asking?.host}</b> with your API key, which stays on the server. <b className="tabular">{asking?.chars.toLocaleString()} characters</b> will be billed
          {audition ? ' for this audition' : ' (finished lines already in the cache are free)'}.
        </p>
        <p className="text-muted">Nothing paid ever runs without this click.</p>
      </div>
    </Dialog>
  )
}
