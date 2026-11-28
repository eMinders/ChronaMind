// Syntax-only check of every ```mermaid block. Usage: node mermaid_parse.cjs <node_modules_dir> <file.md>
// Needs `mermaid` and `jsdom` installed in <node_modules_dir>. This does NOT render the diagrams.
const path = require("path");
const fs = require("fs");
const nm = path.resolve(process.argv[2]);
const req = (m) => require(require.resolve(m, { paths: [nm, path.dirname(nm)] }));
const { JSDOM } = req("jsdom");
const dom = new JSDOM("<!DOCTYPE html><body></body>");
global.window = dom.window; global.document = dom.window.document;
try { global.navigator = dom.window.navigator; } catch (e) {}
global.DOMPurify = undefined;
(async () => {
  const mermaidPkg = JSON.parse(fs.readFileSync(path.join(nm, "mermaid", "package.json"), "utf8"));
  const mermaid = (await import(require.resolve("mermaid/dist/mermaid.core.mjs", { paths: [nm] }))).default;
  mermaid.initialize({ startOnLoad: false });
  const text = fs.readFileSync(process.argv[3], "utf8");
  const blocks = [...text.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1]);
  let failed = 0;
  for (let i = 0; i < blocks.length; i++) {
    try { await mermaid.parse(blocks[i]); console.log(`block ${i + 1}/${blocks.length}: parse OK (${blocks[i].split("\n")[0].trim()})`); }
    catch (e) { failed++; console.log(`block ${i + 1}/${blocks.length}: PARSE FAILED: ${String(e.message || e).split("\n")[0]}`); }
  }
  console.log(`mermaid ${mermaidPkg.version}: ${blocks.length - failed} of ${blocks.length} blocks parsed`);
  process.exit(failed ? 1 : 0);
})();
