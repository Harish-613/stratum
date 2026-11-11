// Webview script: renders DOT from the engine with Graphviz (WebAssembly),
// adds pan/zoom and click handling, and fills the findings sidebar.

const vscode = acquireVsCodeApi();
const { Graphviz } = await import(window.GRAPHVIZ_URL);
const graphviz = await Graphviz.load();

const graphEl = document.getElementById('graph');
const canvas = document.getElementById('canvas');
const sidebar = document.getElementById('sidebar');
const crumbs = document.getElementById('crumbs');
const depthSel = document.getElementById('depth');

let view = { mode: 'files', focus: '', depth: 2 };
let transform = { x: 0, y: 0, scale: 1 };

// ---------- messages from the extension ----------
window.addEventListener('message', (event) => {
  const msg = event.data;
  if (msg.type === 'render') {
    view = msg.view;
    renderGraph(msg.dot);
    renderToolbar();
    renderSidebar(msg.report);
  }
});
vscode.postMessage({ type: 'ready' });

// ---------- graph ----------
function renderGraph(dot) {
  let svg;
  try {
    svg = graphviz.dot(dot);
  } catch (err) {
    graphEl.innerHTML = `<p class="hint">Graphviz failed: ${escapeHtml(String(err))}</p>`;
    return;
  }
  graphEl.innerHTML = svg;
  const svgEl = graphEl.querySelector('svg');
  svgEl.removeAttribute('width');
  svgEl.removeAttribute('height');
  for (const node of svgEl.querySelectorAll('g.node')) {
    const id = node.id || '';
    node.addEventListener('click', (e) => {
      e.stopPropagation();
      if (id.startsWith('file:')) {
        vscode.postMessage({ type: 'openFile', path: id.slice(5) });
      } else if (id.startsWith('dir:')) {
        const folder = id.slice(4);
        vscode.postMessage({ type: 'focus', folder: folder === '(root)' ? '' : folder });
      }
    });
    node.addEventListener('mouseenter', () => highlight(svgEl, node, true));
    node.addEventListener('mouseleave', () => highlight(svgEl, node, false));
  }
  fit();
}

// Highlight a node's incoming and outgoing edges on hover.
function highlight(svgEl, node, on) {
  const name = node.querySelector('title')?.textContent;
  if (!name) return;
  for (const edge of svgEl.querySelectorAll('g.edge')) {
    const [src, dst] = (edge.querySelector('title')?.textContent || '').split('->');
    if (src === name || dst === name) edge.classList.toggle('hot', on);
  }
}

// ---------- pan & zoom ----------
function apply() {
  graphEl.style.transform = `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`;
}

function fit() {
  const svgEl = graphEl.querySelector('svg');
  if (!svgEl) return;
  const box = svgEl.viewBox.baseVal;
  const pad = 24;
  const scale = Math.min((canvas.clientWidth - pad) / box.width, (canvas.clientHeight - pad) / box.height, 1.5);
  svgEl.style.width = `${box.width}px`;
  svgEl.style.height = `${box.height}px`;
  transform = {
    scale,
    x: (canvas.clientWidth - box.width * scale) / 2,
    y: (canvas.clientHeight - box.height * scale) / 2,
  };
  apply();
}

function zoomAt(factor, cx, cy) {
  const next = Math.max(0.05, Math.min(8, transform.scale * factor));
  const k = next / transform.scale;
  transform.x = cx - (cx - transform.x) * k;
  transform.y = cy - (cy - transform.y) * k;
  transform.scale = next;
  apply();
}

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top);
}, { passive: false });

let drag = null;
canvas.addEventListener('pointerdown', (e) => {
  if (e.target.closest('g.node')) return;
  drag = { x: e.clientX - transform.x, y: e.clientY - transform.y };
  canvas.setPointerCapture(e.pointerId);
  canvas.classList.add('dragging');
});
canvas.addEventListener('pointermove', (e) => {
  if (!drag) return;
  transform.x = e.clientX - drag.x;
  transform.y = e.clientY - drag.y;
  apply();
});
canvas.addEventListener('pointerup', () => {
  drag = null;
  canvas.classList.remove('dragging');
});
window.addEventListener('resize', fit);

document.getElementById('zoom-in').onclick = () => zoomAt(1.25, canvas.clientWidth / 2, canvas.clientHeight / 2);
document.getElementById('zoom-out').onclick = () => zoomAt(0.8, canvas.clientWidth / 2, canvas.clientHeight / 2);
document.getElementById('zoom-fit').onclick = fit;

