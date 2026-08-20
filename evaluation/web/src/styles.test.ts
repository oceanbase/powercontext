import { describe, expect, it } from "vitest";

import styles from "./styles.css?raw";

function declarations(selector: string, from = 0): string {
  const start = styles.indexOf(`${selector} {`, from);
  expect(start, `missing CSS rule for ${selector}`).toBeGreaterThanOrEqual(0);
  const end = styles.indexOf("}", start);
  expect(end, `unterminated CSS rule for ${selector}`).toBeGreaterThan(start);
  return styles.slice(start, end);
}

function minimumHeight(selector: string): number {
  const match = declarations(selector).match(/min-height:\s*(\d+)px/);
  expect(match, `${selector} must declare a pixel min-height`).not.toBeNull();
  return Number(match?.[1]);
}

describe("interactive target sizing", () => {
  it.each([".filter-field select", ".text-button", ".task-link"])(
    "keeps %s at least 44px high",
    (selector) => {
      expect(minimumHeight(selector)).toBeGreaterThanOrEqual(44);
    },
  );

  it.each([".text-button", ".task-link"])("uses inline-flex alignment for %s", (selector) => {
    const rule = declarations(selector);
    expect(rule).toMatch(/display:\s*inline-flex/);
    expect(rule).toMatch(/align-items:\s*center/);
  });
});

describe("report batch list layout", () => {
  it("keeps the report action on one line while long batch metadata owns the flexible column", () => {
    const row = declarations(".report-index li");
    expect(row).toMatch(/display:\s*grid/);
    expect(row).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/);

    const content = declarations(".report-index li > div");
    expect(content).toMatch(/min-width:\s*0/);

    const action = declarations(".report-index \.primary-link");
    expect(action).toMatch(/white-space:\s*nowrap/);
  });

  it("moves the whole report action below the metadata at narrow widths", () => {
    const narrowLayout = styles.indexOf("@media (max-width: 620px)");
    expect(narrowLayout).toBeGreaterThanOrEqual(0);
    expect(declarations(".report-index li", narrowLayout)).toMatch(
      /grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    );
  });
});
