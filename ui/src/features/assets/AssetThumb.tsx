import { useState } from 'react'
import { useApi } from '@/api/context'
import type { AssetInfo } from '@/api/types'
import { cn } from '@/lib/cn'

/** A library asset drawn by the engine as a scene would show it (any of the three styles; `trueScale` puts a person beside it). */
export function AssetThumb({ asset, style = 'flat_vector', timeOfDay, trueScale, className, alt }: { asset: Pick<AssetInfo, 'name' | 'version'>; style?: string; timeOfDay?: string; trueScale?: boolean; className?: string; alt?: string }) {
  const api = useApi()
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div role="img" aria-label={alt ?? asset.name} className={cn('grid aspect-[9/16] w-full place-items-center rounded-card bg-raised p-2 text-center text-[11px] text-faint', className)}>
        {asset.name.replace(/_/g, ' ')}
      </div>
    )
  }
  return <img src={api.assetThumbUrl(asset, style, timeOfDay, trueScale)} alt={alt ?? asset.name} loading="lazy" draggable={false} onError={() => setFailed(true)} className={cn('aspect-[9/16] w-full rounded-card bg-raised object-cover', className)} />
}
