# Dishify AR — setup

This replaces the image-tracking version. This one is simpler: no marker
image to choose, no compiling target files. Just three files and hosting.

## What changed
No more "point at a printed image" — the model now places wherever you tap,
on any flat surface. The angle/lighting sensitivity from the image-tracking
version doesn't apply here; this uses each phone's native surface detection
instead of pattern matching a printed image.

## Files (all three go in the same folder)
- `ar.html` — the viewer page. This is what your QR code should link to.
- `burger.glb` — the model, used for the on-page preview and Android AR.
- `burger.usdz` — the same model in Apple's AR format, used only on iPhone.

Both model files stay wherever `ar.html` is, referenced by relative path.
Don't rename them unless you also update the `src` / `ios-src` values inside
`ar.html`.

## Hosting
Must be served over `https://` (or `localhost` for testing). AR requires a
secure connection — phones won't allow camera/motion access on plain
`http://`. Any static host works: Firebase Hosting, GitHub Pages, Netlify,
Vercel, etc.

## What happens on each platform
- **Android (Chrome):** taps "View in Your Space" → WebXR opens inline, or
  hands off to Google's Scene Viewer app if needed → tap a surface to place
  the burger, walk around it.
- **iPhone (Safari):** taps the same button → opens Apple's native AR Quick
  Look using `burger.usdz` → same tap-to-place behavior, Apple's own AR
  engine.
- **Desktop / no AR support:** still shows the model inline, draggable and
  auto-rotating — the AR button just won't appear.

## The QR code
`dishify_qr_code.png` from earlier still works for this — it points at
`ar.html`, which is what you want. Once you know the real hosted address,
regenerate it with that URL (any QR generator works, or ask me).

## Testing
Open `ar.html` on your phone directly first, skip the QR code, to confirm
the model loads and AR launches before printing anything.
