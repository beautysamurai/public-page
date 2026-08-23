(function () {
  "use strict";

  var root = document.querySelector("[data-simulator]");
  var models = window.RatesModels;
  var i18n = window.RatesI18n;
  if (!root || !models) return;

  var language = new URLSearchParams(window.location.search).get("lang") === "en" ? "en" : "ja";
  var form = root.querySelector("[data-simulator-form]");
  var canvas = root.querySelector("canvas");
  var error = root.querySelector("[data-simulator-error]");

  function t(key) {
    var catalog = i18n && i18n.copy && i18n.copy[language];
    var fallback = i18n && i18n.copy && i18n.copy.ja;
    return String((catalog && catalog[key]) || (fallback && fallback[key]) || key);
  }

  function values() {
    var result = {};
    form.querySelectorAll("[data-model-input]").forEach(function (input) {
      result[input.name] = Number(input.value);
    });
    return result;
  }

  function format(value, digits) {
    return new Intl.NumberFormat(language === "en" ? "en-US" : "ja-JP", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    }).format(value);
  }

  function setOutput(name, value, digits) {
    var node = root.querySelector('[data-output="' + name + '"]');
    if (node) node.textContent = format(value, digits);
  }

  function drawChart(series, currentX, options) {
    options = options || {};
    var rect = canvas.getBoundingClientRect();
    var scale = Math.max(1, window.devicePixelRatio || 1);
    var width = Math.max(320, Math.round(rect.width));
    var height = Math.max(260, Math.round(rect.height));
    canvas.width = width * scale;
    canvas.height = height * scale;
    var context = canvas.getContext("2d");
    context.scale(scale, scale);
    var pad = { top: 24, right: 24, bottom: 42, left: 58 };
    var plotWidth = width - pad.left - pad.right;
    var plotHeight = height - pad.top - pad.bottom;
    var all = series.reduce(function (memo, item) { return memo.concat(item.points.map(function (p) { return p.y; })); }, []);
    var xMin = series[0].points[0].x;
    var xMax = series[0].points[series[0].points.length - 1].x;
    var rawMin = Math.min.apply(null, all);
    var rawMax = Math.max.apply(null, all);
    var yMin = options.zeroBase === false ? rawMin - Math.max(1e-9, (rawMax - rawMin) * 0.12) : 0;
    var yMax = rawMax + Math.max(1e-9, (rawMax - yMin) * 0.08);
    function x(value) { return pad.left + (value - xMin) / (xMax - xMin) * plotWidth; }
    function y(value) { return pad.top + plotHeight - (value - yMin) / (yMax - yMin) * plotHeight; }

    context.clearRect(0, 0, width, height);
    context.strokeStyle = "#d2cdc1";
    context.fillStyle = "#3c4b54";
    context.lineWidth = 1;
    context.font = '11px "SFMono-Regular", Consolas, monospace';
    context.textAlign = "right";
    for (var tick = 0; tick <= 4; tick += 1) {
      var value = yMin + (yMax - yMin) * tick / 4;
      var py = y(value);
      context.beginPath(); context.moveTo(pad.left, py); context.lineTo(width - pad.right, py); context.stroke();
      context.fillText(format(value, options.yDigits === undefined ? 1 : options.yDigits), pad.left - 9, py + 4);
    }
    context.textAlign = "center";
    for (var xt = 0; xt <= 4; xt += 1) {
      var xv = xMin + (xMax - xMin) * xt / 4;
      context.fillText(format(xv, options.xDigits === undefined ? 0 : options.xDigits), x(xv), height - 15);
    }
    context.setLineDash([4, 5]);
    context.strokeStyle = "#a8a397";
    context.beginPath(); context.moveTo(x(currentX), pad.top); context.lineTo(x(currentX), pad.top + plotHeight); context.stroke();
    context.setLineDash([]);
    series.forEach(function (item) {
      context.strokeStyle = item.color;
      context.lineWidth = 2.5;
      context.beginPath();
      item.points.forEach(function (point, index) {
        if (index === 0) context.moveTo(x(point.x), y(point.y));
        else context.lineTo(x(point.x), y(point.y));
      });
      context.stroke();
    });
  }

  function calculateBlackScholes(input) {
    var result = models.blackScholes(input);
    setOutput("call", result.call, 4);
    setOutput("put", result.put, 4);
    setOutput("delta", result.callDelta, 4);
    setOutput("gamma", result.gamma, 5);
    var low = Math.max(0.01, Math.min(input.spot, input.strike) * 0.35);
    var high = Math.max(input.spot, input.strike) * 1.75;
    var calls = [];
    var puts = [];
    for (var index = 0; index <= 80; index += 1) {
      var spot = low + (high - low) * index / 80;
      var point = models.blackScholes(Object.assign({}, input, { spot: spot }));
      calls.push({ x: spot, y: point.call });
      puts.push({ x: spot, y: point.put });
    }
    drawChart([
      { points: calls, color: "#287965" },
      { points: puts, color: "#bc6848" }
    ], input.spot);
  }

  function smileRange(forward) {
    return { low: Math.max(0.0001, forward * 0.35), high: forward * 1.85 };
  }

  function calculateSabr(input) {
    var volatility = models.sabrVolatility(input);
    setOutput("volatility", volatility * 100, 3);
    setOutput("call", models.blackForwardCall(input.forward + input.shift, input.strike + input.shift, input.maturity, volatility), 5);
    var atm = models.sabrVolatility(Object.assign({}, input, { strike: input.forward }));
    setOutput("atm", atm * 100, 3);
    var range = smileRange(input.forward + input.shift);
    var points = [];
    for (var index = 0; index <= 90; index += 1) {
      var shiftedStrike = range.low + (range.high - range.low) * index / 90;
      var strike = shiftedStrike - input.shift;
      points.push({ x: strike, y: models.sabrVolatility(Object.assign({}, input, { strike: strike })) * 100 });
    }
    drawChart([{ points: points, color: "#287965" }], input.strike, { xDigits: 3, yDigits: 1, zeroBase: false });
  }

  function calculateHjb(input) {
    var result = models.hjbQuotes(input);
    setOutput("reservation", result.reservation, 4);
    setOutput("bid", result.bid, 4);
    setOutput("ask", result.ask, 4);
    setOutput("spread", result.spread, 4);
    var bid = [], reservation = [], ask = [];
    for (var inventory = -10; inventory <= 10; inventory += 1) {
      var point = models.hjbQuotes(Object.assign({}, input, { inventory: inventory }));
      bid.push({ x: inventory, y: point.bid });
      reservation.push({ x: inventory, y: point.reservation });
      ask.push({ x: inventory, y: point.ask });
    }
    drawChart([
      { points: bid, color: "#287965" },
      { points: reservation, color: "#b77b2f" },
      { points: ask, color: "#bc6848" }
    ], input.inventory, { xDigits: 0, yDigits: 2, zeroBase: false });
  }

  function calculateZabr(input) {
    var volatility = models.zabrVolatility(input);
    var atm = models.zabrVolatility(Object.assign({}, input, { strike: input.forward }));
    setOutput("volatility", volatility * 100, 3);
    setOutput("atm", atm * 100, 3);
    setOutput("ratio", volatility / atm, 3);
    var range = smileRange(input.forward);
    var points = [];
    for (var index = 0; index <= 90; index += 1) {
      var strike = range.low + (range.high - range.low) * index / 90;
      points.push({ x: strike, y: models.zabrVolatility(Object.assign({}, input, { strike: strike })) * 100 });
    }
    drawChart([{ points: points, color: "#bc6848" }], input.strike, { xDigits: 3, yDigits: 1, zeroBase: false });
  }

  function update() {
    try {
      error.hidden = true;
      var input = values();
      if (root.dataset.simulator === "black-scholes") calculateBlackScholes(input);
      else if (root.dataset.simulator === "sabr") calculateSabr(input);
      else if (root.dataset.simulator === "hjb") calculateHjb(input);
      else if (root.dataset.simulator === "zabr") calculateZabr(input);
    } catch (reason) {
      error.textContent = t("sim.error");
      error.hidden = false;
    }
  }

  form.addEventListener("input", update);
  form.addEventListener("reset", function () { window.setTimeout(update, 0); });
  window.addEventListener("resize", update);
  update();
})();
