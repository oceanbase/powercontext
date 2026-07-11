---
name: PowerMem
description: A precise, restrained developer surface for persistent AI memory infrastructure.
colors:
  ocean-blue: "#0083ff"
  ocean-blue-deep: "#0074e3"
  ocean-blue-soft: "#80c1ff"
  light-background: "#ffffff"
  light-surface: "#f7f9fc"
  light-ink: "#171a1f"
  light-body: "#303742"
  light-muted: "#596475"
  light-border: "#dde3eb"
  dark-background: "#0c0f13"
  dark-surface: "#11151a"
  dark-raised: "#151a20"
  dark-ink: "#f2f5f8"
  dark-muted: "#a3adba"
  dark-border: "#29313a"
typography:
  display:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "clamp(3.5rem, 5.3vw, 5rem)"
    fontWeight: 700
    lineHeight: 0.98
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "clamp(2.2rem, 4vw, 3.2rem)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.035em"
  title:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.2rem"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  body:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.82rem"
    fontWeight: 650
    lineHeight: 1.4
rounded:
  sm: "8px"
  md: "12px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  2xl: "48px"
  3xl: "64px"
components:
  button-primary:
    backgroundColor: "{colors.ocean-blue}"
    textColor: "{colors.light-background}"
    rounded: "{rounded.sm}"
    padding: "12px 24px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.ocean-blue-deep}"
    textColor: "{colors.light-background}"
    rounded: "{rounded.sm}"
    padding: "12px 24px"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.light-background}"
    textColor: "{colors.light-ink}"
    rounded: "{rounded.sm}"
    padding: "12px 24px"
    height: "44px"
  surface-panel:
    backgroundColor: "{colors.light-surface}"
    textColor: "{colors.light-ink}"
    rounded: "{rounded.md}"
    padding: "24px"
  navbar-action:
    backgroundColor: "{colors.light-surface}"
    textColor: "{colors.light-muted}"
    rounded: "{rounded.sm}"
    width: "44px"
    height: "44px"
---

# Design System: PowerMem

## Overview

**Creative North Star: "The Calibrated Memory Console"**

PowerMem should feel like a calibrated memory console: exact enough for engineers to trust, quiet enough for code and evidence to remain primary, and unmistakably connected to OceanBase. The interface uses open structure, disciplined rules, clear numeric alignment, and a small amount of OceanBase blue to guide attention.

The system is precise, confident, and restrained. It does not imitate an AI launch template and it does not decorate uncertainty. Light and dark modes are equal expressions of the same hierarchy rather than a primary theme with an automatic inversion.

**Key Characteristics:**

- Open layouts organized by alignment and 1px rules before containers.
- OceanBase blue used as a deliberate signal on actions, active states, and key data.
- Technical copy, code, and benchmark evidence carry more visual weight than ornament.
- Comfortable 4px-based spacing with tight groups and generous section separation.
- Responsive structure that reflows without horizontal overflow.

## Colors

The palette is neutral and high-contrast, with one exact blue signal and separate calibrated surfaces for light and dark environments.

### Primary

- **OceanBase Signal Blue:** Reserved for primary actions, active navigation, selected states, key metrics, and small directional details.
- **OceanBase Deep Blue:** Used for hover and pressed states on light backgrounds.
- **OceanBase Soft Blue:** Used for legible accent details on dark surfaces and rare low-emphasis blue treatments.

### Neutral

- **Clear White and Cloud Surface:** Default light canvas and subtle grouped surface.
- **Carbon Ink and Slate:** Primary and secondary light-mode text.
- **Hairline Gray:** Dividers, outlines, and structural boundaries in light mode.
- **Night Black, Night Surface, and Raised Carbon:** Three distinct dark-mode planes; they must not collapse into one black field.
- **Frost Ink and Steel:** Primary and secondary dark-mode text.
- **Night Hairline:** Structural boundaries in dark mode.

