const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const path = require('node:path');
const fs = require('node:fs');
(async () => {
  const [,, htmlPath, outDir, prefix = 'slide'] = process.argv;
  if (!htmlPath || !outDir) { console.error('usage: node render.cjs <html> <outDir> [prefix]'); process.exit(1); }
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.resolve(htmlPath), { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
  const slides = await page.$$('.slide');
  let i = 0;
  for (const s of slides) {
    i++;
    const box = await s.boundingBox();
    const file = path.join(outDir, `${prefix}-${String(i).padStart(2, '0')}.png`);
    await s.screenshot({ path: file, type: 'png' });
    console.log(`${file} ${Math.round(box.width)}x${Math.round(box.height)}`);
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
