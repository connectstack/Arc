// Evaluate a JS file (or inline expression) in a page of the demo app and print the result: for finding the right selectors.
import { readFileSync } from 'node:fs'
import { launch, sleep } from './cdp.mjs'
const [url, ...rest] = process.argv.slice(2)
const expr = rest.join(' ')
const page = await launch({ width: 1440, height: 900, dpr: 1 })
await page.goto(url, "document.querySelector('h1,h2') !== null")
await sleep(2500)
const src = expr.startsWith('@') ? readFileSync(expr.slice(1), 'utf8') : expr
console.log(JSON.stringify(await page.eval(src), null, 1))
await page.close()
