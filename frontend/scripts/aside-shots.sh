#!/bin/bash
# usage: scripts/aside-shots.sh <base-url> <outdir> <path> [<path>...]
#   e.g. scripts/aside-shots.sh http://127.0.0.1:2455 /tmp/lb-shots / /usage /keys
# Needs Aside with the aside-guard wrappers (aside-tab, aside repl) and python3 with Pillow.
# Browser work on this machine goes through Aside only; do not swap in a headless browser.
#
# Aside has no viewport API, so each page is loaded in a same-origin iframe of the target width,
# scaled to fit the tab, captured in viewport chunks and stitched. Writes <slug>-<w>-<theme>.png
# and <slug>-checks.json (content height, scrollWidth vs clientWidth for the sideways-scroll check).
# The app's email blur is switched on for the origin, and email addresses plus every account's
# email local part (display names read "Max · alex") are masked before capture, so the images
# can go on the private review site.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
base=$1; out=$2; shift 2
mkdir -p "$out"
read -r -d '' template <<'JS' || true
await attachBrowserTab('__ID__');
await fs.mkdir('./artifacts', { recursive: true });
const res = {};
await page.evaluate(() => localStorage.setItem('agent-lb-privacy', '1'));
const locals = await page.evaluate(async () => {
  try {
    const j = await (await fetch('/api/accounts')).json();
    return (j.accounts || j).map((a) => (a.email || '').split('@')[0]).filter((x) => x.length > 1 && !['kimi', 'glm', 'cursor', 'devin', 'openrouter'].includes(x));
  } catch { return []; }
});
const mask = () => page.evaluate((locals) => {
  const d = document.getElementById('shot').contentDocument;
  const email = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+/g;
  const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const local = locals.length
    ? new RegExp('(?<![A-Za-z0-9._%+-])(' + locals.sort((a, b) => b.length - a.length).map(esc).join('|') + ')(?![A-Za-z0-9_%+-])', 'g')
    : null;
  const tw = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = tw.nextNode())) {
    let v = n.nodeValue.replace(email, (m) => m[0] + '•••@•••');
    if (local) v = v.replace(local, (m) => m[0] + '•••' + m.at(-1));
    if (v !== n.nodeValue) n.nodeValue = v;
  }
  d.querySelectorAll('[title]').forEach((e) => { if (/@/.test(e.title) || (local && local.test(e.title))) e.title = ''; });
}, locals);
for (const w of [1440, 390]) for (const theme of ['light', 'dark']) {
  const vw = await page.evaluate(() => innerWidth);
  const s = Math.min(1, (vw - 4) / w);
  await page.evaluate(({ p, w, s }) => {
    document.head.innerHTML = ''; document.body.innerHTML = ''; document.documentElement.className = '';
    document.documentElement.style.cssText = 'margin:0;overflow-x:hidden'; document.body.style.cssText = 'margin:0';
    const f = document.createElement('iframe'); f.id = 'shot'; f.setAttribute('scrolling', 'no'); f.src = p;
    f.style.cssText = 'border:0;display:block;width:' + w + 'px;height:900px;transform-origin:0 0;transform:scale(' + s + ')';
    document.body.appendChild(f);
  }, { p: '__PATH__', w, s });
  await sleep(7000);
  const info = await page.evaluate(({ theme, s }) => {
    const f = document.getElementById('shot'); const d = f.contentDocument;
    d.documentElement.classList.toggle('dark', theme === 'dark');
    const h = Math.max(d.documentElement.scrollHeight, d.body.scrollHeight);
    f.style.height = h + 'px'; document.body.style.height = Math.ceil(h * s) + 'px';
    return { h, s, sw: d.documentElement.scrollWidth, cw: d.documentElement.clientWidth, url: f.contentWindow.location.pathname, vh: innerHeight, dpr: devicePixelRatio };
  }, { theme, s });
  await sleep(1200);
  const total = Math.ceil(info.h * s); const chunks = [];
  for (let k = 0; k * info.vh < total; k++) {
    const y = await page.evaluate((y) => { window.scrollTo(0, y); return scrollY; }, k * info.vh);
    await mask(); await sleep(350);
    const name = '__SLUG__-' + w + '-' + theme + '-' + k + '.png';
    await fs.writeFile('./artifacts/' + name, await page.screenshot());
    chunks.push({ name, want: k * info.vh, y });
  }
  info.chunks = chunks; info.total = total; res[w + '-' + theme] = info;
}
await fs.writeFile('./artifacts/__SLUG__-checks.json', JSON.stringify(res));
await page.close();
JS
for path in "$@"; do
  slug=$(echo "$path" | sed 's#^/##; s#/#-#g')
  [ -z "$slug" ] && slug=home
  id=$(~/.local/aside-guard/bin/aside-tab "$base/__shots__" | tail -1)
  js=${template//__ID__/$id}; js=${js//__PATH__/$path}; js=${js//__SLUG__/$slug}
  ~/.local/aside-guard/bin/aside repl "$js" 2>&1 | grep -iv "last tab closed\|no current open\|ok |" | tail -3 || true
  sess=$(ls -dt ~/.aside/u/1/sessions/*/ | head -1)
  python3 "$here/aside-stitch.py" "${sess}artifacts" "$out" "$slug"
done
