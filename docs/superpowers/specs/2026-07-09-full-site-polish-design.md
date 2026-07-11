# PowerMem Full-Site Polish Design

## Goal

Remove remaining AI-template visual patterns and deliver a coherent, production-grade PowerMem marketing and documentation website in light and dark themes.

## Design Direction

Use a restrained OceanBase-derived system: true white and cool neutral surfaces in light mode, near-black and deep blue-neutral surfaces in dark mode, and #0083FF as the only brand accent. Typography is precise and compact, with balanced headings, readable line lengths, and no decorative gradients, glows, oversized type, or repeated card scaffolding.

## Global System

Consolidate shared colors, spacing, type, radii, code surfaces, focus states, and responsive rules in custom.css. Use system UI typography rather than an unverified Inter dependency. Keep display letter spacing no tighter than -0.04em. Remove thick side accents and wide decorative shadows. Preserve native Docusaurus theme switching.

## Static Ocean Pair Code

Remove all Run, status, timer, and output UI. The Hero displays one concise static Python sample. Light mode uses a cool-white editor with blue, violet, amber, teal, and slate syntax roles. Dark mode uses a deep-ocean editor with cyan, lavender, apricot, teal, and desaturated-blue roles. Documentation code blocks receive the same Ocean Pair treatment.

## Homepage

- Hero remains a two-column composition with a two-line headline and static code sample.
- Core capabilities become an open hierarchy: three primary capabilities and two quieter secondary capabilities, without identical boxed cards.
- Performance proof becomes a compact comparison band rather than nested cards.
- Quick Start becomes one installation command plus one representative example and direct documentation action.

## Features

Keep the page as an upcoming-capabilities roadmap, but use a correct three-part row: icon, title/description, and compact details. Details wrap horizontally rather than forming a narrow vertical column. Remove empty placeholder cards and decorative icon color variants.

## Benchmark

Reduce hero height, keep the benchmark context close to the title, and use a compact metric strip. Improve table typography, numeric alignment, hierarchy, and mobile overflow. Preserve all existing numbers and claims.

## Community

Use three equal channels in one row on desktop and one column on mobile. Remove the empty grid quadrant. Keep contribution copy in a separate open section with a clear GitHub action.

## Documentation

Keep the native three-column structure. Improve article headings, paragraph measure, sidebar rhythm, table-of-contents contrast, code blocks, tables, tabs, and callouts. Replace thick alert side borders with a full subtle border and tinted surface. Preserve mobile drawers.

## Cleanup

Delete unused visual components only after confirming they have no imports: animated backgrounds, mouse glow, grid background, superseded marketing sections, and old ValueProps variants. Remove unused CSS and icon variants tied only to deleted components. Do not change SDK, server, dashboard, content routes, or deployment.

## Verification

Run Impeccable detector, TypeScript, and both-locale production build. Verify home, Features, Benchmark, Community, and a representative document in English and Chinese, light and dark, at 1440×1000 and 390×844. Inspect screenshots directly and fix overflow, clipping, weak contrast, awkward wrapping, empty grid cells, and inconsistent spacing.

