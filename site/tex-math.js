(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RatesTexMath = Object.freeze(api);
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var MATHML_NS = "http://www.w3.org/1998/Math/MathML";
  var OPERATORS = {
    sum: "∑", prod: "∏", int: "∫", cdot: "⋅", times: "×", pm: "±", mp: "∓",
    le: "≤", leq: "≤", ge: "≥", geq: "≥", neq: "≠", lt: "<", gt: ">",
    approx: "≈", sim: "∼", to: "→", rightarrow: "→", leftarrow: "←",
    in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", cup: "∪", cap: "∩"
  };
  // Identifiers must be mi, not mo: operator spacing separates Greek variables
  // from adjacent factors and even inserts gaps inside function arguments.
  var IDENTIFIERS = {
    partial: "∂", nabla: "∇", infty: "∞", ell: "ℓ",
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ϵ",
    varepsilon: "ε", zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι",
    kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", omicron: "ο",
    pi: "π", varpi: "ϖ", rho: "ρ", varrho: "ϱ", sigma: "σ", varsigma: "ς",
    tau: "τ", upsilon: "υ", phi: "φ", varphi: "ϕ", chi: "χ", psi: "ψ", omega: "ω",
    Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Pi: "Π",
    Xi: "Ξ", Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω"
  };
  var ACCENTS = { bar: "¯", overline: "¯", hat: "^", tilde: "˜" };
  var DELIMITERS = {
    "{": "{", "}": "}", "(": "(", ")": ")", "[": "[", "]": "]",
    langle: "⟨", rangle: "⟩", vert: "|", lvert: "|", rvert: "|",
    "|": "‖", Vert: "‖", lVert: "‖", rVert: "‖", ".": ""
  };

  function upright(node) {
    if (node.type === "mi") node.variant = "normal";
    (node.children || []).forEach(upright);
    return node;
  }

  function Parser(source) {
    this.source = source;
    this.index = 0;
    this.depth = 0;
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
    if (++this.depth > 64) throw new Error("TeX nesting limit");
    this.skipSpace();
    if (this.take() !== "{") throw new Error("expected TeX group");
    var value = this.expression("}");
    if (this.take() !== "}") throw new Error("unclosed TeX group");
    this.depth -= 1;
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
    return this.peek() === "{" ? this.group() : this.atom(false, true);
  };
  Parser.prototype.atom = function (withScripts, singleToken) {
    this.skipSpace();
    var character = this.peek();
    if (!character || /[}_^]/.test(character)) throw new Error("missing TeX atom");
    var node;
    if (character === "{") {
      node = this.group();
    } else if (character === "\\") {
      var command = this.command();
      if (command === "frac") {
        node = { type: "mfrac", children: [this.group(), this.group()] };
      } else if (command === "sqrt") {
        node = { type: "msqrt", children: [this.group()] };
      } else if (command === "text" || command === "operatorname") {
        node = this.textGroup();
        if (command === "operatorname") node.type = "mo";
      } else if (command === "mathrm") {
        node = upright(this.group());
      } else if (command === "rm") {
        node = upright(this.expression("}"));
      } else if (typeof ACCENTS[command] === "string") {
        node = {
          type: "mover",
          children: [this.script(), { type: "mo", text: ACCENTS[command] }]
        };
      } else if (command === "left" || command === "right") {
        this.skipSpace();
        var escaped = this.peek() === "\\";
        var delimiter = escaped ? this.command() : this.take();
        if (typeof DELIMITERS[delimiter] !== "string") throw new Error("unsupported delimiter");
        node = { type: "mo", text: !escaped && delimiter === "|" ? "|" : DELIMITERS[delimiter], stretchy: true };
      } else if (command === "," || command === ";" || command === ":" || command === "quad" || command === "qquad") {
        node = { type: "mspace", width: command === "qquad" ? "2em" : command === "quad" ? "1em" : ".25em" };
      } else if (command === "!") {
        node = { type: "mspace", width: "-.15em" };
      } else if (typeof IDENTIFIERS[command] === "string") {
        node = { type: "mi", text: IDENTIFIERS[command] };
      } else if (typeof OPERATORS[command] === "string") {
        node = { type: "mo", text: OPERATORS[command] };
      } else if (typeof DELIMITERS[command] === "string") {
        node = { type: "mo", text: DELIMITERS[command] };
      } else {
        throw new Error("unsupported TeX command");
      }
    } else if (/[0-9]/.test(character)) {
      var numberStart = this.index;
      this.index += 1;
      if (!singleToken) while (/[0-9.]/.test(this.peek())) this.index += 1;
      node = { type: "mn", text: this.source.slice(numberStart, this.index) };
    } else if (/[A-Za-z\u0370-\u03ff]/.test(character)) {
      node = { type: "mi", text: this.take() };
    } else {
      var operator = this.take();
      if (operator === "-") operator = "−";
      if (operator === "*") operator = "∗";
      // Older public-text sanitization used angle quotation marks in TeX.
      if (operator === "›") operator = ">";
      if (operator === "‹") operator = "<";
      node = { type: "mo", text: operator };
    }

    if (withScripts === false) return node;
    var subscript = null;
    var superscript = null;
    this.skipSpace();
    while (this.peek() === "_" || this.peek() === "^") {
      var marker = this.take();
      var value = this.script();
      if (marker === "_") {
        if (subscript) throw new Error("duplicate subscript");
        subscript = value;
      } else {
        if (superscript) throw new Error("duplicate superscript");
        superscript = value;
      }
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
    if (typeof source !== "string" || !source.trim() || source.length > 2000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f$]/.test(source)) return null;
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
    if (node.variant) element.setAttribute("mathvariant", node.variant);
    if (node.type === "mo" && /^[()[\]{}|‖⟨⟩]$/.test(node.text)) {
      // Explicit fence spacing also handles a parenthesis in an infix position
      // (G(0), adjacent factors), consistently across native MathML engines.
      element.setAttribute("lspace", "0em");
      element.setAttribute("rspace", "0em");
      element.setAttribute("fence", "true");
      // Ordinary TeX parentheses retain text size; only left/right stretch.
      element.setAttribute("stretchy", node.stretchy ? "true" : "false");
    }
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
