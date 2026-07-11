# Interactive Hero Memory Demo Design

## Goal

Turn the homepage hero code sample into a product-specific interactive memory demo that shows how PowerMem converts application code into durable memory.

## Interaction

The panel starts ready with a short Python example and a Run button. Clicking Run moves through three deterministic states: running, extracting, and complete. The active code area receives a restrained OceanBase-blue progress treatment. Completion reveals a compact structured result containing the extracted preference, user scope, and stored state.

The animation runs only after an explicit click. The button is disabled while running and can replay after completion. An aria-live region announces state changes. Reduced-motion users receive the final result without staged animation.

## Visual Design

The panel is a technical canvas rather than a simulated macOS window. Its header contains a Python label, status indicator, and Run control. The code and result share one surface separated by a fine rule. OceanBase blue indicates execution; green is reserved for successful persistence.

Light mode uses a cool neutral editor. Dark mode uses a high-contrast near-black editor inspired by Trigger.dev, without glow, glass, particles, or decorative terminal noise.

## Architecture

Keep the demo inside the existing Hero component because it has no reuse case. Use React state and one effect with cleaned-up timers. Keep Docusaurus ColorMode as the only theme source and Prism as the syntax renderer. Do not add a new animation or state dependency.

## Responsive Behavior

At desktop widths the demo remains beside the headline. Below 860px it stacks beneath the copy. Code never overflows its panel; the result becomes a two-column compact grid and collapses to one column on narrow phones.

## Verification

Run TypeScript and Docusaurus production builds. Verify idle, running, completed, and replay states in a real browser. Capture light and dark desktop screenshots and one mobile screenshot. Confirm keyboard activation, disabled state, aria-live output, and reduced-motion CSS.

