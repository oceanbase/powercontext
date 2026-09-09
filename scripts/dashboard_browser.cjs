/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// Verify a running Server against its own API responses without installing fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function checkReadingBounds(page, route) {
  const overflow = await page.evaluate(() => {
    const issues = [];
    const walker = document.createTreeWalker(document.querySelector('main'), NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const element = node.parentElement;
      if (!node.textContent.trim() || element.closest('script, style, svg, [aria-hidden="true"]')) continue;
      const style = getComputedStyle(element);
      if (style.visibility !== 'visible' || !element.getClientRects().length) continue;
      // Tabler tables intentionally scroll within their own accessible region.
      const scroll = element.closest('.table-responsive');
      const card = element.closest('.card');
      const bounds = scroll ? { left: 0, right: scroll.scrollWidth } : card?.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(node);
      const clip = style.textOverflow === 'ellipsis' && ['hidden', 'clip'].includes(style.overflowX) ? element.getBoundingClientRect() : null;
      for (const rect of range.getClientRects()) {
        const visibleLeft = clip ? Math.max(rect.left, clip.left) : rect.left;
        const visibleRight = clip ? Math.min(rect.right, clip.right) : rect.right;
        const left = scroll ? visibleLeft - scroll.getBoundingClientRect().left + scroll.scrollLeft : visibleLeft;
        const right = left + visibleRight - visibleLeft;
        if (bounds && (left < bounds.left - 2 || right > bounds.right + 2)) issues.push(node.textContent.slice(0, 80));
        if (!scroll && (left < -2 || right > innerWidth + 2)) issues.push(node.textContent.slice(0, 80));
      }
    }
    return [...new Set(issues)];
  });
  assert.deepEqual(overflow, [], `Reading content exceeds its page or card: ${route}`);
}

async function checkChartTooltip(page, chart) {
  const legend = chart.locator('.apexcharts-legend-series').first();
  await legend.click();
  await legend.click();
  for (const bar of await chart.locator('.apexcharts-bar-area').all()) {
    const bounds = await bar.boundingBox();
    if (!bounds || bounds.width < 2 || bounds.height < 2) continue;
    await bar.hover();
    const tooltip = chart.locator('.apexcharts-tooltip.apexcharts-active');
    await tooltip.waitFor();
    await page.waitForFunction(() => {
      const reading = document.querySelector('.apexcharts-tooltip.apexcharts-active')?.getBoundingClientRect();
      return reading && reading.left >= 0 && reading.right <= innerWidth + 1;
    });
    return;
  }
}

async function clickNavigation(page, selector) {
  const target = page.locator(selector);
  const toggle = page.locator('.navbar-toggler');
  if (await toggle.isVisible() && !await page.locator('#dashboard-navigation').isVisible()) await toggle.click();
  await target.click();
}

async function chooseScope(page, scope) {
  await clickNavigation(page, '.scope-picker .ts-control');
  await page.locator(`.scope-picker [role="option"][data-value="${scope}"]`).click();
}

async function checkScopePicker(page, base, parent, child) {
  const viewport = page.viewportSize();
  for (const width of [390, 1536]) {
    for (const lang of ['zh', 'en']) {
      for (const theme of ['light', 'dark']) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(`${base}/dashboard/home?scope=${parent.scope_id}&period=30d&lang=${lang}&theme=${theme}`);
        await clickNavigation(page, '.scope-picker .ts-control');
        const picker = page.locator('.scope-picker');
        const input = picker.locator('.dropdown-input');
        const location = page.url();
        await input.fill('no-matching-scope-query');
        await picker.locator('.no-results').waitFor();
        assert.equal(page.url(), location, 'Filtering scopes navigated away');
        await input.press('Escape');
        assert.equal(await picker.locator('.ts-control').getAttribute('aria-expanded'), 'false');
        await clickNavigation(page, '.scope-picker .ts-control');
        await input.fill(child.title);
        await picker.locator(`[role="option"].active[data-value="${child.scope_id}"]`).waitFor();
        await input.press('Enter');
        await page.waitForURL(url => url.searchParams.get('scope') === child.scope_id);
        await chooseScope(page, parent.scope_id);
        await page.waitForURL(url => url.searchParams.get('scope') === parent.scope_id);
        assert.equal(new URL(page.url()).searchParams.get('period'), '30d');
      }
    }
  }
  await page.setViewportSize(viewport);
}

