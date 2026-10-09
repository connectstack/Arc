import { current, isDraft, type Draft } from 'immer'

/** A deep copy that also works on an immer draft (a Proxy, which `structuredClone` refuses). */
export function plainCopy<T>(v: T): T {
  return structuredClone(isDraft(v) ? (current(v as Draft<T>) as T) : v)
}
