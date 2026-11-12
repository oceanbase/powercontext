# Figures

Isometric figures drawn for the website, built once in Node and rendered as static SVG.

## `anatomy-kit/` (vendored)

The drawing kit, the audit and the 3D turning runtime from the
[Anatomy](https://github.com/wheresryan22/anatomy) skill (`kit/` at commit
`74a3a34ab10ff804d7e59a23324c6d50fa15c85a` of the copy it was taken from), under the MIT License in
[`anatomy-kit/LICENSE`](anatomy-kit/LICENSE). The files are unmodified copies of `iso-kit.mjs`,
`lathe.mjs`, `tube.mjs`, `audit.mjs`, `turn.mjs`, `turn-build.mjs` and `turn-audit.mjs`; they keep their
upstream license, are excluded from the Apache header check (`.licenserc.yaml`) and from `pnpm lint`.
Only `turn.mjs` ships to the browser; the rest runs in the build.

## `handoff-turntable/`

The home page hero figure: a rotary table (one Scope) carrying a card-file box (Memory), a spindle of
slips (Source journal), an in-tray (Review Inbox) and a handoff cartridge, turning 180° between the
station of the session that is ending and the station of the next session. An index arm over a spring
detent plunger reads four marks on the table, one per Handoff step in loop order: Work Contract,
Prepared Handoff, Acknowledgement, Task Outcome. The turn runs from Prepared Handoff (ending session)
to Acknowledgement (next session); the other two marks sit on the rim before and after that arc.

| File | Role |
| --- | --- |
| `scene.mjs` | Geometry: every static part and every part on the turning table, in world units. |
| `motion.mjs` | Pure motion model shared by the site component and the preview: opening, turns, detents. |
| `figure.css` | Card chrome and the `--hf-*` colour tokens, appended to the scoped kit styles. |
| `build.mjs` | Builds the scene and writes `generated/`; also writes preview pages and runs the audits. |
| `preview.js` | Glue for the standalone preview pages (not shipped). |
| `generated/` | Checked-in output read by `src/components/home/handoff-figure.tsx`. |

`generated/` is checked in, so `pnpm build` stays deterministic and offline and never runs the figure
build. After changing `scene.mjs`, `motion.mjs` or `figure.css`, regenerate and commit the output:

```bash
pnpm figures                                          # rewrite generated/
node figures/handoff-turntable/build.mjs --preview    # also write .preview/*.html (light and dark)
node figures/handoff-turntable/build.mjs --audit      # kit audit and orbit audit; must end "turn audit passed"
```

`generated/art.json` holds the static SVG (back layer, the table at rest, front layer) and is bundled
with the component; `generated/turn.json` holds the 3D runtime's data and is loaded on the client
together with `anatomy-kit/turn.mjs`, which only renders while the table turns. `generated/figure.css`
scopes the kit styles to `.hf` and follows the `dark` class on `<html>`.
