# PowerMem Website Redesign

## Goal

Redesign the complete PowerMem Docusaurus website with a restrained modern developer aesthetic inspired by Linear and Vercel. Preserve the existing Docusaurus architecture, content routes, localization, and deployment workflow while making the homepage, product pages, and documentation feel like one coherent product.

## Scope

The redesign covers:

- Homepage
- Features
- Benchmark
- Community
- Documentation pages
- Global navigation and footer
- English and Chinese locales
- Desktop and mobile layouts
- Light and dark themes

PowerMem SDK, server, dashboard, documentation content semantics, and deployment infrastructure are outside this redesign.

## Architecture

Keep `@docusaurus/preset-classic` and the existing React and TypeScript page structure. Use Docusaurus ColorMode as the single theme implementation. Remove the current forced-dark styling and update all affected pages and components to consume shared design tokens. Do not add a second theme system, compatibility aliases, or legacy CSS fallback paths.

## Visual Direction

- Use the blue embedded in the repository's OceanBase logo, `#0083FF`, as the primary brand color.
- Generate accessible light and dark color scales from that brand anchor.
- Use white and cool neutral grays in light mode.
- Use cool near-black surfaces rather than pure black in dark mode.
- Prefer fine borders, deliberate whitespace, and restrained shadows.
- Remove broad neon glows, multicolor gradients, excessive card framing, and decorative animations.
- Use one coherent typography, spacing, radius, border, and motion system across marketing and documentation pages.

## Global Navigation

The navigation remains a single horizontal bar on desktop:

- OceanBase / PowerMem identity on the left
- Docs, Features, Benchmark, and Community navigation
- Locale switch, color-mode switch, GitHub, and Discord actions on the right

On mobile, retain Docusaurus's native menu behavior and keep theme and locale controls reachable. Interactive elements must have hover, active, and `focus-visible` states.

## Homepage

Replace the current highly decorative composition with:

1. A compact hero presenting PowerMem's value proposition.
2. Primary actions for getting started and viewing GitHub.
3. A real, copyable Python usage example.
4. An open three-column core-capabilities section without oversized cards.
5. A concise explanation of the memory workflow.
6. A focused quick-start call to action.

Remove repeated English labels, inflated card height, and claims that do not add information. Stack the hero and code example vertically on small screens.

## Features

Present current capabilities as a concise capability matrix or open grid. Each item contains one title, a short explanation, and selected supporting details. Icons are secondary navigation cues rather than glowing decorations.

## Benchmark

Prioritize evidence and readability:

- Clear benchmark introduction
- Compact headline metrics
- Test methodology
- Readable comparison tables or result groups
- Links to supporting material where already available

Avoid visual effects that compete with the data.

## Community

Use one primary contribution path and a compact list of community destinations, including GitHub and Discord. Keep community actions easy to scan and remove redundant decorative containers.

## Documentation

Retain Docusaurus's three-region document structure:

- Left navigation with tighter rhythm and a clear current-page state
- Main article column around 760 pixels wide
- Right on-page table of contents with an active-section state

Provide coordinated light and dark styling for prose, headings, links, code blocks, tables, blockquotes, tabs, and admonitions. Preserve Docusaurus's native mobile sidebar drawer and hide the right table of contents at narrow widths.

## Theme Behavior

- Respect the operating system preference on first visit.
- Allow explicit light or dark selection through the navbar.
- Let Docusaurus persist the user's selection.
- Do not force dark mode or maintain a separate storage mechanism.
- Ensure both themes meet readable contrast levels for text, links, controls, borders, and syntax highlighting.

## Accessibility and Motion

- Preserve semantic heading order and keyboard navigation.
- Provide visible focus states.
- Maintain sufficient color contrast in both modes.
- Respect `prefers-reduced-motion`.
- Use animation only for subtle state transitions.

## Verification

Verification must include:

- TypeScript type checking
- A production Docusaurus build
- English and Chinese routes
- Homepage, Features, Benchmark, Community, and a representative documentation page
- Desktop and mobile viewport checks
- Light and dark mode checks
- Theme switching and persistence after refresh
- Browser screenshots of both themes for the homepage and documentation page
- Direct visual inspection of the final screenshots

## Delivery

Implementation remains in the independent `/tmp/powermem-redesign` clone. No upstream branch, commit, pull request, or deployment is created. The final handoff includes the temporary clone path, local preview URL, verification results, and screenshots.
