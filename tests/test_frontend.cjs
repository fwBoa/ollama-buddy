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
    env: { ...process.env, OLLAMA_BUDDY_DATA: temporary, PYTHONDONTWRITEBYTECODE: '1', OLLAMA_BUDDY_TEST_FALLBACK:'1' },
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
    assert.equal(payload.daily_days[0].day,fixture.anchor.slice(0,10));
    assert.equal(await dashboard.locator('[data-chart-range="cycle"]').getAttribute('aria-pressed'),'true');
    assert.equal(await dashboard.locator('.chart-day').count(),payload.daily_days.length);
    assert.match(await dashboard.locator('#sub').innerText(),/Since the configured cycle start/);
    assert.equal(JSON.stringify(payload).includes('fixture-secret'), false);
    assert.equal(JSON.stringify(payload).includes('never-expose@example.test'), false);
    console.log('PASS historical models removed, CSP and secret minimisation');
    await dashboard.setViewportSize({ width: 1120, height: 880 });
    await dashboard.locator('[data-chart-range="30"]').click();
    assert.equal(await dashboard.locator('.chart-day').count(), payload.daily_days.length);
    const firstDay = payload.daily_days[0];
    await dashboard.locator(`[data-select-day="${firstDay.day}"]`).click();
    assert.match(await dashboard.locator('#chart-detail').innerText(), /requests?/);
    assert.equal(await dashboard.locator(`[data-select-day="${firstDay.day}"]`).getAttribute('aria-pressed'), 'true');
    await dashboard.locator('.nav [data-view="activity"]').click();
    assert.equal(await dashboard.locator('.ledger tbody tr').count(), payload.daily_days.length);
    assert.equal(await dashboard.locator('#main').evaluate(el=>getComputedStyle(el).outlineStyle), 'none');
    const input30 = payload.daily_days.reduce((sum,day)=>sum+day.input_tokens,0);
    assert.equal(await dashboard.locator('[data-token="input_tokens"]').getAttribute('title'), input30.toLocaleString('en-US'));
    const cached30 = payload.daily_days.reduce((sum,day)=>sum+day.cached_input_tokens,0);
    assert.equal(await dashboard.locator('[data-token="cached_input_tokens"]').getAttribute('title'), cached30.toLocaleString('en-US'));
    await dashboard.locator('[data-chart-range="7"]').click();
    assert.equal(await dashboard.locator('[data-token="input_tokens"]').getAttribute('title'), payload.daily_days.slice(-7).reduce((sum,day)=>sum+day.input_tokens,0).toLocaleString('en-US'));
    await dashboard.evaluate(data=>applyData({...data,daily_days:data.daily_days.map(day=>({...day,cached_input_tokens:null}))}),payload);
    assert.equal(await dashboard.locator('[data-token="cached_input_tokens"]').innerText(),'…');
    assert.match(await dashboard.locator('.tokens').innerText(),/Missing values are not zero/);
    await dashboard.evaluate(data=>applyData(data),payload);
    console.log('PASS token counters, cache subset, selected range and unavailable values');
    await dashboard.locator('.nav [data-view="overview"]').click();
    const official = {...payload,quota:{...payload.quota,measurement:'balance',source:'api',monthly:60,current:12.34,remaining:47.66,purchased:7.5,ratio:12.34/60,resets_source:'balance',error:null}};
    official.activity_start_source = 'balance';
    await dashboard.evaluate(data=>applyData(data),official);
    assert.match(await dashboard.locator('#sub').innerText(),/Since renewal/);
    assert.match(await dashboard.locator('.quota').innerText(),/Monthly credits/);
    assert.match(await dashboard.locator('.quota').innerText(),/Purchased credits[\s\S]*7.50/);
    assert.match(await dashboard.locator('#hero').innerText(),/12.34/);
    await mini.evaluate(data=>render(data),official);
    assert.match(await mini.locator('#body').innerText(),/Monthly credits/);
    await dashboard.evaluate(data=>applyData({...data,usage_available:false,usage_error:'HTTP 503'}),official);
    assert.match(await dashboard.locator('#app').innerText(),/Daily spending unavailable/);
    assert.equal(await dashboard.locator('.quota .meter').count(),1);
    assert.equal(await dashboard.locator('#api-key').count(),0);
    const legacy = {...official,quota:{...official.quota,ratio:null,current:null,monthly:0,error:'legacy plan',legacy_limits:{session:{remaining_percent:75,resets_at:official.quota.resets_at},weekly:{remaining_percent:40,resets_at:official.quota.resets_at}}}};
    await dashboard.evaluate(data=>applyData(data),legacy);
    assert.match(await dashboard.locator('.quota').innerText(),/Legacy plan/);
    assert.match(await dashboard.locator('.quota').innerText(),/75.0%/);
    assert.equal(await dashboard.locator('.quota .meter').count(),0);
    await dashboard.evaluate(data=>applyData(data),payload);
    await mini.evaluate(data=>render(data),payload);
    console.log('PASS official credits in both views, purchased balance, independent outage and legacy plans');
    await dashboard.locator('.nav [data-view="activity"]').click();
    const cycleStart = Date.parse(fixture.today+'T00:00:00Z')-30*86400000;
    const cycleDays = Array.from({length:31},(_,i)=>({day:new Date(cycleStart+i*86400000).toISOString().slice(0,10),usd:1,requests:1,input_tokens:10,output_tokens:2,cached_input_tokens:3}));
    await dashboard.evaluate(({data,cycleStart,cycleDays})=>applyData({...data,activity_start:cycleStart,activity_start_source:'balance',daily_days:cycleDays}),{data:official,cycleStart,cycleDays});
    await dashboard.locator('[data-chart-range="cycle"]').click();
    assert.equal(await dashboard.locator('.chart-day').count(),31);
    assert.equal(await dashboard.locator('[data-token="input_tokens"]').getAttribute('title'),'310');
    await dashboard.evaluate(()=>applyData({...lastData,activity_start:lastData.activity_start+12*3600000,activity_partial_day:lastData.daily_days[0].day}));
    assert.match(await dashboard.locator('#renewal-note').innerText(),/may include usage before renewal/);
    await dashboard.locator('[data-chart-range="30"]').click();
    assert.equal(await dashboard.locator('.chart-day').count(),30);
    assert.equal(await dashboard.locator('[data-token="input_tokens"]').getAttribute('title'),'300');
    await dashboard.evaluate(data=>applyData(data),payload);
    await dashboard.locator('.nav [data-view="overview"]').click();
    console.log('PASS renewal window, full 31-day cycle, matching token period and partial-day note');
    await dashboard.locator('[data-chart-range="7"]').click();
    for (const width of [360, 680, 880, 1120]) {
      await dashboard.setViewportSize({ width, height: 880 });
      assert.equal(await dashboard.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `overflow at ${width}`);
    }
    await dashboard.setViewportSize({ width: 1120, height: 880 });
    // Keyboard activation, independent of pointer interactions.
    await dashboard.locator('.nav [data-view="settings"]').focus();
    await dashboard.keyboard.press('Enter');
    await dashboard.locator('#monthly').waitFor({ state: 'visible' });
    await dashboard.locator('.nav [data-view="overview"]').click();
    if (process.env.OLLAMA_BUDDY_QA) {
      await dashboard.screenshot({ path: '/private/tmp/ollama-buddy-overview.png', fullPage: true });
      const previewContext = await browser.newContext({ locale: 'fr-FR', timezoneId: 'Europe/Paris', viewport: {width:1120,height:880} });
      const preview = await previewContext.newPage();
      await preview.goto(fixture.url);
      await preview.locator('.quota .meter').waitFor();
      await preview.screenshot({path:'/private/tmp/ollama-buddy-preview.png',fullPage:true});
      await previewContext.close();
      await mini.setViewportSize({ width: 300, height: 520 });
      await mini.locator('body').screenshot({ path: '/private/tmp/ollama-buddy-mini.png' });
    }
    console.log('PASS chart selection, full activity, keyboard navigation and responsive layout');
    const gapDay = payload.daily_days[payload.daily_days.length - 3].day;
    await dashboard.evaluate(day => applyData({ ...lastData, days: lastData.days.filter(d => d.day !== day), daily_days: lastData.daily_days.filter(d => d.day !== day),
      quota: { ...lastData.quota, source: 'unavailable', error: 'incomplete history', ratio: null } }), gapDay);
    assert.match(await dashboard.locator('.quota').innerText(), /History incomplete/);
    await dashboard.locator(`[data-select-day="${gapDay}"]`).click();
    assert.match(await dashboard.locator('#chart-detail').innerText(), /No record for this day/);
    assert.equal(await dashboard.locator('.chart-day.missing').count(), 1);
    await dashboard.evaluate(data => applyData(data), payload);
    await dashboard.evaluate(() => applyData({ ...lastData, days: [], daily_days: [], usd: 0, requests: 0, peak: 0 }));
    assert.match(await dashboard.locator('#app').innerText(), /No daily records yet/);
    await dashboard.locator('.nav [data-view="activity"]').click();
    assert.equal((await dashboard.locator('#app').innerText()).match(/No daily records yet/g).length, 1);
    assert.equal(await dashboard.locator('[data-chart-range="7"]').isDisabled(), true);
    if (process.env.OLLAMA_BUDDY_QA) {
      await dashboard.evaluate(() => { applyLang('fr'); render(lastData); });
      await dashboard.screenshot({ path: '/private/tmp/ollama-buddy-empty-fixed.png', fullPage: true });
      await dashboard.evaluate(() => { applyLang('en'); render(lastData); });
    }

    await dashboard.locator('.nav [data-view="overview"]').click();

    await dashboard.evaluate(data => applyData({ ...data, usage_available: false,
      quota: { ...data.quota, source: 'unavailable', key_set: false } }), payload);
    await dashboard.locator('#api-key').waitFor({ state: 'visible' });
    assert.equal(await dashboard.locator('#hero').count(), 0);
    await dashboard.evaluate(data => applyData({ ...data, usage_available: false,
      quota: { ...data.quota, source: 'unavailable', key_error: '<img src=x onerror="window.injected=true">' } }), payload);
    assert.equal(await dashboard.locator('.connect img').count(), 0);
    assert.equal(await dashboard.evaluate(() => window.injected), undefined);
    await dashboard.evaluate(data => applyData({ ...data, quota: { ...data.quota, ratio: 1.1 } }), payload);
    assert.match(await dashboard.locator('.quota').innerText(), /Cap reached/);
    await dashboard.evaluate(data => applyData(data), payload);
    // A stream update also keeps keyboard focus on the inspected bar.
    await dashboard.locator(`[data-select-day="${payload.days.at(-1).day}"]`).focus();
    await dashboard.evaluate(() => applyData({ ...lastData, requests: lastData.requests + 1 }));
    assert.equal(await dashboard.evaluate(() => document.activeElement.dataset.selectDay), payload.days.at(-1).day);
    await dashboard.evaluate(data => applyData(data), payload);
    console.log('PASS missing records, empty history, onboarding, escaped errors and exceeded quota');
    await dashboard.evaluate(data => applyData({ ...data, days: [], usd: 0, requests: 0,
      daily_days: [{day: new Date().toISOString().slice(0,10),usd:.25,requests:7}],
      quota: {...data.quota,source:'indisponible',error:'partial reset day',ratio:null,current:null} }), payload);
    assert.match(await dashboard.locator('#hero').innerText(), /0.25/);
    assert.match(await dashboard.locator('#chart-detail').innerText(), /7 requests/);
    assert.match(await dashboard.locator('.quota').innerText(), /Reset day/);
    await dashboard.evaluate(() => applyData({...lastData,daily_days:[{...lastData.daily_days[0],usd:.5,requests:8}]}));
    assert.match(await dashboard.locator('#hero').innerText(), /0.50/);
    assert.match(await dashboard.locator('#chart-detail').innerText(), /8 requests/);
    await dashboard.evaluate(data => applyData(data),payload);
    console.log('PASS daily spending updates during the partial reset day');



    await dashboard.locator('.nav [data-view="settings"]').click();
    await dashboard.locator('#theme-select').selectOption('light');
    await mini.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await dashboard.locator('#lang-select').selectOption('fr');
    await dashboard.waitForFunction(() => document.documentElement.lang === 'fr');
    await mini.waitForFunction(() => document.documentElement.lang === 'fr');
    console.log('PASS theme and language synchronisation');
    // A draft survives leaving the field, moving to another view and receiving a push.
    await dashboard.locator('#monthly').fill('81.5');
    await dashboard.locator('#monthly').press('Tab');
    await dashboard.locator('.nav [data-view="overview"]').click();
    await dashboard.evaluate(() => applyData({ ...lastData, requests: lastData.requests + 1 }));
    await dashboard.locator('.nav [data-view="settings"]').click();
    assert.equal(await dashboard.locator('#monthly').inputValue(), '81.5');
    await dashboard.locator('#monthly').fill('');
    await dashboard.evaluate(() => drafts.delete('monthly'));
    if (process.env.OLLAMA_BUDDY_QA) {
      await dashboard.locator('#theme-select').selectOption('dark');
      await dashboard.locator('.nav [data-view="overview"]').click();
      await dashboard.screenshot({ path: '/private/tmp/ollama-buddy-dark.png', fullPage: true });
      await dashboard.setViewportSize({ width: 360, height: 800 });
      await dashboard.screenshot({ path: '/private/tmp/ollama-buddy-mobile.png', fullPage: true });
      await dashboard.setViewportSize({ width: 1120, height: 880 });
      await dashboard.locator('.nav [data-view="settings"]').click();
      await dashboard.locator('#theme-select').selectOption('light');
    }

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

    await dashboard.locator('.nav [data-view="settings"]').click();
    await dashboard.locator('#monthly').fill('75');
    await context.request.post(fixture.url + '/api/theme', { data: { theme: 'dark' } });
    await dashboard.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    assert.equal(await dashboard.locator('#monthly').inputValue(), '75');
    assert.equal(await dashboard.locator('.nav [data-view="settings"]').getAttribute('aria-current'), 'page');
    await dashboard.locator('#save-monthly').click();
    await dashboard.waitForFunction(() => lastData.quota.monthly === 75);
    console.log('PASS live updates preserve active input and settings');

    const utcInput = new Date(Date.parse(fixture.anchor) + 3600000).toISOString().slice(0, 16);
    await dashboard.locator('#reset-at').fill(utcInput);
    await dashboard.locator('#monthly').focus();
    const expected = Date.parse(utcInput + 'Z');
    await dashboard.waitForFunction(value => lastData.quota.resets_set === value, expected);
    assert.equal(await dashboard.locator('#reset-at').inputValue(), utcInput);
    assert.match(await dashboard.locator('#reset-hint-local').innerText(), /Prochaine réinitialisation ici/);
    assert.match(await dashboard.locator('#reset-hint').innerText(), /UTC, pas en heure locale/);
    const todayLabel = await dashboard.evaluate(day => dayLabel(day), fixture.today);
    assert.match(todayLabel, new RegExp(String(Number(fixture.today.slice(-2)))));
    await dashboard.locator('#reset-at').fill('');
    await dashboard.locator('#monthly').focus();
    await dashboard.waitForFunction(() => lastData.quota.resets_set === 0);
    await dashboard.locator('.nav [data-view="overview"]').click();
    assert.match(await dashboard.locator('#app').innerText(), /Aucun cycle/);
    assert.equal(await dashboard.locator('.quota .meter').count(), 0);
    assert.match(await dashboard.locator('#app').innerText(), /Dépense disponible/);
    assert.equal(await dashboard.locator('.model.day').count(), 4);
    await mini.waitForFunction(() => document.getElementById('body').textContent.includes('Dépense disponible'));
    console.log('PASS UTC date roundtrip, clearing the cycle and spending without quota');

    await context.request.post(fixture.url + '/api/quota', { data: { quota_reset_at: expected } });
    await dashboard.locator('.quota .meter').waitFor();
    await dashboard.locator('.nav [data-view="settings"]').click();
    await dashboard.locator('#remove-key').click();
    assert.equal(await dashboard.evaluate(() => lastData.quota.key_set), true);
    await dashboard.locator('#confirm-disconnect').click();
    await dashboard.waitForFunction(() => lastData.quota.key_set === false);
    assert.equal(await dashboard.locator('.quota .meter').count(), 0);
    await dashboard.locator('#api-key').fill('replacement-key');
    await dashboard.locator('#save-key').click();
    await dashboard.waitForFunction(() => lastData.quota.key_set === true);
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
      await mini.locator('body').screenshot({ path: '/private/tmp/ollama-buddy-mini.png' });
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
