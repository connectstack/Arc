// A small asset library for tests of the studio: the objects the real catalog would list (their size, shape, anchor and recolourable
// parts), and the mock engine with them in its catalog. Not used by the app.
import type { Api } from '@/api/client'
import type { Catalog, CatalogEntry } from '@/api/types'

export const OBJECT_FIXTURES: CatalogEntry[] = [
  {
    name: 'car',
    summary: 'A small red hatchback, side view',
    tags: ['car', 'auto', 'vehicle', 'गाड़ी', 'कार'],
    height: 300,
    aspect: 2,
    anchor: [0.5, 1],
    size: 0.52,
    library: 'builtin',
    roles: ['body', 'body_dark', 'window'],
    palette: { body: '#d9342b', body_dark: '#a82620', window: '#bfe3f2' },
  },
  {
    name: 'tree',
    summary: 'A round green tree with a brown trunk',
    tags: ['tree', 'पेड़', 'ped'],
    height: 620,
    aspect: 0.8,
    anchor: [0.5, 1],
    size: 1.08,
    library: 'builtin',
    roles: ['leaves', 'trunk'],
    palette: { leaves: '#3a8a3a', trunk: '#7a5230' },
  },
  {
    name: 'cake',
    summary: 'A birthday cake with candles',
    tags: ['cake', 'केक', 'birthday'],
    height: 140,
    aspect: 1.1,
    anchor: [0.5, 1],
    size: 0.24,
    library: 'builtin',
    roles: ['icing'],
    palette: { icing: '#f4a3c4' },
  },
  {
    name: 'billboard',
    summary: 'A very wide sign on two posts',
    tags: ['billboard', 'sign', 'hoarding'],
    height: 400,
    aspect: 6,
    anchor: [0.5, 1],
    size: 0.7,
    library: 'builtin',
    roles: [],
    palette: {},
  },
  {
    name: 'balloon',
    summary: 'A party balloon that floats',
    tags: ['balloon', 'गुब्बारा'],
    height: 220,
    aspect: 0.75,
    anchor: [0.5, 0.5],
    size: 0.38,
    library: 'user',
    roles: ['body'],
    palette: { body: '#e4572e' },
  },
]

/** The mock engine with the fixtures as the catalog's objects (change `api.catalog` only: the rest stays the mock). */
export function withObjects(api: Api, objects: CatalogEntry[] = OBJECT_FIXTURES): Api {
  const catalog = api.catalog.bind(api)
  api.catalog = async (): Promise<Catalog> => ({ ...(await catalog()), objects: structuredClone(objects) })
  return api
}

/** The fixtures as a catalog, to hand straight to the functions that take one. */
export async function catalogWithObjects(api: Api): Promise<Catalog> {
  return withObjects(api).catalog()
}
