(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RatesTexMath = Object.freeze(api);
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var MATHML_NS = "http://www.w3.org/1998/Math/MathML";
  var SYMBOLS = {
    sum: "∑", prod: "∏", int: "∫", partial: "∂", nabla: "∇",
    infty: "∞", cdot: "⋅", times: "×", pm: "±", mp: "∓",
    le: "≤", leq: "≤", ge: "≥", geq: "≥", neq: "≠",
    approx: "≈", sim: "∼", to: "→", rightarrow: "→", leftarrow: "←",
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ϵ",
    theta: "θ", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν",
    pi: "π", rho: "ρ", sigma: "σ", tau: "τ", phi: "φ", omega: "ω",
    Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Pi: "Π",
    Sigma: "Σ", Phi: "Φ", Omega: "Ω"
  };
  var ACCENTS = { bar: "¯", overline: "¯", hat: "^", tilde: "˜" };
  var DELIMITERS = { "{": "{", "}": "}", langle: "⟨", rangle: "⟩", vert: "|" };

  function Parser(source) {
    this.source = source;
    this.index = 0;
  }

  Parser.prototype.peek = function () { return this.source.charAt(this.index); };
  Parser.prototype.take = function () { return this.source.charAt(this.index++); };
  Parser.prototype.skipSpace = function () {
    while (/\s/.test(this.peek())) this.index += 1;
  };
  Parser.prototype.command = function () {
    this.take();
    if (!/[A-Za-z]/.test(this.peek())) return this.take();
    var start = this.index;
    while (/[A-Za-z]/.test(this.peek())) this.index += 1;
    return this.source.slice(start, this.index);
  };
  Parser.prototype.group = function () {
    this.skipSpace();
    if (this.take() !== "{") throw new Error("expected TeX group");
    var value = this.expression("}");
    if (this.take() !== "}") throw new Error("unclosed TeX group");
    return value;
  };
  Parser.prototype.textGroup = function () {
    this.skipSpace();
    if (this.take() !== "{") throw new Error("expected TeX text group");
    var result = "";
    while (this.index < this.source.length && this.peek() !== "}") {
      result += this.take();
    }
    if (this.take() !== "}") throw new Error("unclosed TeX text group");
    return { type: "mtext", text: result };
  };
  Parser.prototype.script = function () {
    this.skipSpace();
    return this.peek() === "{" ? this.group() : this.atom();
  };
  Parser.prototype.atom = function () {
    this.skipSpace();
    var character = this.peek();
    if (!character) throw new Error("missing TeX atom");
    var node;
    if (character === "{") {
      node = this.group();
    } else if (character === "\\") {
      var command = this.command();
      if (command === "frac") {
        node = { type: "mfrac", children: [this.group(), this.group()] };
      } else if (command === "sqrt") {
        node = { type: "msqrt", children: [this.group()] };
      } else if (command === "text" || command === "mathrm" || command === "operatorname") {
        node = this.textGroup();
      } else if (ACCENTS[command]) {
        node = {
          type: "mover",
          children: [this.peek() === "{" ? this.group() : this.atom(), { type: "mo", text: ACCENTS[command] }]
        };
      } else if (command === "left" || command === "right") {
        this.skipSpace();
        var delimiter = this.peek() === "\\" ? this.command() : this.take();
        node = { type: "mo", text: DELIMITERS[delimiter] || delimiter };
      } else if (command === "," || command === ";" || command === ":" || command === "quad" || command === "qquad") {
        node = { type: "mspace", width: command === "qquad" ? "2em" : command === "quad" ? "1em" : ".25em" };
      } else if (command === "!") {
        node = { type: "mspace", width: "-.15em" };
      } else if (SYMBOLS[command]) {
        node = { type: "mo", text: SYMBOLS[command] };
      } else if (DELIMITERS[command]) {
        node = { type: "mo", text: DELIMITERS[command] };
      } else {
        throw new Error("unsupported TeX command");
      }
    } else if (/[0-9.]/.test(character)) {
      var numberStart = this.index;
      while (/[0-9.]/.test(this.peek())) this.index += 1;
      node = { type: "mn", text: this.source.slice(numberStart, this.index) };
    } else if (/[A-Za-z]/.test(character)) {
      node = { type: "mi", text: this.take() };
    } else {
      var operator = this.take();
      if (operator === "-") operator = "−";
      if (operator === "*") operator = "∗";
      node = { type: "mo", text: operator };
    }

    var subscript = null;
    var superscript = null;
    this.skipSpace();
    while (this.peek() === "_" || this.peek() === "^") {
      var marker = this.take();
      var value = this.script();
      if (marker === "_") subscript = value;
      else superscript = value;
      this.skipSpace();
    }
    if (subscript && superscript) return { type: "msubsup", children: [node, subscript, superscript] };
    if (subscript) return { type: "msub", children: [node, subscript] };
    if (superscript) return { type: "msup", children: [node, superscript] };
    return node;
  };
  Parser.prototype.expression = function (stop) {
    var children = [];
    this.skipSpace();
    while (this.index < this.source.length && this.peek() !== stop) {
      children.push(this.atom());
      this.skipSpace();
    }
    return { type: "mrow", children: children };
  };

  function parse(source) {
    if (typeof source !== "string" || !source.trim() || source.length > 2000 || /[\u0000-\u001f<>$]/.test(source)) return null;
    try {
      var parser = new Parser(source.trim());
      var result = parser.expression("");
      return parser.index === parser.source.length && result.children.length ? result : null;
    } catch (_error) {
      return null;
    }
  }

  function mathNode(documentRef, node) {
    var element = documentRef.createElementNS(MATHML_NS, node.type);
    if (node.text !== undefined) element.textContent = node.text;
    if (node.width) element.setAttribute("width", node.width);
    (node.children || []).forEach(function (child) {
      element.appendChild(mathNode(documentRef, child));
    });
    return element;
  }

  function render(source, display, documentRef) {
    var ast = parse(source);
    if (!ast || !documentRef || typeof documentRef.createElementNS !== "function") return null;
    var math = documentRef.createElementNS(MATHML_NS, "math");
    math.setAttribute("display", display ? "block" : "inline");
    math.setAttribute("aria-label", "TeX: " + source.trim());
    math.setAttribute("class", display ? "source-block-math" : "source-inline-math");
    math.appendChild(mathNode(documentRef, ast));
    return math;
  }

  return { parse: parse, render: render };
});
