// Run with Playwright and its Chromium browser available on the Node search path.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ollama-buddy-browser-'));
  const server = spawn('python3', [path.join(__dirname, 'fixture_server.py')], {
    env: { ...process.env, OLLAMA_BUDDY_DATA: temporary, PYTHONDONTWRITEBYTECODE: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let browser;
  let errors = [];
  server.stderr.on('data', data => errors.push(data.toString()));
  try {
    const fixture = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('Fixture server did not start')), 5000);
      server.on('exit', code => { clearTimeout(timer); reject(new Error(`Server exit ${code}: ${errors.join('')}`)); });
      server.stdout.on('data', data => {
        output += data;
        if (output.includes('\n')) {
          clearTimeout(timer);
          resolve(JSON.parse(output.split('\n')[0]));
        }
      });
    });
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ locale: 'en-US', timezoneId: 'America/Los_Angeles' });
    const dashboard = await context.newPage();
    const mini = await context.newPage();
    const pageErrors = [];
    for (const page of [dashboard, mini]) page.on('pageerror', err => pageErrors.push(err.message));
    await dashboard.goto(fixture.url);
    await dashboard.waitForLoadState('networkidle');
    await dashboard.locator('.quota .meter').waitFor();
    await mini.goto(fixture.url + '/mini');
    await mini.waitForLoadState('networkidle');
    // Inspect the rendered pages before operating controls.
    assert.match(await dashboard.locator('#app').innerText(), /Estimated monthly quota/i);
    assert.match(await mini.locator('#body').innerText(), /Estimated monthly quota/i);
    assert.equal(await dashboard.locator('.models img').count(), 0);
    assert.equal(await mini.locator('.models img').count(), 0);
    assert.equal(await dashboard.evaluate(() => window.injected), undefined);
    assert.equal(await mini.evaluate(() => window.injected), undefined);
    assert.equal(await dashboard.locator('[data-model]').count(), 0);
    assert.equal(await mini.locator('#body').innerText().then(text => text.includes('<img')), false);
    const payload = await (await context.request.get(fixture.url + '/api/usage')).json();
    assert.equal(JSON.stringify(payload).includes('fixture-secret'), false);
    assert.equal(JSON.stringify(payload).includes('never-expose@example.test'), false);
    console.log('PASS historical models removed, CSP and secret minimisation');

    await dashboard.locator('.quota-settings summary').first().click();
    await dashboard.locator('#theme-select').selectOption('light');
    await mini.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await dashboard.locator('#lang-select').selectOption('fr');
    await dashboard.waitForFunction(() => document.documentElement.lang === 'fr');
    await mini.waitForFunction(() => document.documentElement.lang === 'fr');
    console.log('PASS theme and language synchronisation');
    await dashboard.locator('#notifications').check();
    await dashboard.waitForFunction(() => lastData.notifications === true);
    const preferences = JSON.parse(fs.readFileSync(path.join(temporary, 'config.json'), 'utf8'));
    assert.equal(preferences.notifications, true);
    assert.equal(preferences.ollama_api_key, '');
    await dashboard.evaluate(() => setNativeNotificationStatus('denied'));
    assert.match(await dashboard.locator('#notifications-hint').innerText(), /bloquées/);
    await dashboard.locator('#notifications').uncheck();
    await dashboard.waitForFunction(() => lastData.notifications === false);
    console.log('PASS optional notifications, denied permission hint and keychain-only config');

    if (!await dashboard.locator('.quota-settings').first().evaluate(el => el.open))
      await dashboard.locator('.quota-settings summary').first().click();
    await dashboard.locator('#monthly').fill('75');
    await context.request.post(fixture.url + '/api/theme', { data: { theme: 'dark' } });
    await dashboard.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    assert.equal(await dashboard.locator('#monthly').inputValue(), '75');
    assert.equal(await dashboard.locator('.quota-settings').first().evaluate(el => el.open), true);
    await dashboard.locator('#save-monthly').click();
    await dashboard.waitForFunction(() => lastData.quota.monthly === 75);
    console.log('PASS live updates preserve active input and settings');

    const utcInput = new Date(Date.parse(fixture.anchor) + 3600000).toISOString().slice(0, 16);
    await dashboard.locator('#reset-at').fill(utcInput);
    await dashboard.locator('#monthly').focus();
    const expected = Date.parse(utcInput + 'Z');
    await dashboard.waitForFunction(value => lastData.quota.resets_set === value, expected);
    assert.equal(await dashboard.locator('#reset-at').inputValue(), utcInput);
    const todayLabel = await dashboard.evaluate(day => dayLabel(day), fixture.today);
    assert.match(todayLabel, new RegExp(String(Number(fixture.today.slice(-2)))));
    await dashboard.locator('#reset-at').fill('');
    await dashboard.locator('#monthly').focus();
    await dashboard.waitForFunction(() => lastData.quota.resets_set === 0);
    assert.match(await dashboard.locator('#app').innerText(), /Aucun cycle/);
    assert.equal(await dashboard.locator('.quota .meter').count(), 0);
    assert.match(await dashboard.locator('#app').innerText(), /Dépense disponible/);
    assert.equal(await dashboard.locator('.model.day').count(), 7);
    await mini.waitForFunction(() => document.getElementById('body').textContent.includes('Dépense disponible'));
    console.log('PASS UTC date roundtrip, clearing the cycle and spending without quota');

    await context.request.post(fixture.url + '/api/quota', { data: { quota_reset_at: expected } });
    await dashboard.locator('.quota .meter').waitFor();
    await dashboard.locator('#remove-key').click();
    await dashboard.waitForFunction(() => lastData.quota.key_set === false);
    assert.equal(await dashboard.locator('.quota .meter').count(), 0);
    await dashboard.locator('#api-key').fill('replacement-key');
    await dashboard.locator('#save-key').click();
    await dashboard.locator('.quota .meter').waitFor();
    assert.equal(await dashboard.locator('#api-key').inputValue(), '');
    assert.equal(await dashboard.locator('[data-model]').count(), 0);
    console.log('PASS key removal, reconnection and account history isolation');

    await dashboard.evaluate(() => postQuota({ monthly_quota: -1 }).catch(() => {}));
    await dashboard.locator('#action-error').waitFor({ state: 'visible' });
    assert.match(await dashboard.locator('#action-error').innerText(), /Échec/);

    const fallback = await context.newPage();
    fallback.on('pageerror', err => pageErrors.push(err.message));
    await fallback.addInitScript(() => {
      delete window.EventSource;
      const active = new Set();
      const start = window.setInterval.bind(window);
      const stop = window.clearInterval.bind(window);
      window.setInterval = (...args) => { const id = start(...args); active.add(id); return id; };
      window.clearInterval = id => { active.delete(id); stop(id); };
      window.activePolls = () => active.size;
    });
    await fallback.goto(fixture.url);
    await fallback.locator('.quota .meter').waitFor();
    await fallback.evaluate(() => { connect(); connect(); });
    assert.equal(await fallback.evaluate(() => activePolls()), 1);
    await fallback.close();
    console.log('PASS polling fallback reconnects without duplicate timers');
    if (process.env.OLLAMA_BUDDY_QA) {
      await dashboard.locator('#theme-select').selectOption('light');
      await dashboard.waitForFunction(() => document.documentElement.dataset.theme === 'light');
      await dashboard.screenshot({ path: '/private/tmp/ollama-buddy-dashboard.png', fullPage: true });
      await mini.screenshot({ path: '/private/tmp/ollama-buddy-mini.png', fullPage: true });
    }
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(errors, []);
    console.log('PASS visible save errors and no JavaScript errors');
    console.log('Browser suite complete.');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => server.exitCode !== null || server.signalCode !== null ? resolve() : server.once('exit', resolve));
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
