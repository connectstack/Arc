import type { AudioResult } from '@/api/types'
import { audioKey, useAudio } from '@/store/audio'
import { useProject } from '@/store/project'
import { toast } from '@/components/ui'
import { plural } from '@/lib/format'

/** Put the caption windows the voice was timed to into the spec, so the timeline matches what is heard (one undo step). */
export function applyRetimed(result: AudioResult): void {
  if (result.retimed.length === 0) return
  const { edit } = useProject.getState()
  edit((d) => {
    for (const r of result.retimed) {
      const c = d.scenes[r.scene]?.captions[r.caption]
      if (!c) continue
      c.t0 = r.t0
      c.t1 = r.t1
    }
  })
  // the soundtrack was made from exactly these windows: the edit does not make it stale
  const { spec, id } = useProject.getState()
  const audio = useAudio.getState()
  if (spec && audio.result === result && audio.projectId === id) useAudio.setState({ specKey: audioKey(spec) })
  toast.success('Captions now follow the speech', `${plural(result.retimed.length, 'caption')} retimed`)
}
