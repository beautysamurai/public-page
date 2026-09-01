"use strict";

const assert = require("node:assert/strict");
const texMath = require("../site/tex-math.js");

function documentStub() {
  return {
    createElementNS(namespace, name) {
      return {
        namespace,
        name,
        attributes: {},
        children: [],
        textContent: "",
        setAttribute(key, value) { this.attributes[key] = value; },
        appendChild(child) { this.children.push(child); return child; }
      };
    }
  };
}

const formula = String.raw`A=\sum_t (p_t-\bar{p})r_t`;
const ast = texMath.parse(formula);
assert.ok(ast, "supported TeX should parse");
assert.equal(ast.type, "mrow");
assert.equal(texMath.parse(String.raw`\href{bad}{x}`), null);
assert.equal(texMath.parse("<script>"), null);

const inline = texMath.render(formula, false, documentStub());
assert.ok(inline);
assert.equal(inline.name, "math");
assert.equal(inline.namespace, "http://www.w3.org/1998/Math/MathML");
assert.equal(inline.attributes.display, "inline");
assert.equal(inline.attributes.class, "source-inline-math");
assert.match(inline.attributes["aria-label"], /^TeX: A=/);

const block = texMath.render(String.raw`\frac{1}{\sqrt{T}}`, true, documentStub());
assert.ok(block);
assert.equal(block.attributes.display, "block");
assert.equal(block.attributes.class, "source-block-math");

console.log("TeX math renderer tests passed");
