// What the asset screens' tests share: the mock engine with a small built-in library, files to drop, and assets of "your own".
import type { Api } from '@/api/client'
import { createMockApi } from '@/api/mock'
import type { AssetFields, AssetInfo } from '@/api/types'

/** The mock engine with a few built-in library assets (car, cake, cat, beach ...), as `?library` asks for. */
export async function libraryApi(): Promise<Api> {
  window.history.replaceState({}, '', '/?library')
  return createMockApi()
}

/** A small SVG with two colours marked recolourable (`data-role`). */
export const svgFile = (name = 'kite.svg'): File =>
  new File(['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><g data-role="body"><path d="M0 0h10v10z"/></g><g data-role="accent"><circle r="2"/></g></svg>'], name, { type: 'image/svg+xml' })

/** One asset of your own, added the way the dialog adds it (a draft, then kept). */
export async function addUserAsset(api: Api, name: string, fields: Partial<AssetFields> = {}): Promise<AssetInfo> {
  const draft = await api.createAssetDraft(svgFile(`${name}.svg`), { kind: fields.kind })
  return api.commitAssetDraft(draft.id, { name, kind: 'object', summary: '', tags: [], facing: 'right', ...fields })
}