// ---------- toolbar ----------
document.getElementById('mode-folders').onclick = () =>
  vscode.postMessage({ type: 'view', mode: 'folders', depth: Number(depthSel.value) });
document.getElementById('mode-files').onclick = () => vscode.postMessage({ type: 'view', mode: 'files' });
depthSel.onchange = () => vscode.postMessage({ type: 'view', mode: 'folders', depth: Number(depthSel.value) });

function renderToolbar() {
  document.getElementById('mode-folders').classList.toggle('active', view.mode === 'folders');
  document.getElementById('mode-files').classList.toggle('active', view.mode === 'files');
  document.getElementById('depth-wrap').style.display = view.mode === 'folders' ? '' : 'none';
  depthSel.value = String(view.depth);

  crumbs.innerHTML = '';
  if (view.mode !== 'files') return;
  const parts = view.focus ? view.focus.split('/') : [];
  crumbs.append(crumb('All files', ''));
  parts.forEach((part, i) => {
    crumbs.append(document.createTextNode(' / '));
    crumbs.append(crumb(part, parts.slice(0, i + 1).join('/')));
  });
}

function crumb(label, folder) {
  const a = document.createElement('a');
  a.textContent = label;
  a.onclick = () => vscode.postMessage({ type: 'focus', folder });
  return a;
}

// ---------- sidebar ----------
function renderSidebar(r) {
  const s = r.stats;
  const langs = Object.entries(s.languages).map(([k, v]) => `${k} ${v}`).join(', ');
  sidebar.innerHTML = `
    <div class="stats">
      <div><b>${s.files.toLocaleString()}</b> files</div>
      <div><b>${s.loc.toLocaleString()}</b> lines</div>
      <div><b>${s.edges.toLocaleString()}</b> imports</div>
      <div class="muted">${escapeHtml(langs)}</div>
    </div>`;

  section(`Import cycles (${s.cycles})`, r.cycles, (cycle) => {
    const el = document.createElement('div');
    el.className = 'item';
    el.innerHTML = cycle.map((f) => `<a data-file="${escapeAttr(f)}">${escapeHtml(f)}</a>`).join(' → ') + ' → ↺';
    return el;
  }, 'No import cycles.');

  section(`Unreachable files (${s.unreachable})`, r.unreachable,
    (f) => link(f, f), 'Every file is reachable from an entry point (or none were detected).');

  section(`Duplicate code (${s.duplicate_groups})`, r.duplicates, (d) => {
    const el = document.createElement('div');
    el.className = 'item';
    el.innerHTML = `<div class="muted">${d.lines} lines × ${d.instances.length}</div>` +
      d.instances.map((i) => `<a data-file="${escapeAttr(i.file)}" data-line="${i.line}">${escapeHtml(i.name)} <span class="muted">${escapeHtml(i.file)}:${i.line}</span></a>`).join('<br>');
    return el;
  }, 'No duplicated functions found.');

  section(`Complex functions (${s.complex_functions})`, r.complex, (c) =>
    link(`${c.name}  ·  ${c.complexity}`, c.file, c.line, `${c.file}:${c.line}`), 'No function has complexity ≥ 10.');

  section('Most imported files', r.hubs, (h) => link(`${h.path}`, h.path, undefined, `${h.fanIn} importers`), '');
}

function section(title, items, render, empty) {
  const details = document.createElement('details');
  details.open = items.length > 0 && items.length <= 20;
  details.innerHTML = `<summary>${escapeHtml(title)}</summary>`;
  if (items.length === 0 && empty) {
    details.insertAdjacentHTML('beforeend', `<div class="item muted">${escapeHtml(empty)}</div>`);
  }
  for (const item of items) details.append(render(item));
  sidebar.append(details);
}

function link(label, file, line, detail) {
  const el = document.createElement('div');
  el.className = 'item';
  el.innerHTML = `<a data-file="${escapeAttr(file)}" ${line ? `data-line="${line}"` : ''}>${escapeHtml(label)}</a>` +
    (detail ? ` <span class="muted">${escapeHtml(detail)}</span>` : '');
  return el;
}

sidebar.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-file]');
  if (!a) return;
  vscode.postMessage({ type: 'openFile', path: a.dataset.file, line: a.dataset.line ? Number(a.dataset.line) : undefined });
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}
const escapeAttr = escapeHtml;
