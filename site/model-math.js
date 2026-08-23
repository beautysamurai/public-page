(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RatesModels = Object.freeze(api);
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  function normalCdf(x) {
    var sign = x < 0 ? -1 : 1;
    var value = Math.abs(x) / Math.sqrt(2);
    var t = 1 / (1 + 0.3275911 * value);
    var erf = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-value * value);
    return 0.5 * (1 + sign * erf);
  }

  function normalPdf(x) {
    return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
  }

  function blackScholes(input) {
    var s = Number(input.spot);
    var k = Number(input.strike);
    var t = Number(input.maturity);
    var r = Number(input.rate);
    var q = Number(input.dividend);
    var sigma = Number(input.volatility);
    if (!(s > 0 && k > 0 && t > 0 && sigma > 0)) throw new RangeError("positive-inputs");
    var rootT = Math.sqrt(t);
    var d1 = (Math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * rootT);
    var d2 = d1 - sigma * rootT;
    var spotDiscount = Math.exp(-q * t);
    var strikeDiscount = Math.exp(-r * t);
    return {
      call: s * spotDiscount * normalCdf(d1) - k * strikeDiscount * normalCdf(d2),
      put: k * strikeDiscount * normalCdf(-d2) - s * spotDiscount * normalCdf(-d1),
      callDelta: spotDiscount * normalCdf(d1),
      gamma: spotDiscount * normalPdf(d1) / (s * sigma * rootT),
      vega: s * spotDiscount * normalPdf(d1) * rootT,
      d1: d1,
      d2: d2
    };
  }

  function blackForwardCall(forward, strike, maturity, volatility) {
    if (!(forward > 0 && strike > 0 && maturity > 0 && volatility > 0)) throw new RangeError("positive-inputs");
    var stdDev = volatility * Math.sqrt(maturity);
    var d1 = Math.log(forward / strike) / stdDev + 0.5 * stdDev;
    var d2 = d1 - stdDev;
    return forward * normalCdf(d1) - strike * normalCdf(d2);
  }

  function sabrVolatility(input) {
    var forward = Number(input.forward) + Number(input.shift || 0);
    var strike = Number(input.strike) + Number(input.shift || 0);
    var maturity = Number(input.maturity);
    var alpha = Number(input.alpha);
    var beta = Number(input.beta);
    var rho = Number(input.rho);
    var nu = Number(input.nu);
    if (!(forward > 0 && strike > 0 && maturity > 0 && alpha > 0 && nu >= 0 && beta >= 0 && beta <= 1 && Math.abs(rho) < 1)) throw new RangeError("sabr-inputs");
    var oneMinusBeta = 1 - beta;
    var logMoneyness = Math.log(forward / strike);
    var geometric = Math.pow(forward * strike, 0.5 * oneMinusBeta);
    var z = nu === 0 ? 0 : (nu / alpha) * geometric * logMoneyness;
    var zOverX = 1;
    if (Math.abs(z) > 1e-7) {
      var radical = Math.sqrt(1 - 2 * rho * z + z * z);
      var x = Math.log((radical + z - rho) / (1 - rho));
      zOverX = z / x;
    } else {
      zOverX = 1 - 0.5 * rho * z + (2 - 3 * rho * rho) * z * z / 12;
    }
    var logSquared = logMoneyness * logMoneyness;
    var denominator = geometric * (1 + oneMinusBeta * oneMinusBeta * logSquared / 24 + Math.pow(oneMinusBeta, 4) * logSquared * logSquared / 1920);
    var correction = 1 + maturity * (
      oneMinusBeta * oneMinusBeta * alpha * alpha / (24 * geometric * geometric) +
      rho * beta * nu * alpha / (4 * geometric) +
      (2 - 3 * rho * rho) * nu * nu / 24
    );
    return alpha / denominator * zOverX * correction;
  }

  function hjbQuotes(input) {
    var mid = Number(input.mid);
    var inventory = Number(input.inventory);
    var remaining = Number(input.remaining);
    var sigma = Number(input.sigma);
    var gamma = Number(input.gamma);
    var kappa = Number(input.kappa);
    if (!(mid > 0 && remaining >= 0 && sigma >= 0 && gamma > 0 && kappa > 0)) throw new RangeError("hjb-inputs");
    var riskTerm = gamma * sigma * sigma * remaining;
    var reservation = mid - inventory * riskTerm;
    var spread = riskTerm + 2 / gamma * Math.log(1 + gamma / kappa);
    return { reservation: reservation, spread: spread, bid: reservation - spread / 2, ask: reservation + spread / 2 };
  }

  function zabrDerivative(y, u, nu, rho, gamma) {
    var g2 = gamma - 2;
    var A = 1 + g2 * g2 * nu * nu * y * y + 2 * rho * g2 * nu * y;
    var B = 2 * rho * (1 - gamma) * nu + 2 * (1 - gamma) * g2 * nu * nu * y;
    var C = (1 - gamma) * (1 - gamma) * nu * nu;
    var discriminant = Math.max(0, B * B * u * u - 4 * A * (C * u * u - 1));
    return (-B * u + Math.sqrt(discriminant)) / (2 * A);
  }

  function zabrVolatility(input) {
    var forward = Number(input.forward);
    var strike = Number(input.strike);
    var alpha = Number(input.alpha);
    var beta = Number(input.beta);
    var rho = Number(input.rho);
    var rawNu = Number(input.nu);
    var gamma = Number(input.gamma);
    if (!(forward > 0 && strike > 0 && alpha > 0 && rawNu >= 0 && beta >= 0 && beta <= 1 && Math.abs(rho) < 1 && gamma >= 0)) throw new RangeError("zabr-inputs");
    if (Math.abs(forward - strike) < 1e-12) return alpha * Math.pow(forward, beta - 1);
    var nu = rawNu * Math.pow(alpha, 1 - gamma);
    var y;
    if (Math.abs(beta - 1) < 1e-10) y = Math.log(forward / strike) * Math.pow(alpha, gamma - 2);
    else y = (Math.pow(forward, 1 - beta) - Math.pow(strike, 1 - beta)) * Math.pow(alpha, gamma - 2) / (1 - beta);
    var x;
    if (rawNu < 1e-12) {
      x = y * Math.pow(alpha, 1 - gamma);
    } else if (Math.abs(gamma - 1) < 1e-10) {
      var radical = Math.sqrt(1 + nu * nu * y * y - 2 * rho * nu * y);
      x = Math.log((radical + nu * y - rho) / (1 - rho)) / nu;
    } else {
      var steps = Math.max(80, Math.ceil(Math.abs(y) * 40));
      var h = y / steps;
      var u = 0;
      var position = 0;
      for (var index = 0; index < steps; index += 1) {
        var k1 = zabrDerivative(position, u, nu, rho, gamma);
        var k2 = zabrDerivative(position + h / 2, u + h * k1 / 2, nu, rho, gamma);
        var k3 = zabrDerivative(position + h / 2, u + h * k2 / 2, nu, rho, gamma);
        var k4 = zabrDerivative(position + h, u + h * k3, nu, rho, gamma);
        u += h * (k1 + 2 * k2 + 2 * k3 + k4) / 6;
        position += h;
      }
      x = u * Math.pow(alpha, 1 - gamma);
    }
    var result = Math.log(forward / strike) / x;
    if (!(result > 0 && Number.isFinite(result))) throw new RangeError("zabr-result");
    return result;
  }

  return {
    normalCdf: normalCdf,
    blackScholes: blackScholes,
    blackForwardCall: blackForwardCall,
    sabrVolatility: sabrVolatility,
    hjbQuotes: hjbQuotes,
    zabrVolatility: zabrVolatility
  };
});
