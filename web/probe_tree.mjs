import { chromium } from "playwright";
const base = process.argv[2];
const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(base + "&drive=1");
await page.waitForTimeout(9000);
const paths = await page.evaluate(async () => {
  const s = window.__stokes;
  if (!s) return ["NO __stokes"];
  for (let i = 0; i < 30; i++) {
    if (s.getState().tree !== null) break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  const tree = s.getState().tree;
  if (!tree) return ["NO TREE after 30s keys=" + Object.keys(s.getState()).slice(0, 40).join(",")];
  const out = [];
  const walk = (n, d) => {
    if (!n || d > 3) return;
    out.push(`${n.path} ${n.type} ${n.name}`);
    for (const c of n.children ?? []) walk(c, d + 1);
  };
  walk(tree, 0);
  return out;
});
console.log(paths.join("\n"));
await browser.close();
