import { useState } from 'react'
import { useCatalog } from '@/api/hooks'
import { Page, PageHeader } from '@/components/shell/PageHeader'
import { Segmented } from '@/components/ui'
import { titleCase } from '@/lib/format'
import { useUi } from '@/store/ui'
import { LibraryBrowser } from './LibraryBrowser'

/** Everything a script can ask for, with real thumbnails drawn by the same renderer the video uses. */
export function LibraryPage() {
  const catalog = useCatalog().data
  const fallback = useUi((s) => s.defaultStyle)
  const [style, setStyle] = useState(fallback)
  const styles = catalog?.styles.map((s) => ({ value: s.name, label: titleCase(s.name) })) ?? []

  return (
    <>
      <PageHeader
        title="Library"
        subtitle={catalog ? `${catalog.actions.length} actions · ${catalog.backgrounds.length} backgrounds · ${catalog.archetypes.length} characters · ${catalog.sfx.length} sounds` : 'Everything a script can ask for'}
        actions={styles.length > 0 ? <Segmented label="Preview in style" size="sm" value={style} onChange={setStyle} options={styles} /> : undefined}
      />
      <Page className="flex flex-col overflow-hidden">
        <div className="mx-auto flex min-h-0 w-full max-w-[1500px] flex-1 flex-col px-4 pb-0 pt-4 sm:px-6">
          <LibraryBrowser browse styleName={style} />
        </div>
      </Page>
    </>
  )
}
