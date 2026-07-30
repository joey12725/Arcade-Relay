# Arcade Y2K Frontend Asset Pack

This pack is a ready-to-integrate visual foundation for the Pegasus cabinet UI, Lifeline screen, and server-hosted web control plane described in the frontend brief.

## Contents

- `cabinet/skins/` — twelve 4:3 PNG backdrops, one each for Favorites, NES, SNES, N64, Genesis, Dreamcast, PSX, Arcade, GBA, Streaming, Steam, and Lifeline.
- `web/sprites/` — sixteen alpha PNG UI decorations: gel pills, chrome title plaque, panel frame, favorite star, online/offline/pending/success/error states, bubbles, glitter, marquee, and sparkles.
- `cabinet/mascots/` — an anime-style portrait mascot of the supplied tabby cat.
- `library-seeds/` — a metadata-only starter catalog with NES, PS1, Pokémon, and Celeste selections. It contains no game files.
- `source/` — original generated contact sheets plus the intermediate sprite crops. Retain these for re-cutting or alternate exports.

## Integration notes

Cabinet skin images are 724 × 543 (native 4:3). Use them as full-screen `cover` backdrops. Their central areas are intentionally quiet for the game cover, title, metadata, trailer panel, and controls. Keep UI text out of the artwork layer so it remains localizable and CRT-readable.

The Lifeline skin provides four empty status bays. Render the network-state label and its very large OK/BAD value inside each bay; do not rasterize diagnostic text into the backdrop.

The extracted web sprites have transparency around their visual shapes. They are intentionally unlabelled so status meaning always has a visible text label in the application. Stretch gel pills horizontally only; do not scale their height disproportionately.

The supplied tabby cat is included as an approved project-owned mascot. The image-generation service rejected the requested Mario and Pepsiman illustrations, so those branded mascot assets are not included. Use an approved licensed source image or a system logo/badge for those systems.

## Design constraints preserved

- Bright cobalt, red, lemon, white, plus restrained purple/aqua/lime support colors.
- 1999–2002 web/media-player-skin language: chrome, gel, bubbles, stars, marquee strips, and chunky beveled panels.
- No fake CRT scanlines, curvature, bloom, or chromatic aberration.
- No physical hardware enclosure, PCB/circuit, atom, product-render, or decorative character imagery.
- No readable text is baked into the generated skins or decorative sprites.

## Suggested asset mapping

| UI surface | Asset |
| --- | --- |
| Pegasus Favorites | `cabinet/skins/skin-favorites.png` |
| NES / SNES / N64 | matching `skin-nes`, `skin-snes`, `skin-n64` PNG |
| Genesis / Dreamcast / PSX / Arcade | matching cabinet skin PNG |
| GBA / Streaming / Steam | matching cabinet skin PNG |
| Lifeline | `cabinet/skins/skin-lifeline.png` |
| Favorite state | `web/sprites/favorite-star.png` |
| Shell connection state | `web/sprites/status-online.png`, `status-offline.png`, `status-pending.png` |
| Web titles and tab/button bases | `web/sprites/chrome-title-plaque.png` and `gel-*.png` |
| Decorative web separators | `bubble-cluster.png`, `glitter-divider.png`, `marquee-stripe.png`, `sparkle-cluster.png` |
