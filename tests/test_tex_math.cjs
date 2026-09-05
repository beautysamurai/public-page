"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
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
// Literal angle brackets are math relations, never interpreted as HTML tags.
assert.ok(texMath.parse("x<y>z"));

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

function flatten(node) {
  return [node].concat((node.children || []).flatMap(flatten));
}

test("Greek variables are identifiers without operator spacing", () => {
  const nodes = flatten(texMath.parse(String.raw`A(\alpha,G(0))K+\xi+\nu+\Psi`));
  for (const symbol of ["α", "ξ", "ν", "Ψ"]) {
    assert.equal(nodes.find((node) => node.text === symbol).type, "mi");
  }
  const fences = flatten(texMath.render("G(0)(1-x)", false, documentStub())).filter((node) => ["(", ")"].includes(node.textContent));
  assert.equal(fences.length, 4);
  for (const fence of fences) {
    assert.equal(fence.attributes.lspace, "0em");
    assert.equal(fence.attributes.rspace, "0em");
    assert.equal(fence.attributes.stretchy, "false");
  }
  assert.equal(flatten(texMath.render(String.raw`\left(\frac{a}{b}\right)`, true, documentStub()))
    .find((node) => node.textContent === "(").attributes.stretchy, "true");
});

test("the reported norm, roman identity and inequality all render", () => {
  for (const source of [
    String.raw`\|h\|_1=\alpha`,
    String.raw`({\rm Id}-\alpha D_\nu I)\phi_\alpha=D_\nu K`,
    String.raw`\xi>1/2-H`, String.raw`\xi›1/2-H`,
    String.raw`\left\lVert h\right\rVert_1=\alpha`,
    String.raw`G(0)(1-\alpha)=1`, String.raw`S^\alpha`, String.raw`L^2`
  ]) assert.ok(texMath.render(source, false, documentStub()), source);
  const norm = flatten(texMath.parse(String.raw`\|h\|_1`));
  assert.equal(norm.filter((node) => node.text === "‖").length, 2);
  const roman = flatten(texMath.parse(String.raw`{\rm Id} + I`));
  assert.deepEqual(roman.filter((node) => node.type === "mi").map((node) => node.variant), ["normal", "normal", undefined]);
  assert.ok(flatten(texMath.parse(String.raw`\xi›0`)).some((node) => node.text === ">"));
});

test("scripts attach to the base, not to each other", () => {
  const atom = texMath.parse("x_1^2").children[0];
  assert.equal(atom.type, "msubsup");
  assert.deepEqual(atom.children.map((node) => node.text), ["x", "1", "2"]);
  assert.equal(texMath.parse("x^2_1").children[0].type, "msubsup");
  assert.equal(texMath.parse("x_12").children.length, 2, "unbraced script is a single token");
  for (const source of ["x_1_2", "x^^2", "{x", "x}", "x_", "{".repeat(65) + "x" + "}".repeat(65)]) {
    assert.equal(texMath.parse(source), null, source);
  }
  assert.ok(texMath.parse("x +\n y"), "multiline block TeX is accepted");
});

test("untrusted TeX cannot create HTML, links or arbitrary attributes", () => {
  for (const source of [String.raw`\href{javascript:bad}{x}`, String.raw`\style{color:red}{x}`, String.raw`\constructor`, "x".repeat(2001)]) {
    assert.equal(texMath.render(source, false, documentStub()), null);
  }
  const rendered = texMath.render('<script>alert(1)</script>', false, documentStub());
  const nodes = flatten(rendered);
  assert.ok(nodes.every((node) => ["math", "mrow", "mi", "mo", "mn"].includes(node.name)));
  assert.ok(nodes.every((node) => node.namespace === "http://www.w3.org/1998/Math/MathML"));
  assert.ok(nodes.every((node) => !Object.keys(node.attributes).some((key) => /^(on|href|style)/.test(key))));
});

test("Japanese and English formulas from the reported edition all parse", () => {
  const archiveDir = path.join(__dirname, "../site/data/archive");
  const index = JSON.parse(fs.readFileSync(path.join(archiveDir, "index.json"), "utf8"));
  // Freeze the regression corpus. New unsupported TeX has an intentional text
  // fallback and must not stop future research publication or its auto-merge.
  const regressionId = "2026-09-04-daily-openai-01";
  const texts = index.editions.filter((edition) => edition.editionId === regressionId)
    .map((edition) => JSON.parse(fs.readFileSync(path.join(archiveDir, edition.path), "utf8")).sourceText);
  const translations = JSON.parse(fs.readFileSync(path.join(__dirname, "../site/data/i18n/en.json"), "utf8"));
  texts.push(...translations.editions.filter((edition) => edition.editionId === regressionId).map((edition) => edition.sourceText));
  let count = 0;
  for (const text of texts) {
    for (const match of text.matchAll(/(?<![\\$])\$([^$\n]+)\$(?!\$)/g)) {
      assert.ok(texMath.parse(match[1]), match[1]);
      count += 1;
    }
  }
  assert.ok(count > 10);
});
