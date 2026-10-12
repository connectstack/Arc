import type { Vec2 } from '@/api/types'
import { SliderField } from '@/components/ui'

/** What a slider covers, and how far the number beside it may go: a place on the stage is a fraction of the frame, 0 at the left or top edge and 1 at the right or bottom. */
export interface Axis {
  min: number
  max: number
  inputMin: number
  inputMax: number
}

/** Across the frame: a little past either edge, so someone can wait just out of shot; the number may go further. */
export const ACROSS: Axis = { min: -0.4, max: 1.4, inputMin: -1, inputMax: 2 }
/** Up and down the frame, for something that can stand, float or fall anywhere. */
export const DOWN: Axis = { min: -0.2, max: 1.2, inputMin: -0.2, inputMax: 1.4 }
/** Where a character's feet are: on the picture. */
export const FEET: Axis = { min: 0, max: 1, inputMin: 0, inputMax: 1 }
/** A camera pan: the fraction of the frame the picture slides. */
export const PAN: Axis = { min: -1, max: 1, inputMin: -1, inputMax: 1 }

const round3 = (v: number): number => Math.round(v * 1000) / 1000

function Row({ caption, children }: { caption: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[46px_minmax(0,1fr)] items-center gap-2">
      <span className="text-[12px] text-muted">{caption}</span>
      {children}
    </div>
  )
}

/**
 * Two sliders for a point on the stage, "across" and "down", each with its number beside it. `onChange` fires as a slider moves (give it the
 * live edit), `onCommit` when it is let go or a number is typed. The ticks mark the edges of the frame.
 */
export function PointSliders({ name, value, onChange, onCommit, across = ACROSS, down = DOWN, edges = true }: { name: string; value: Vec2; onChange: (v: Vec2) => void; onCommit?: () => void; across?: Axis; down?: Axis; edges?: boolean }) {
  const marks = edges ? [0, 1] : undefined
  return (
    <div className="flex flex-col gap-1">
      <Row caption="Across">
        <SliderField label={`${name} x`} valueLabel={`${name} x`} value={value[0]} min={across.min} max={across.max} inputMin={across.inputMin} inputMax={across.inputMax} step={0.01} marks={marks} onChange={(x) => onChange([round3(x), value[1]])} onCommit={onCommit} />
      </Row>
      <Row caption="Down">
        <SliderField label={`${name} y`} valueLabel={`${name} y`} value={value[1]} min={down.min} max={down.max} inputMin={down.inputMin} inputMax={down.inputMax} step={0.01} marks={marks} onChange={(y) => onChange([value[0], round3(y)])} onCommit={onCommit} />
      </Row>
    </div>
  )
}
