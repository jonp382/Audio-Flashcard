// Colour theme (the `theme` setting). Dark mode is the `dark` class on <html>; index.css
// re-points the palette under it. index.html starts with class="dark" (the default), so
// the window never flashes white while the settings load.
import type { Theme } from './api'

const media = window.matchMedia('(prefers-color-scheme: dark)')
let current: Theme = 'dark'

function paint() {
  const dark = current === 'dark' || (current === 'system' && media.matches)
  document.documentElement.classList.toggle('dark', dark)
}

media.addEventListener('change', () => current === 'system' && paint())

export function applyTheme(theme: Theme) {
  current = theme
  paint()
}
