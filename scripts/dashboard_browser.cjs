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
      for (const rect of range.getClientRects()) {
        const left = scroll ? rect.left - scroll.getBoundingClientRect().left + scroll.scrollLeft : rect.left;
        const right = left + rect.width;
        if (bounds && (left < bounds.left - 2 || right > bounds.right + 2)) issues.push(node.textContent.slice(0, 80));
        if (!scroll && (left < -2 || right > innerWidth + 2)) issues.push(node.textContent.slice(0, 80));
      }
    }
    return [...new Set(issues)];
  });
  assert.deepEqual(overflow, [], `Reading content exceeds its page or card: ${route}`);
}

async function clickNavigation(page, selector) {
  const target = page.locator(selector);
  if (!await target.isVisible()) await page.locator('.navbar-toggler').click();
  await target.click();
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
  const api = async route => {
    const response = await context.request.get(base + route);
    assert(response.ok(), `${route}: HTTP ${response.status()}`);
    return response.json();
  };
  const scopes = (await api('/v1/scopes')).items;
  const defaultScope = (await api('/v1/scopes/default')).scope_id;
  const report = { pages: [], scopes: scopes.map(scope => scope.scope_id), errors };
  for (const scope of scopes) {
    for (const name of ['home', 'handoff', 'notes', 'methods', 'usage']) {
      const response = await page.goto(`${base}/dashboard/${name}?scope=${encodeURIComponent(scope.scope_id)}`);
      assert.equal(response.status(), 200);
      assert.equal(await page.locator('#scope').inputValue(), scope.scope_id);
      report.pages.push({ page: name, scope: scope.scope_id, status: response.status() });
    }
  }
  await page.goto(base + '/dashboard/home');
  assert.equal(await page.locator('#scope').inputValue(), defaultScope);
  const alternative = scopes.find(scope => scope.scope_id !== defaultScope);
  if (alternative) {
    await page.locator('#scope').selectOption(alternative.scope_id);
    await page.locator('.scope-go').click();
    await page.waitForURL(url => url.searchParams.get('scope') === alternative.scope_id);
    assert.equal((await api('/v1/scopes/default')).scope_id, defaultScope);
  }
  const parent = scopes.find(scope => scopes.some(child => child.parent_scope_id === scope.scope_id));
  if (parent) {
    for (const extent of ['exact', 'subtree']) {
      await page.goto(`${base}/dashboard/usage?scope=${parent.scope_id}&extent=${extent}`);
      const response = await context.request.post(base + '/v1/stats', {
        data: {
          selection: extent === 'exact' ? { mode: extent, scope_ids: [parent.scope_id] } : { mode: extent, root_scope_id: parent.scope_id },
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
  let routes = ['home', 'handoff', 'notes', 'methods', 'usage'];
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
  }
  for (const width of [320, 390, 600, 768, 991, 992, 1024, 1199, 1200, 1280, 1399, 1400, 1536, 1920]) {
    await page.setViewportSize({ width, height: 1024 });
    for (const route of routes) {
      const response = await page.goto(`${base}/dashboard/${route}`);
      assert.equal(response.status(), 200, route);
      await page.waitForLoadState('load');
      await checkReadingBounds(page, `${route}, ${width}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, route);
      await page.screenshot({ path: path.join(output, `${route.split('?')[0].replaceAll('/', '-')}-${width}.png`), fullPage: true });
    }
  }
  for (const language of ['zh', 'en']) {
    for (const theme of ['light', 'dark']) {
      for (const width of [390, 1536]) {
        await page.setViewportSize({ width, height: 1024 });
        for (const route of routes) {
          const separator = route.includes('?') ? '&' : '?';
          const response = await page.goto(`${base}/dashboard/${route}${separator}lang=${language}&theme=${theme}`);
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
          await page.screenshot({ path: path.join(output, `${route.split('?')[0].replaceAll('/', '-')}-${language}-${theme}-${width}.png`), fullPage: true });
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
  await page.locator('.dropdown-toggle').nth(1).click();
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
      await source.click();
      await page.locator('#evidence.show').waitFor();
      assert(await page.locator('#evidence-content').innerText());
      await page.setViewportSize({ width: 1400, height: 1024 });
      await page.waitForFunction(() => !document.querySelector('#evidence').classList.contains('show'));
      await page.setViewportSize({ width: 1399, height: 1024 });
      await source.click();
      await page.locator('#evidence.show').waitFor();
      await page.locator('#evidence .btn-close').click();
      await page.waitForFunction(() => !document.body.style.overflow);
      await clickNavigation(page, '.navbar-nav a[href*="/usage?"]');
      await page.waitForURL(url => url.pathname.endsWith('/usage'));
      assert.equal(await page.locator('.offcanvas-backdrop').count(), 0);
      await page.goBack();
      await page.locator('[data-evidence]').first().waitFor();
    }
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
  console.log(`Verified ${report.pages.length} scope pages, ${routes.length} responsive pages, navigation, drawer and recovery.`);
}
main().catch(error => { console.error(error); process.exit(1); });
