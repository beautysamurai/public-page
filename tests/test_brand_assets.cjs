const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const site = path.join(__dirname, '..', 'site');
const origin = 'https://beautysamurai.github.io';
const icon = '/public-page/assets/rates-butterfly-icon-v2.png';
const wordmark = '/public-page/assets/rates-butterfly-wordmark-v2.png';
const pages = ['index.html', 'classics/index.html', 'theory/index.html',
  'theory/black-scholes/index.html', 'theory/sabr/index.html',
  'theory/zabr/index.html', 'theory/hjb/index.html', '404.html'];
const documentFor = (file, url) => new JSDOM(fs.readFileSync(path.join(site, file), 'utf8'), { url }).window.document;

test('every page shares a local butterfly favicon and accessible, size-reserved brand image', () => {
  for (const file of pages) {
    const url = origin + '/public-page/' + file;
    const document = documentFor(file, url);
    const icons = document.querySelectorAll('link[rel="icon"]');
    assert.equal(icons.length, 1, file);
    assert.equal(icons[0].href, origin + icon, file);
    const brand = document.querySelector('.brand');
    const image = brand.querySelector('img.brand-logo');
    assert.ok(image, file);
    assert.equal(image.src, origin + icon, file);
    assert.equal(image.getAttribute('alt'), '', 'adjacent live brand text names the decorative icon');
    assert.equal(image.getAttribute('width'), '40');
    assert.equal(image.getAttribute('height'), '40');
    assert.ok(brand.textContent.includes('Rates & Execution'), file);
    for (const asset of document.querySelectorAll('img.brand-logo, img.brand-wordmark, link[rel="icon"]')) {
      const address = new URL(asset.src || asset.href);
      assert.equal(address.origin, origin);
      assert.ok(address.pathname.startsWith('/public-page/assets/'));
      assert.ok(fs.existsSync(path.join(site, address.pathname.slice('/public-page/'.length))), file);
    }
    assert.equal(document.querySelectorAll('.brand-mark, .mark').length, 0, file);
  }
});

test('404 branding resolves at missing nested URLs without falling outside the project', () => {
  for (const url of ['/public-page/missing/', '/public-page/theory/missing/deep/path']) {
    const document = documentFor('404.html', origin + url);
    assert.equal(document.querySelector('img.brand-logo').src, origin + icon);
    assert.equal(document.querySelector('link[rel="icon"]').href, origin + icon);
  }
});

test('footer lockups include readable alternative text, reserved proportions and deferred loading', () => {
  for (const file of ['index.html', 'theory/index.html']) {
    const document = documentFor(file, origin + '/public-page/' + file);
    const image = document.querySelector('.site-footer .brand-wordmark');
    assert.equal(image.src, origin + wordmark);
    assert.equal(image.alt, 'Rates & Execution');
    assert.doesNotMatch(document.querySelector('.footer-brand-lockup').textContent, /DV01|\b(?:5Y|10Y|15Y)\b/);
    assert.equal(Number(image.getAttribute('width')) / Number(image.getAttribute('height')), 2);
    assert.equal(image.getAttribute('loading'), 'lazy');
    assert.ok(document.querySelector('.site-footer [data-i18n="footer.subtitle"]'));
    assert.equal(document.querySelector('meta[property="og:image"]').content, origin + '/public-page/og.png');
  }
});

test('the upload icon is square and safely under one million bytes; wordmark is a wide PNG', () => {
  for (const [name, ratio] of [['rates-butterfly-icon-v2.png', 1], ['rates-butterfly-wordmark-v2.png', 2]]) {
    const bytes = fs.readFileSync(path.join(site, 'assets', name));
    assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    const width = bytes.readUInt32BE(16), height = bytes.readUInt32BE(20);
    assert.equal(width / height, ratio);
    assert.ok(width >= 200 && height >= 200);
    if (ratio === 1) assert.ok(bytes.length < 1000000);
  }
});
