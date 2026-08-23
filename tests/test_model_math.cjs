"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const models = require("../site/model-math.js");

test("Black-Scholes matches a standard reference value and parity", () => {
  const input = { spot: 100, strike: 100, maturity: 1, rate: 0.05, dividend: 0, volatility: 0.2 };
  const result = models.blackScholes(input);
  assert.ok(Math.abs(result.call - 10.4506) < 1e-4);
  assert.ok(Math.abs(result.put - 5.5735) < 1e-4);
  assert.ok(Math.abs(result.call - result.put - (100 - 100 * Math.exp(-0.05))) < 1e-9);
});

test("SABR reduces to constant volatility when beta is one and nu is zero", () => {
  for (const strike of [0.02, 0.03, 0.05]) {
    const volatility = models.sabrVolatility({ forward: 0.03, strike, maturity: 5, alpha: 0.2, beta: 1, rho: 0, nu: 0, shift: 0 });
    assert.ok(Math.abs(volatility - 0.2) < 1e-12);
  }
});

test("HJB quotes are centered on reservation price and respond to inventory", () => {
  const flat = models.hjbQuotes({ mid: 100, inventory: 0, remaining: 1, sigma: 2, gamma: 0.1, kappa: 1.5 });
  const long = models.hjbQuotes({ mid: 100, inventory: 2, remaining: 1, sigma: 2, gamma: 0.1, kappa: 1.5 });
  assert.equal(flat.reservation, 100);
  assert.ok(long.reservation < flat.reservation);
  assert.ok(Math.abs((long.bid + long.ask) / 2 - long.reservation) < 1e-12);
  assert.ok(long.bid < long.ask);
});

test("ZABR gamma one agrees with the SABR short-maturity limit", () => {
  const base = { forward: 0.03, strike: 0.02, alpha: 0.08, beta: 0.7, rho: -0.3, nu: 0.2 };
  const zabr = models.zabrVolatility({ ...base, gamma: 1 });
  const sabr = models.sabrVolatility({ ...base, maturity: 1e-9, shift: 0 });
  assert.ok(Math.abs(zabr - sabr) < 2e-5);
});
