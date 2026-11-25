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

// Read-only browser acceptance against two existing Scopes; requires Playwright.
// node scripts/dashboard_processing_browser.cjs URL SCOPE OTHER_SCOPE
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

async function main() {
  const [base, scope, other] = process.argv.slice(2);
  assert(base && scope && other && scope !== other, 'Provide a Server URL and two distinct readable Scope IDs');
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({
      viewport: {width: 1440, height: 1000},
      extraHTTPHeaders: process.env.POWERCONTEXT_TOKEN ? {Authorization: `Bearer ${process.env.POWERCONTEXT_TOKEN}`} : {},
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const fragment = '**/dashboard/artifact-processing-status?*';
    const root = page.locator('[data-artifact-processing]');
    const snapshot = page.locator('[data-processing-snapshot]');
    const settled = () => page.waitForFunction(() => {
      const root = document.querySelector('[data-artifact-processing]');
      return root?.querySelector('[data-processing-snapshot]') && !root.hasAttribute('aria-busy');
    });
    const open = async (route = 'notes', query = '') => {
      const url = new URL(`${base}/dashboard/${route}`);
      url.searchParams.set('scope', scope);
      url.searchParams.set('lang', 'en');
      for (const [key, value] of new URLSearchParams(query.replace(/^&/, ''))) url.searchParams.set(key, value);
      await page.goto(url.href);
      await settled();
    };
    const refresh = async () => {
      await root.locator('[data-processing-refresh]').click();
      await page.waitForFunction(() => !document.querySelector('[data-artifact-processing]')?.hasAttribute('aria-busy'));
    };

    for (const [route, family, query] of [
      ['notes', 'atomic-memory', ''], ['topics', 'topic-memory', ''],
      ['methods', 'experience', ''], ['methods', 'skill', '&kind=skill'],
      ['profile', 'profile', ''], ['handoff', 'handoff', ''], ['prompts', 'prompt', ''],
    ]) {
      await open(route, query);
      assert.equal(await snapshot.getAttribute('data-family'), family);
    }
    for (const width of [1440, 390]) {
      for (const lang of ['en', 'zh']) {
        for (const theme of ['light', 'dark']) {
          await page.setViewportSize({width, height: 1000});
          await open('notes', `&theme=${theme}&lang=${lang}`);
          assert.equal(await page.locator('html').getAttribute('lang'), lang === 'zh' ? 'zh-CN' : 'en');
          await root.locator('[data-processing-details]').click();
          await page.locator('[data-processing-drawer].show').waitFor();
          await page.waitForFunction(() => {
            const drawer = document.querySelector('[data-processing-drawer]');
            const bounds = drawer.getBoundingClientRect();
            return drawer.classList.contains('show') && bounds.x >= -1 && bounds.right <= innerWidth + 1;
          });
          const bounds = await page.locator('[data-processing-drawer]').boundingBox();
          assert(bounds.x >= -1 && bounds.x + bounds.width <= width + 1, 'Drawer overflows viewport');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
          await page.keyboard.press('Escape');
          await page.locator('[data-processing-drawer][aria-modal]').waitFor({state: 'hidden'});
          assert(await root.locator('[data-processing-details]').evaluate(node => document.activeElement === node));
        }
      }
    }

    await page.setViewportSize({width: 1440, height: 1000});
    await open();
    const previous = await snapshot.innerText();
    const location = page.url();
    await page.route(fragment, route => route.fulfill({status: 503, body: ''}));
    await refresh();
    assert.equal(await snapshot.innerText(), previous);
    assert((await root.locator('[data-processing-feedback]').innerText()).includes('previous observation'));
    assert.equal(page.url(), location);
    assert(await page.locator('.memory-directory').isVisible());
    await page.unroute(fragment);
    await page.route(fragment, route => route.fulfill({status: 403, body: ''}));
    await refresh();
    assert.equal(await snapshot.count(), 0, 'Authorization failure retained a prior snapshot');
    assert(await page.locator('.memory-directory').isVisible());
    await page.unroute(fragment);
    await refresh();
    await settled();

    // Compare clocks in both directions and expire both copies without polling.
    let requests = 0;
    for (const skew of [-120000, 120000]) {
      await page.route(fragment, async route => {
        requests++;
        const response = await route.fetch();
        const queried = Date.now() + skew;
        const timing = `data-queried="${new Date(queried).toISOString()}" data-expires="${new Date(queried + 2000).toISOString()}"`;
        const body = (await response.text())
          .replace(/ data-(?:queried|expires)="[^"]*"/g, '')
          .replace('data-processing-snapshot', `data-processing-snapshot ${timing}`)
          .replace(/(data-processing-executor>)[^<]*/g, '$1Running when observed');
        await route.fulfill({response, body});
      });
      await refresh();
      const labels = root.locator('[data-processing-executor]');
      assert.deepEqual(await labels.allTextContents(), ['Running when observed', 'Running when observed']);
      await page.waitForFunction(() => {
        const root = document.querySelector('[data-artifact-processing]');
        return [...root.querySelectorAll('[data-processing-executor]')].every(node => node.textContent === root.dataset.stale);
      }, null, {timeout: 5000});
      await page.unroute(fragment);
    }
    assert.equal(requests, 2);

    // Hold the destination page until a late Memory refresh has finished.
    const topics = '**/dashboard/topics?*';
    const destinationReady = Promise.withResolvers();
    const releaseDestination = Promise.withResolvers();
    const refreshReady = Promise.withResolvers();
    const releaseRefresh = Promise.withResolvers();
    const refreshFinished = Promise.withResolvers();
    await page.route(topics, async route => {
      destinationReady.resolve();
      await releaseDestination.promise;
      await route.continue();
    });
    await page.route(fragment, async route => {
      const response = await route.fetch();
      refreshReady.resolve();
      await releaseRefresh.promise;
      await route.fulfill({response}).catch(() => {}); // The outgoing fetch may already be cancelled.
      refreshFinished.resolve();
    });
    await root.locator('[data-processing-refresh]').click();
    await refreshReady.promise;
    await page.locator('.nav-link[href*="/dashboard/topics"]').first().click();
    await destinationReady.promise;
    releaseRefresh.resolve();
    await refreshFinished.promise;
    assert.equal(await snapshot.count(), 0, 'Navigation retained the outgoing Memory snapshot');
    await page.unroute(fragment);
    releaseDestination.resolve();
    await settled();
    assert.equal(await snapshot.getAttribute('data-family'), 'topic-memory');
    await page.unroute(topics);
    await open();

    // Failed Scope navigation must leave the picker, URL and refreshed observation in agreement.
    const pickScope = value => page.evaluate(value => {
      const picker = document.querySelector('#scope').tomselect;
      picker.addOption({scope_id: value, display_title: value});
      picker.setValue(value);
    }, value);
    const notes = '**/dashboard/notes?*';
    for (const failure of ['disconnect', 503, 204]) {
      await page.route(notes, route => failure === 'disconnect' ? route.abort('failed') : route.fulfill({status: failure}));
      await pickScope(other);
      await page.locator('[data-scope-switch-error]').waitFor();
      const selectedTitle = await page.locator('#scope').evaluate(node => node.tomselect.options[node.value].display_title);
      assert((await page.locator('[data-scope-switch-error]').innerText()).includes(selectedTitle));
      assert.equal(await page.locator('#scope').inputValue(), scope);
      assert.equal(new URL(page.url()).searchParams.get('scope'), scope);
      await refresh();
      assert.equal(await snapshot.getAttribute('data-scope'), scope);
      await page.unroute(notes);
    }

    // Recover the retained page even when a drawer navigation fails during its closing transition.
    await root.locator('[data-processing-details]').click();
    const outgoingDrawer = page.locator('[data-processing-drawer].show');
    const oldId = await outgoingDrawer.getAttribute('id');
    await page.route(notes, route => route.fulfill({status: 503}));
    await pickScope(other);
    await page.waitForFunction(id => {
      const root = document.querySelector('[data-artifact-processing]');
      const drawer = root?.querySelector('[data-processing-drawer]');
      return drawer && drawer.id !== id && !root.hasAttribute('aria-busy');
    }, oldId);
    assert.equal(await snapshot.getAttribute('data-scope'), scope);
    assert.equal(await root.locator('[data-processing-feedback]').innerText(), '');
    assert.equal(await page.locator('#scope').inputValue(), scope);
    await page.unroute(notes);

    // A delayed options preload and a replaced navigation must both preserve a newer selection.
    const options = '**/dashboard/scope-options?*';
    const optionsReady = Promise.withResolvers();
    const releaseOptions = Promise.withResolvers();
    await page.route(options, async route => {
      optionsReady.resolve();
      await releaseOptions.promise;
      const current = {scope_id: scope, title: 'Original Scope', display_title: 'Original Scope', parent_scope_id: null};
      await route.fulfill({json: {current, parent: null, items: [current], next_cursor: null}});
    });
    await open();
    await optionsReady.promise;
    const firstRequest = Promise.withResolvers();
    const secondRequest = Promise.withResolvers();
    const releaseFirst = Promise.withResolvers();
    const releaseSecond = Promise.withResolvers();
    await page.route(notes, async route => {
      const first = new URL(route.request().url()).searchParams.get('scope') === other;
      (first ? firstRequest : secondRequest).resolve();
      await (first ? releaseFirst : releaseSecond).promise;
      await route.abort('failed').catch(() => {});
    });
    await pickScope(other);
    await firstRequest.promise;
    await pickScope('');
    await secondRequest.promise;
    releaseFirst.resolve();
    await page.evaluate(() => {
      window.scopeOptionsLoaded = new Promise(resolve => {
        document.querySelector('#scope').tomselect.on('load', () => resolve());
      });
    });
    releaseOptions.resolve();
    await page.evaluate(() => window.scopeOptionsLoaded);
    assert.equal(await page.locator('#scope').inputValue(), '');
    releaseSecond.resolve();
    await page.locator('[data-scope-switch-error]').waitFor();
    assert.equal(await page.locator('#scope').inputValue(), scope);
    await page.unroute(notes);
    await page.unroute(options);

    // Changing the Scope while a refresh is delayed must discard that response.
    await page.route(fragment, async route => {
      const response = await route.fetch();
      if (new URL(route.request().url()).searchParams.get('scope') === scope) {
        await new Promise(resolve => setTimeout(resolve, 800));
      }
      await route.fulfill({response}).catch(() => {}); // Cancellation is expected on navigation.
    });
    await root.locator('[data-processing-refresh]').click();
    await root.locator('[data-processing-details]').click();
    await pickScope(other);
    await page.waitForURL(url => url.searchParams.get('scope') === other);
    await settled();
    await page.waitForTimeout(1000);
    assert.equal(await snapshot.getAttribute('data-scope'), other);
    assert.equal(await page.locator('.offcanvas-backdrop').count(), 0);
    assert.equal(await page.locator('body').evaluate(node => node.style.overflow), '');
    await page.unroute(fragment);

    await open('methods');
    const methods = '**/dashboard/methods?*';
    await page.route(methods, route => route.abort('failed'));
    const failedNavigation = page.waitForEvent('requestfailed', request =>
      new URL(request.url()).pathname === '/dashboard/methods');
    await page.locator('.nav-tabs a[href*="kind=skill"]').click();
    await failedNavigation;
    await page.unroute(methods);
    await refresh();
    await settled();
    assert.equal(await snapshot.getAttribute('data-family'), 'experience');
    assert.equal(await snapshot.getAttribute('data-scope'), scope);

    await page.locator('.nav-tabs a[href*="kind=skill"]').click();
    await page.waitForURL(url => url.searchParams.get('kind') === 'skill');
    await settled();
    assert.equal(await snapshot.getAttribute('data-family'), 'skill');
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('[data-processing-snapshot]')?.dataset.family === 'experience');
    await settled();
    assert.equal(await snapshot.getAttribute('data-family'), 'experience');

    await open();
    const note = page.locator('.note-selector').first();
    if (await note.count()) {
      const destination = await note.getAttribute('href');
      await note.click();
      await page.waitForURL(new URL(destination, base).href);
      await settled();
      const selected = page.url();
      await refresh();
      assert.equal(page.url(), selected, 'Status refresh changed the selected memory');
      assert.equal(await root.count(), 1, 'Replacing notes-browser duplicated the status component');
    }
    await root.locator('[data-processing-details]').click();
    await page.locator('[data-processing-drawer].show').waitFor();
    await page.locator('[data-processing-drawer] a[href*="record_type=dreams"]').last().click();
    await page.waitForURL(url => url.pathname.endsWith('/processing'));
    await page.goBack();
    await page.waitForURL(url => url.pathname.endsWith('/notes'));
    await settled();
    assert.equal(await page.locator('[data-processing-drawer][aria-modal]').count(), 0);
    assert.equal(await page.locator('.offcanvas-backdrop').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Processing status browser acceptance passed: families, themes, mobile, focus, errors, expiry, Scope races, tabs and history.');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