async function changePreference(page, key, value) {
  const menu = page.locator('.display-preferences');
  if (!await menu.isVisible()) {
    await page.locator('.navbar-toggler').click();
    await page.locator('#dashboard-navigation.show').waitFor();
  }
  const toggle = menu.locator('.dropdown-toggle');
  await toggle.scrollIntoViewIfNeeded();
  const before = await toggle.boundingBox();
  await toggle.click();
  assert.deepEqual(await toggle.boundingBox(), before, 'Opening display settings moved its button');
  const bounds = await menu.locator('.dropdown-menu').boundingBox();
  assert(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1 && bounds.y + bounds.height <= page.viewportSize().height + 1, 'Display settings exceed the viewport');
  assert(await menu.locator('.dropdown-item').evaluateAll(items => items.every(element => {
    const rect = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  })), 'Display options are clipped by navigation scrolling');
  await menu.locator(`.dropdown-item[href*="${key}=${value}"]`).click();
  await page.waitForURL(url => url.searchParams.get(key) === value);
  const selectedTheme = page.locator('.display-preferences [data-theme-value][aria-current="true"]');
  assert.equal(await selectedTheme.getAttribute('data-theme-value'), await page.evaluate(() => document.documentElement.dataset.bsTheme || 'light'));
}

async function displayLayout(page) {
  const navigation = page.locator('.navbar-nav');
  const expand = await navigation.count() && !await navigation.isVisible();
  if (expand) {
    await page.locator('.navbar-toggler').click();
    await page.locator('#dashboard-navigation.show').waitFor();
  }
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => scrollTo(0, 0));
  const layout = await page.evaluate(() => {
    const rects = selector => [...document.querySelectorAll(selector)].map(element => {
      const rect = element.getBoundingClientRect();
      return [rect.x, rect.y, rect.width, rect.height].map(Math.round);
    });
    return {
      navigation: rects('.navbar-nav .nav-link'),
      preferences: rects('.dropdown-toggle').map(rect => [rect[1], rect[3]]),
      content: rects('main').map(rect => [rect[0], rect[2]]),
      period: rects('.period-control').map(rect => [rect[0], rect[2]]),
    };
  });
  if (expand) {
    await page.locator('.navbar-toggler').click();
    await navigation.waitFor({ state: 'hidden' });
  }
  return layout;
}

async function openSourceReader(page, trigger, keyboard = false) {
  await page.evaluate(() => {
    window.sourceReaderReady = new Promise(resolve => {
      document.querySelector('#evidence').addEventListener('shown.bs.modal', () => resolve(true), { once: true });
    });
  });
  if (keyboard) await trigger.press('Enter');
  else await trigger.click();
  await page.evaluate(() => window.sourceReaderReady);
}

async function checkSourceReading(page, base, route, api) {
  await page.goto(base + '/dashboard/' + route);
  const opener = page.locator('[data-evidence]').first();
  if (!await opener.count()) return;
  const sourceURL = new URL(await opener.getAttribute('href'), base);
  const before = await page.evaluate(() => document.documentElement.scrollHeight);
  await openSourceReader(page, opener);
  const text = page.locator('.original-content');
  await text.waitFor();
  await page.locator('#evidence.show').waitFor();
  await page.waitForFunction(() => {
    const rect = document.querySelector('.original-content').getBoundingClientRect();
    return rect.left >= 0 && rect.right <= innerWidth + 1;
  });
  assert(await page.evaluate(() => document.documentElement.scrollHeight) <= before + 1, 'Opening a source lengthened the page');
  const bounds = await text.boundingBox();
  assert(bounds.height > 40 && bounds.y >= 0 && bounds.y + bounds.height <= page.viewportSize().height + 1);
  const source = await api(`/v1/scopes/${sourceURL.searchParams.get('scope')}/sources/${sourceURL.searchParams.get('source_type')}/${sourceURL.pathname.split('/evidence/')[1]}`);
  if (typeof source.content === 'string') assert.equal(await text.textContent(), source.content);
  else assert.deepEqual(JSON.parse(await text.textContent()), source.content);
  if (await text.evaluate(element => element.scrollHeight > element.clientHeight)) {
    const position = await page.evaluate(() => scrollY);
    await text.hover();
    await page.mouse.wheel(0, 400);
    await page.waitForFunction(() => document.querySelector('.original-content').scrollTop > 0);
    assert.equal(await page.evaluate(() => scrollY), position);
    await text.focus();
    await page.keyboard.press('Control+End');
    await page.waitForFunction(() => {
      const element = document.querySelector('.original-content');
      return element.scrollHeight - element.scrollTop - element.clientHeight < 2;
    });
  }
  const sources = page.locator('.source-content-view .dropdown-item');
  if (await sources.count() > 1) {
    const destination = await sources.nth(1).getAttribute('href');
    await page.locator('#evidence-source-picker').click();
    await sources.nth(1).click();
    await page.waitForFunction(href => document.querySelector('.source-content-view [aria-current="true"]')?.getAttribute('href') === href, destination);
    assert.equal(await text.evaluate(element => element.scrollTop), 0);
    await page.waitForFunction(() => document.querySelector('#evidence')?.contains(document.activeElement));
  }
  const url = page.url();
  const position = await page.evaluate(() => scrollY);
  for (const key of ['Escape', 'Enter']) {
    await page.context().setOffline(true);
    try {
      if (key === 'Enter') await page.locator('#evidence .btn-close').focus();
      await page.keyboard.press(key);
      await page.waitForFunction(() => !document.body.style.overflow);
      assert(await opener.evaluate(element => element === document.activeElement), 'Closing the source lost keyboard focus');
    } finally {
      await page.context().setOffline(false);
    }
    assert.equal(page.url(), url);
    assert.equal(await page.evaluate(() => scrollY), position);
    assert(await page.evaluate(() => document.documentElement.scrollHeight) <= before + 1);
    if (key === 'Escape') {
      await openSourceReader(page, opener, true);
    }
  }
}

