import { analyser, FSProvider, flatten, tech } from '@specfy/stack-analyser';
import '@specfy/stack-analyser/dist/autoload.js';

const root = process.argv[2];
if (!root) {
  console.error('usage: analyse.mjs <dir>');
  process.exit(2);
}

const res = await analyser({ provider: new FSProvider({ path: root }) });
const flat = flatten(res, { merge: true });

const slugs = new Set();
const walk = (pl) => {
  if (pl.tech) slugs.add(pl.tech);
  if (pl.techs) for (const t of pl.techs) slugs.add(t);
  if (pl.childs) for (const c of pl.childs) walk(c);
};
walk(flat);

const out = [];
const seen = new Set();
for (const slug of slugs) {
  const info = tech.indexed[slug];
  if (!info) continue;
  const dedup = `${info.name} ${info.type}`;
  if (seen.has(dedup)) continue;
  seen.add(dedup);
  out.push({ name: info.name, type: info.type, key: slug });
}

console.log(JSON.stringify({ techs: out }));
