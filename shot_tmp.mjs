import { chromium } from 'playwright';
const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
const pg = await b.newPage({ viewport: { width: 1440, height: 1200 } });
await pg.goto('http://127.0.0.1:5057/settings', { waitUntil: 'networkidle' });
const panel = await pg.$('.workbuddy-settings-panel');
if (panel) await panel.scrollIntoViewIfNeeded();
await pg.screenshot({ path: '/tmp/ys_settings.png', fullPage: false });
console.log('SHOT_OK');
await b.close();