async function checkMemoryAccordion(page) {
  const accordion = page.locator('#memory-accordion');
  if (!await accordion.isVisible()) return;
  const buttons = accordion.locator('.accordion-button');
  const location = page.url();
  await page.context().setOffline(true);
  try {
    for (const button of (await buttons.all()).slice(0, 2)) {
      if (await button.getAttribute('aria-expanded') !== 'true') {
        await button.focus();
        await button.press('Enter');
      }
      const panel = page.locator(await button.getAttribute('data-bs-target'));
      await panel.locator('.source-text').waitFor();
      await page.waitForFunction(() => document.querySelectorAll('#memory-accordion .accordion-collapse.show').length === 1 && !document.querySelector('#memory-accordion .collapsing'));
      assert((await panel.innerText()).trim(), 'Expanded memory has no text');
      assert(await panel.evaluate(element => element.scrollHeight <= element.clientHeight + 1), 'Expanded memory requires internal scrolling');
    }
    const expanded = accordion.locator('.accordion-button[aria-expanded="true"]');
    if (await expanded.count()) {
      await expanded.press('Enter');
      await page.waitForFunction(() => !document.querySelector('#memory-accordion .show, #memory-accordion .collapsing'));
    }
    assert.equal(page.url(), location, 'Expanding a memory navigated away');
  } finally {
    await page.context().setOffline(false);
  }
}

async function checkSourceRecovery(page) {
  await page.context().setOffline(true);
  try {
    await openSourceReader(page, page.locator('[data-evidence]').first());
    await page.locator('#evidence [data-source-error]:not([hidden])').waitFor();
  } finally {
    await page.context().setOffline(false);
  }
  await page.locator('#evidence [data-source-retry]').click();
  const text = page.locator('.original-content');
  await text.waitFor();
  const sources = page.locator('#evidence .dropdown-item');
  if (await sources.count() > 1) {
    const original = await text.textContent();
    const destination = await sources.nth(1).getAttribute('href');
    await page.locator('#evidence-source-picker').click();
    await page.context().setOffline(true);
    try {
      await sources.nth(1).click();
      await page.locator('#evidence [data-source-error]:not([hidden])').waitFor();
      assert.equal(await text.textContent(), original, 'A failed source switch discarded the readable source');
    } finally {
      await page.context().setOffline(false);
    }
    await page.locator('#evidence [data-source-retry]').click();
    await page.waitForFunction(href => document.querySelector('#evidence [aria-current="true"]')?.getAttribute('href') === href, destination);
  }
  await page.locator('#evidence .btn-close').click();
  await page.waitForFunction(() => !document.body.classList.contains('modal-open'));
}