**The Signal, Not Atmosphere Rule.** OceanBase blue must occupy less than roughly 10% of a screen and must communicate action, state, or evidence. It is never ambient decoration.

**The Paired Theme Rule.** Every new surface must define an intentional light and dark treatment at the same time.

## Typography

**Display Font:** System UI sans-serif stack

**Body Font:** System UI sans-serif stack

**Character:** One family creates a direct, product-native voice. Hierarchy comes from decisive scale and weight changes, not from decorative font pairing.

### Hierarchy

- **Display:** Bold, fluid, and compact for page-level ideas; desktop display headings stay at or above a `-0.04em` tracking floor.
- **Headline:** Bold section titles with a clear scale step down from display type.
- **Title:** Semibold component and feature titles, usually one or two lines.
- **Body:** Regular technical prose with a comfortable line height and a 65–75 character reading measure.
- **Label:** Semibold metadata and controls in normal case; all-caps tracking is not the default.

**The Unbroken Thought Rule.** Desktop headings must not be forced into decorative multi-line fragments. Wrapping follows meaning and viewport constraints.

## Elevation

PowerMem is flat by default. Depth is expressed through tonal surface changes and 1px boundaries; persistent drop shadows are avoided. A small ambient shadow is permitted only for a floating menu or popover that must separate from content, never for ordinary cards.

**The Structural Depth Rule.** If alignment, spacing, a rule, or a tonal surface can establish grouping, adding a shadow is forbidden.

## Components

### Buttons

- **Shape:** Compact, gently rounded rectangle using the small radius and a minimum 44px target.
- **Primary:** OceanBase blue with white text and strong semibold labeling.
- **Hover / Focus:** Deepens one blue step on hover; focus uses a visible 2px OceanBase blue outline with 3px offset.
- **Secondary:** Transparent or raised neutral surface with a hairline border and ink-colored text.

### Cards / Containers

- **Corner Style:** Medium radius only when a boundary is semantically useful.
- **Background:** Uses the current theme's base, surface, or raised plane.
- **Shadow Strategy:** Flat at rest; no decorative shadow.
- **Border:** A single 1px hairline, or no container at all when rules and spacing are sufficient.
- **Internal Padding:** Usually large spacing, reduced to medium spacing on mobile.

### Navigation

- **Style:** A 64px translucent neutral bar with the official OceanBase wordmark, an optically balanced divider, and a strong PowerMem product label.
- **States:** Text navigation remains muted until hover or active. Utility actions share a compact grouped toolbar with 44px targets.
- **Mobile:** Product wordmark remains visible while secondary navigation moves behind the native menu control.

### Code Preview

- **Style:** A single outlined frame with a calm header and a theme-specific syntax palette.
- **Behavior:** Code remains readable at laptop widths; mobile lines wrap only where necessary and never force page overflow.

### Data Surface

- **Style:** Tabular numbers, open measurement bands, and quiet score indicators. Table headers use normal case rather than tracked uppercase dashboard labels.

## Do's and Don'ts

### Do:

- **Do** use OceanBase blue only for actions, active states, and technically meaningful emphasis.
- **Do** prefer open chapters, ledgers, and aligned bands over repeated containers.
- **Do** preserve WCAG AA contrast for body text, muted text, code, and controls in both themes.
- **Do** keep touch targets at least 44px and provide visible keyboard focus.
- **Do** use 8px and 12px radii consistently; ordinary surfaces must not exceed 16px.
- **Do** respect reduced motion and use short ease-out transitions only when they clarify state.

### Don't:

- **Don't** use neon-heavy AI landing pages.
- **Don't** use oversized headlines that fragment into many lines.
- **Don't** use decorative glow.
- **Don't** build generic SaaS card walls.
- **Don't** ship low-contrast syntax themes.
- **Don't** add visual effects that compete with technical content.
- **Don't** use gradient text, glassmorphism, colored side stripes, or repeated large rounded icons above headings.
- **Don't** add a card when spacing, alignment, and one hairline rule communicate the same grouping more clearly.
