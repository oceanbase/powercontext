// Verify a running Server against its own API responses without installing fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

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
    for (const name of ['home', 'handoff', 'notes', 'methods', 'usage', 'guide']) {
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
  const routes = ['home', 'handoff', 'notes', 'methods', 'usage', 'guide'];
  for (const family of ['handoff', 'experience', 'skill']) {
    const collection = await api(`/v1/scopes/${defaultScope}/artifacts/${family}`);
    if (collection.items.length) {
      const record = collection.items[0];
      routes.push(`${family === 'handoff' ? 'handoff-detail' : family}?artifact=${record.artifact_id}&revision=${record.revision}`);
    }
  }
  for (const width of [390, 1024, 1536]) {
    await page.setViewportSize({ width, height: 1024 });
    for (const route of routes) {
      const response = await page.goto(`${base}/dashboard/${route}`);
      assert.equal(response.status(), 200, route);
      await page.waitForFunction(() => !!window.htmx);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, route);
      await page.screenshot({ path: path.join(output, `${route.split('?')[0]}-${width}.png`), fullPage: true });
    }
  }
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
      await page.locator('.navbar-nav a[href*="/usage?"]').click();
      await page.waitForURL(url => url.pathname.endsWith('/usage'));
      assert.equal(await page.locator('.offcanvas-backdrop').count(), 0);
      await page.goBack();
      await page.locator('[data-evidence]').first().waitFor();
    }
  }
  await page.goto(base + '/dashboard/home');
  await context.setOffline(true);
  await page.locator('.navbar-nav a[href*="/notes?"]').click();
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