async function main() {
  const base = process.env.POWERCONTEXT_BROWSER_URL || 'http://127.0.0.1:8765';
  const output = process.env.POWERCONTEXT_BROWSER_OUTPUT;
  assert(output, 'Set a private POWERCONTEXT_BROWSER_OUTPUT directory');
  fs.mkdirSync(output, { recursive: true, mode: 0o700 });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1536, height: 1024 } });
  const token = process.env.POWERCONTEXT_REPLAY_TOKEN;
  if (token) await context.setExtraHTTPHeaders({ Authorization: `Bearer ${token}` });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const api = async (route, data) => {
    const response = data ? await context.request.post(base + route, { data }) : await context.request.get(base + route);
    assert(response.ok(), `${route}: HTTP ${response.status()}`);
    return response.json();
  };
  const scopes = (await api('/v1/scopes')).items;
  const defaultScope = (await api('/v1/scopes/default')).scope_id;
  const report = { pages: [], scopes: scopes.map(scope => scope.scope_id), errors };
  for (const width of [390, 1536]) {
    await page.setViewportSize({ width, height: 1024 });
    let contentTop;
    for (const scope of scopes) {
      for (const name of ['home', 'handoff', 'notes', 'methods', 'usage']) {
        const response = await page.goto(`${base}/dashboard/${name}?scope=${encodeURIComponent(scope.scope_id)}`);
        assert.equal(response.status(), 200);
        assert.equal(await page.locator('#scope').inputValue(), scope.scope_id);
        const main = await page.locator('main').boundingBox();
        contentTop ??= main.y;
        assert(Math.abs(main.y - contentTop) < 1, `Content shifted with page length: ${name} at ${width}px`);
        if (name === 'home') {
          const bounds = await page.locator('#usage, #notes, #methods, #handoff').evaluateAll(items => items.map(item => {
            const rect = item.getBoundingClientRect();
            return { id: item.id, top: rect.top, bottom: rect.bottom };
          }));
          const sections = Object.fromEntries(bounds.map(item => [item.id, item]));
          assert(sections.usage.bottom <= sections.notes.top);
          assert(sections.notes.top <= sections.methods.top);
          assert(sections.methods.bottom <= sections.handoff.top);
        }
        if (name === 'notes') {
          assert(await page.locator('.memory-directory').isVisible());
          assert(await page.locator(width < 1200 ? '#memory-accordion' : '#note-reading').isVisible());
        }
        if (name === 'usage') {
          assert(await page.locator('.usage-sheet').isVisible());
          assert(await page.locator('.model-usage-table').isVisible());
        }
        report.pages.push({ page: name, scope: scope.scope_id, width, status: response.status() });
      }
    }
  }
  await page.goto(base + '/dashboard/home');
  assert.equal(await page.locator('#scope').inputValue(), defaultScope);
  const alternative = scopes.find(scope => scope.scope_id !== defaultScope);
  if (alternative) {
    await chooseScope(page, alternative.scope_id);
    await page.waitForURL(url => url.searchParams.get('scope') === alternative.scope_id);
    assert.equal((await api('/v1/scopes/default')).scope_id, defaultScope);
  }
  const parent = scopes.find(scope => scopes.some(child => child.parent_scope_id === scope.scope_id));
  if (parent) {
    const child = scopes.find(scope => scope.parent_scope_id === parent.scope_id);
    await checkScopePicker(page, base, parent, child);
    for (const scope of [parent, child]) {
      await page.goto(`${base}/dashboard/usage?scope=${scope.scope_id}`);
      const response = await context.request.post(base + '/v1/stats', {
        data: {
          selection: { mode: 'exact', scope_ids: [scope.scope_id] },
          period: '7d',
        },
      });
      assert(response.ok());
      const stats = await response.json();
      const chart = page.locator('[data-comparison-chart]');
      if (await chart.count()) {
        const daily = JSON.parse(await chart.getAttribute('data-days'));
        assert.deepEqual(daily.map(({ label, ...values }) => values), stats.recall.daily);
      }
      assert.equal((await api('/v1/scopes/default')).scope_id, defaultScope);
    }
  }
  const readingScope = process.env.POWERCONTEXT_BROWSER_SCOPE || defaultScope;
  let routes = ['home', 'handoff', 'notes', 'methods', 'methods?kind=skill', 'usage'];
  for (const family of ['handoff', 'experience', 'skill']) {
    let collection;
    if (family === 'skill') {
      const response = await context.request.post(base + '/v1/skill/library', { data: { scope_id: readingScope } });
      assert(response.ok());
      collection = { items: (await response.json()).skills.map(item => item.artifact) };
    } else collection = await api(`/v1/scopes/${readingScope}/artifacts/${family}`);
    if (collection.items.length) {
      const record = collection.items[0];
      routes.push(`${family === 'handoff' ? 'handoff-detail' : family}?artifact=${record.artifact_id}&revision=${record.revision}`);
    }
  }
  routes = routes.map(route => `${route}${route.includes('?') ? '&' : '?'}scope=${encodeURIComponent(readingScope)}`);
  const otherScope = scopes.find(scope => scope.scope_id !== readingScope);
  if (otherScope) {
    for (const route of routes) {
      await page.goto(base + '/dashboard/' + route);
      await chooseScope(page, otherScope.scope_id);
      await page.waitForURL(url => url.searchParams.get('scope') === otherScope.scope_id);
      const selected = new URL(page.url());
      const family = route.split('?')[0];
      const destination = { 'handoff-detail': 'handoff', experience: 'methods', skill: 'methods' }[family] || family;
      assert.equal(selected.pathname, '/dashboard/' + destination);
      assert.equal(await page.locator('#scope').inputValue(), otherScope.scope_id);
      if (destination === 'methods') assert.equal(selected.searchParams.get('kind'), family === 'skill' || route.includes('kind=skill') ? 'skill' : 'experience');
      await page.goBack();
      await page.waitForURL(url => url.searchParams.get('scope') === readingScope);
      await page.waitForFunction(scope => document.querySelector('#scope')?.value === scope, readingScope);
      assert.equal(await page.locator('#scope').inputValue(), readingScope);
    }
    assert.equal((await api('/v1/scopes/default')).scope_id, defaultScope);
  }
  const sourceRecord = routes.find(route => route.startsWith('experience?'));
  if (sourceRecord) {
    await page.goto(base + '/dashboard/' + sourceRecord);
    const source = page.locator('[data-evidence]').first();
    if (await source.count()) routes.push((await source.getAttribute('href')).replace('/dashboard/', ''));
  }
  for (const width of [320, 601, 768, 992, 1200, 1536]) {
    await page.setViewportSize({ width, height: 1024 });
    await page.goto(`${base}/dashboard/usage?scope=${readingScope}&lang=en&period=30d`);
    await clickNavigation(page, '.navbar-nav a[href*="/home?"]');
    await page.waitForURL(url => url.pathname.endsWith('/home'));
    await page.evaluate(async () => {
      for (let frame = 0; frame < 20; frame++) {
        await new Promise(requestAnimationFrame);
        for (const chart of document.querySelectorAll('[data-comparison-chart]')) {
          const svg = chart.querySelector('svg');
          if (svg && svg.getBoundingClientRect().width > chart.getBoundingClientRect().width + 1) {
            throw new Error('Chart measured its container before scoped layout was ready');
          }
        }
      }
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `chart at ${width}`);
    const chart = page.locator('[data-comparison-chart]');
    if (await chart.count()) await checkChartTooltip(page, chart);
  }
  for (const width of [320, 390, 1536]) {
    await page.setViewportSize({ width, height: 900 });
    for (const period of ['today', '7d', '30d']) {
      await page.goto(`${base}/dashboard/usage?scope=${readingScope}&period=${period}`);
      const chart = page.locator('[data-comparison-chart]');
      if (!await chart.count()) continue;
      const days = JSON.parse(await chart.getAttribute('data-days'));
      await chart.locator('.apexcharts-xaxis-label tspan').first().waitFor();
      const labels = await chart.locator('.apexcharts-xaxis-label tspan').allTextContents();
      const visible = labels.map(label => label.trim()).filter(Boolean);
      assert(visible.length, `Missing chart dates for ${period} at ${width}px`);
      for (const label of visible) assert(days.some(day => day.label === label), `Chart date ${label} is outside ${period}`);
      if (days.length === 1) assert.deepEqual(visible, [days[0].label]);
      if (period === '7d') await checkChartTooltip(page, chart);
    }
  }
  for (const width of [320, 390, 600, 768, 991, 992, 1024, 1199, 1200, 1280, 1399, 1400, 1536, 1920]) {
    await page.setViewportSize({ width, height: 1024 });
    for (const route of routes) {
      const response = await page.goto(`${base}/dashboard/${route}`);
      assert.equal(response.status(), 200, route);
      await page.waitForLoadState('load');
      await checkReadingBounds(page, `${route}, ${width}`);
      if (route.startsWith('methods')) {
        for (const row of await page.locator('.method-row').all()) {
          assert(await row.evaluate(element => {
            const bounds = element.getBoundingClientRect();
            const directory = element.parentElement.getBoundingClientRect();
            return bounds.top >= directory.top - 1 && bounds.bottom <= directory.bottom + 1;
          }), `Collection entry requires internal scrolling at ${width}px`);
        }
      }
      if (route.startsWith('home?') && width >= 1200) {
        const rows = await page.locator('.note-list .list-group-item').evaluateAll(items => items.map(item => item.getBoundingClientRect().height));
        if (rows.length) assert(Math.max(...rows) - Math.min(...rows) <= 1, `Home memories have unequal row heights at ${width}px`);
        const memories = await page.locator('#notes > .card').boundingBox();
        const methods = page.locator('#methods > .card');
        if (memories && await methods.count()) {
          const first = await methods.first().boundingBox();
          const last = await methods.last().boundingBox();
          assert(Math.abs(memories.y - first.y) <= 1 && Math.abs(memories.y + memories.height - last.y - last.height) <= 1, `Home card edges do not align at ${width}px`);
        }
      }
      const family = route.startsWith('handoff-detail?') ? 'handoff' : route.match(/^(experience|skill)\?/)?.[1];
      if (family) {
        const reference = new URL(base + '/dashboard/' + route).searchParams;
        assert(await page.getByText(`${family}/${reference.get('artifact')}@${reference.get('revision')}`, { exact: true }).isVisible());
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${route}, ${width}px`);
      await page.screenshot({ path: path.join(output, `${(route.startsWith('methods?kind=skill') ? 'skills' : route.split('?')[0].replaceAll('/', '-'))}-${width}.png`), fullPage: true });
    }
  }
  const localizedLayouts = new Map();
  for (const language of ['zh', 'en']) {
    for (const theme of ['light', 'dark']) {
      for (const width of [390, 1536]) {
        await page.setViewportSize({ width, height: 1024 });
        for (const route of routes) {
          const response = await page.goto(`${base}/dashboard/${route}`);
          await changePreference(page, 'lang', language);
          await changePreference(page, 'theme', theme);
          const layout = await displayLayout(page);
          const layoutKey = `${route}/${theme}/${width}`;
          if (language === 'zh') localizedLayouts.set(layoutKey, layout);
          else assert.deepEqual(layout, localizedLayouts.get(layoutKey), `Language changed control placement: ${layoutKey}`);
          const selected = new URL(page.url());
          for (const [key, value] of new URL(base + '/dashboard/' + route).searchParams) {
            assert.equal(selected.searchParams.get(key), value, `Preference change lost ${key}`);
          }
          assert.equal(response.status(), 200, route);
          await checkReadingBounds(page, `${route}, ${language}, ${theme}, ${width}`);
          assert.equal(await page.locator('html').getAttribute('lang'), language === 'zh' ? 'zh-CN' : 'en');
          assert.equal(await page.evaluate(() => document.documentElement.dataset.bsTheme || 'light'), theme);
          if (!route.startsWith('evidence/')) {
            const logo = page.locator(`.brand-${theme}`);
            assert(await logo.isVisible());
            assert(await logo.evaluate(image => image.complete && image.naturalWidth > 0 && Math.abs(image.width / image.height - image.naturalWidth / image.naturalHeight) < 0.1));
          }
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${route}, ${language}, ${theme}, ${width}`);
          await page.screenshot({ path: path.join(output, `${(route.startsWith('methods?kind=skill') ? 'skills' : route.split('?')[0].replaceAll('/', '-'))}-${language}-${theme}-${width}.png`), fullPage: true });
        }
      }
    }
  }
  await page.goto(base + '/dashboard/home');
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  assert.equal(await page.evaluate(() => document.documentElement.dataset.bsTheme), 'dark');
  await clickNavigation(page, '.navbar-nav a[href*="/notes?"]');
  await page.waitForURL(url => url.pathname.endsWith('/notes'));
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  assert(await page.locator('.brand-dark').isVisible());
  const currentScope = await page.locator('#scope').inputValue();
  await page.locator('.dropdown-toggle').first().click();
  await page.locator('.dropdown-item[lang="zh-CN"]').click();
  await page.waitForURL(url => url.searchParams.get('lang') === 'zh');
  assert.equal(await page.locator('html').getAttribute('lang'), 'zh-CN');
  assert.equal(await page.locator('#scope').inputValue(), currentScope);
  await page.locator('.display-preferences .dropdown-toggle').click();
  await page.locator('.dropdown-item[href*="theme=light"]').click();
  await page.waitForURL(url => url.searchParams.get('theme') === 'light');
  assert.equal(await page.evaluate(() => document.documentElement.dataset.bsTheme || 'light'), 'light');
  assert(await page.locator('.brand-light').isVisible());
  const experience = routes.find(route => route.startsWith('experience?'));
  if (experience) {
    await page.setViewportSize({ width: 1399, height: 1024 });
    await page.goto(base + '/dashboard/' + experience);
    const source = page.locator('[data-evidence]').first();
    if (await source.count()) {
      await checkSourceRecovery(page);
      await openSourceReader(page, source);
      await page.locator('.original-content').waitFor();
      for (const width of [1400, 1399]) {
        await page.setViewportSize({ width, height: 1024 });
        await page.locator('#evidence.show').waitFor();
        const reader = await page.locator('#evidence .modal-dialog').boundingBox();
        assert(reader.x >= 0 && reader.x + reader.width <= width + 1);
        assert(reader.width >= width * 0.7, 'Source reader is too narrow');
        assert(reader.y >= 0 && reader.y + reader.height <= page.viewportSize().height);
      }
      await page.locator('#evidence .btn-close').click();
      await page.waitForFunction(() => !document.body.style.overflow);
      await clickNavigation(page, '.navbar-nav a[href*="/usage?"]');
      await page.waitForURL(url => url.pathname.endsWith('/usage'));
      assert.equal(await page.locator('.modal-backdrop').count(), 0);
      await page.goBack();
      await page.locator('[data-evidence]').first().waitFor();
      await openSourceReader(page, page.locator('[data-evidence]').first());
      await page.goForward();
      await page.waitForURL(url => url.pathname.endsWith('/usage'));
      await page.waitForFunction(() => !document.body.classList.contains('modal-open'));
      assert.equal(await page.locator('.modal-backdrop').count(), 0);
    }
  }
  for (const viewport of [{ width: 390, height: 700 }, { width: 844, height: 390 }, { width: 1400, height: 600 }, { width: 1536, height: 900 }]) {
    await page.setViewportSize(viewport);
    for (const route of routes.filter(route => /^(experience|skill|handoff-detail)\?/.test(route))) {
      await checkSourceReading(page, base, route, api);
    }
    await page.goto(`${base}/dashboard/usage?scope=${readingScope}&period=30d`);
    for (const button of await page.locator('.accordion-button').all()) {
      await button.click();
      const panel = page.locator(await button.getAttribute('data-bs-target'));
      await page.waitForFunction(selector => document.querySelector(selector).classList.contains('show'), await button.getAttribute('data-bs-target'));
      const body = panel.locator('.accordion-body');
      assert((await body.boundingBox()).height <= viewport.height + 1);
      await body.focus();
      await page.keyboard.press('Control+End');
      await button.click();
      await panel.waitFor({ state: 'hidden' });
    }
  }
  for (const viewport of [{ width: 390, height: 320 }, { width: 1024, height: 390 }, { width: 1536, height: 320 }]) {
    await page.setViewportSize(viewport);
    for (const language of ['zh', 'en']) {
      await page.goto(`${base}/dashboard/home?scope=${readingScope}&lang=${language}`);
      await changePreference(page, 'lang', language === 'zh' ? 'en' : 'zh');
    }
  }
  for (const width of [390, 1200, 1536]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${base}/dashboard/notes?scope=${readingScope}&lang=en`);
    await checkMemoryAccordion(page);
    const geometry = () => page.evaluate(() => ({
      height: document.documentElement.scrollHeight,
      panels: [...document.querySelectorAll('.collection-panel, .memory-directory')].map(element => {
        const rect = element.getBoundingClientRect();
        return [rect.x, rect.y + scrollY, rect.width, rect.height].map(Math.round);
      }),
      pager: [...document.querySelectorAll('[aria-label="Pagination"]')].map(element => {
        const rect = element.getBoundingClientRect();
        return [rect.x, rect.y + scrollY, rect.width, rect.height].map(Math.round);
      }),
    }));
    const firstLayout = await geometry();
    const checkMemoryDirectory = async () => {
      const directory = page.locator('.memory-items');
      assert(await directory.evaluate(element => element.scrollHeight <= element.clientHeight + 1), 'Memory directory requires internal scrolling');
      for (const item of await directory.locator('a').all()) {
        assert(await item.evaluate(element => {
          const bounds = element.getBoundingClientRect();
          const parent = element.parentElement.getBoundingClientRect();
          return bounds.top >= parent.top - 1 && bounds.bottom <= parent.bottom + 1;
        }), 'Memory entry falls outside its directory');
      }
    };
    await checkMemoryDirectory();
    let following = page.getByRole('link', { name: 'Next page', exact: true });
    while (await following.count()) {
      const destination = await following.getAttribute('href');
      await following.click();
      await page.waitForURL(base + destination);
      const layout = await geometry();
      if (width >= 1200) assert.deepEqual(layout, firstLayout, `Pagination shifted the reading layout at ${width}`);
      else assert(await page.getByRole('navigation', { name: 'Pagination', exact: true }).isVisible());
      await checkMemoryDirectory();
      following = page.getByRole('link', { name: 'Next page', exact: true });
    }
    const selected = page.locator('.note-selector').last();
    if (await selected.count() && await selected.isVisible()) {
      const destination = await selected.getAttribute('href');
      await selected.click();
      await page.waitForURL(base + destination);
      await page.waitForFunction(() => document.activeElement?.id === 'note-reading');
      await page.waitForFunction(() => {
        const reading = document.querySelector('#note-reading').getBoundingClientRect();
        return reading.top >= -1 && reading.bottom <= innerHeight + 1;
      });
    }
  }
  const entries = (await api('/v1/memory/entries/list', { scope_id: readingScope })).entries;
  if (entries.length) {
    const query = entries[0].text.match(/[A-Za-z][A-Za-z-]{3,}/)?.[0] || entries[0].text.slice(0, 12);
    const hits = (await api('/v1/memory/search', { scope_id: readingScope, query, mode: 'fts', limit: 50 })).hits;
    await page.getByRole('searchbox').fill(query);
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await page.waitForURL(url => url.searchParams.get('q') === query && !url.searchParams.has('notes_page'));
    const expected = new Set(hits.map(hit => hit.citation.entry_id));
    const seen = new Set();
    let resultPage = 1;
    while (true) {
      const pagination = page.getByRole('navigation', { name: 'Pagination', exact: true });
      assert(await pagination.isVisible(), 'Search pagination is hidden');
      assert(await pagination.getByText(`Page ${resultPage}`, { exact: true }).isVisible());
      for (const id of await page.locator('.note-selector').evaluateAll(links => links.map(link => new URL(link.href).searchParams.get('entry')))) {
        assert(expected.has(id));
        assert(!seen.has(id));
        seen.add(id);
      }
      const following = page.getByRole('link', { name: 'Next page', exact: true });
      if (!await following.count()) break;
      const destination = await following.getAttribute('href');
      await following.click();
      await page.waitForURL(base + destination);
      resultPage++;
    }
    assert.deepEqual(seen, expected);
    await page.setViewportSize({ width: 390, height: 900 });
    await checkMemoryAccordion(page);
    await page.getByRole('link', { name: 'Clear search', exact: true }).click();
    await page.waitForURL(url => !url.searchParams.has('q') && !url.searchParams.has('notes_page'));
    assert(await page.locator('.note-selector').count());
  }
  for (const route of ['notes', 'methods?kind=skill']) {
    const url = new URL(`${base}/dashboard/${route}`);
    url.searchParams.set('scope', readingScope);
    url.searchParams.set('lang', 'en');
    url.searchParams.set('q', 'no-matching-dashboard-pagination-result');
    await page.goto(url.href);
    const pagination = page.getByRole('navigation', { name: 'Pagination', exact: true });
    assert(await pagination.isVisible(), 'Empty search pagination is hidden');
    for (const label of ['Previous', 'Page 1', 'Next page']) {
      assert(await pagination.getByText(label, { exact: true }).isVisible());
    }
    assert.equal(await pagination.getByRole('link').count(), 0, 'Empty search has an available page link');
  }
  await page.goto(`${base}/dashboard/methods?scope=${readingScope}&lang=en`);
  await page.locator('.nav-tabs').getByRole('link', { name: 'Skill', exact: true }).click();
  await page.waitForURL(url => url.searchParams.get('kind') === 'skill');
  const skills = (await api('/v1/skill/library', { scope_id: readingScope })).skills;
  if (skills.length) {
    await page.getByRole('searchbox').fill(skills[0].content.name);
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await page.waitForURL(url => url.searchParams.get('q') === skills[0].content.name);
    assert(await page.getByRole('navigation', { name: 'Pagination', exact: true }).isVisible());
    await page.getByRole('link', { name: skills[0].content.name, exact: true }).click();
    await page.waitForURL(url => url.pathname.endsWith('/skill'));
    await page.locator('.back-link').click();
    await page.waitForURL(url => url.pathname.endsWith('/methods') && url.searchParams.get('kind') === 'skill');
  }
  await page.goto(base + '/dashboard/home');
  await context.setOffline(true);
  await clickNavigation(page, '.navbar-nav a[href*="/notes?"]');
  await page.locator('#network-error:not([hidden])').waitFor();
  await context.setOffline(false);
  await page.locator('#network-error a').click();
  await page.locator('#network-error[hidden]').waitFor({ state: 'attached' });
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
  console.log(`Verified ${report.pages.length} scope pages, ${routes.length} responsive pages, navigation, source reader and recovery.`);
}
main().catch(error => { console.error(error); process.exit(1); });
