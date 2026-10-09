// Runs before first paint (an external file, so the page's Content-Security-Policy can stay strict): no flash of the wrong theme.
;(function () {
  try {
    var pref = localStorage.getItem('reel.theme') || 'system'
    var dark = pref === 'dark' || (pref === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
    var d = localStorage.getItem('reel.density')
    if (d) document.documentElement.setAttribute('data-density', d)
  } catch (e) {}
})()
