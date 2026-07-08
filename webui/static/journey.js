/** Journey panel — learned skills + memory timeline (HP-401g–k). */

let _journeyPayload = null;
let _journeySelectedId = null;

function _journeyT(key, fallback) {
  return typeof t === 'function' ? t(key) : fallback;
}

function _journeyEsc(s) {
  return typeof esc === 'function' ? esc(String(s ?? '')) : String(s ?? '');
}

function _journeyFormatDate(ts) {
  if (!ts) return _journeyT('journey_date_unknown', 'unknown');
  try {
    const d = new Date(Number(ts) * 1000);
    if (Number.isNaN(d.getTime())) return _journeyT('journey_date_unknown', 'unknown');
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch (_) {
    return _journeyT('journey_date_unknown', 'unknown');
  }
}

function _journeyBucketKey(ts) {
  if (!ts) return _journeyT('journey_bucket_unknown', 'Undated');
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return _journeyT('journey_bucket_unknown', 'Undated');
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long' });
}

function _journeyBuckets(nodes) {
  const groups = new Map();
  (nodes || []).forEach((node) => {
    const key = _journeyBucketKey(node.timestamp);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  });
  return Array.from(groups.entries()).map(([label, items]) => ({
    label,
    items: items.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0)),
  }));
}

function _journeyRenderStats(stats) {
  const s = stats || {};
  const cards = [
    { label: _journeyT('journey_stat_skills', 'Learned skills'), value: s.learned_skills || 0 },
    { label: _journeyT('journey_stat_memories', 'Memories'), value: s.memory_nodes || 0 },
    { label: _journeyT('journey_stat_edges', 'Links'), value: (s.related_edges || 0) + (s.memory_skill_edges || 0) },
  ];
  return (
    '<div class="journey-stats">' +
    cards
      .map(
        (c) =>
          `<div class="journey-stat"><div class="journey-stat-value">${_journeyEsc(c.value)}</div><div class="journey-stat-label">${_journeyEsc(c.label)}</div></div>`,
      )
      .join('') +
    '</div>'
  );
}

function _journeyRenderEmpty() {
  return (
    `<div class="journey-empty insights-empty">` +
    `<div class="journey-empty-title">${_journeyEsc(_journeyT('journey_empty_title', 'No learning yet'))}</div>` +
    `<p>${_journeyEsc(_journeyT('journey_empty_body', 'Use /learn to distill skills, the memory tool to save notes, or install skills from the hub.'))}</p>` +
    `</div>`
  );
}

function _journeyRenderList(payload) {
  const nodes = payload.nodes || [];
  if (!nodes.length) return _journeyRenderEmpty();

  const legend =
    `<div class="journey-legend">` +
    `<span><i class="journey-glyph skill">●</i> ${_journeyEsc(_journeyT('journey_legend_skill', 'Skill'))}</span>` +
    `<span><i class="journey-glyph memory">◆</i> ${_journeyEsc(_journeyT('journey_legend_memory', 'Memory'))}</span>` +
    `</div>`;

  const buckets = _journeyBuckets(nodes);
  const list = buckets
    .map((bucket) => {
      const rows = bucket.items
        .map((node) => {
          const glyph = node.kind === 'memory' ? '◆' : '●';
          const cls = node.kind === 'memory' ? 'memory' : 'skill';
          const selected = node.id === _journeySelectedId ? ' journey-row-selected' : '';
          return (
            `<button type="button" class="journey-row${selected}" data-journey-id="${_journeyEsc(node.id)}" onclick="journeySelectNode('${_journeyEsc(node.id)}')">` +
            `<span class="journey-glyph ${cls}">${glyph}</span>` +
            `<span class="journey-row-label">${_journeyEsc(node.label || node.id)}</span>` +
            `<span class="journey-row-meta">${_journeyEsc(_journeyFormatDate(node.timestamp))}</span>` +
            `</button>`
          );
        })
        .join('');
      return `<section class="journey-bucket"><h3>${_journeyEsc(bucket.label)}</h3>${rows}</section>`;
    })
    .join('');

  return legend + _journeyRenderStats(payload.stats) + `<div class="journey-list">${list}</div>`;
}

function _journeyRenderDetail(detail) {
  const box = $('journeyDetail');
  if (!box) return;
  if (!detail || !detail.ok) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = false;
  const isSkill = detail.kind === 'skill';
  box.innerHTML =
    `<div class="journey-detail-head">` +
    `<div class="journey-detail-title">${_journeyEsc(detail.label || detail.id)}</div>` +
    `<div class="journey-detail-actions">` +
    `<button type="button" class="sm-btn" onclick="journeyOpenEdit()">${_journeyEsc(_journeyT('journey_edit', 'Edit'))}</button>` +
    `<button type="button" class="sm-btn danger" onclick="journeyConfirmDelete()">${_journeyEsc(_journeyT('journey_delete', 'Delete'))}</button>` +
    (isSkill
      ? `<button type="button" class="sm-btn" onclick="switchPanel('skills')">${_journeyEsc(_journeyT('journey_open_skills', 'Skills panel'))}</button>`
      : `<button type="button" class="sm-btn" onclick="switchPanel('memory')">${_journeyEsc(_journeyT('journey_open_memory', 'Memory panel'))}</button>`) +
    `</div></div>` +
    `<pre class="journey-detail-body">${_journeyEsc((detail.content || '').slice(0, 4000))}</pre>` +
    (isSkill
      ? `<p class="journey-detail-hint">${_journeyEsc(_journeyT('journey_delete_skill_hint', 'Deleting a skill archives it — restore with intellect curator restore.'))}</p>`
      : '');
}

async function loadJourney(force) {
  const main = $('journeyContent');
  const sidebar = $('journeySidebar');
  if (!main) return;
  if (!force && _journeyPayload) {
    main.innerHTML = _journeyRenderList(_journeyPayload);
    return;
  }
  const loading = `<div style="color:var(--muted);font-size:12px">${_journeyEsc(_journeyT('loading', 'Loading...'))}</div>`;
  main.innerHTML = loading;
  if (sidebar) sidebar.innerHTML = loading;
  try {
    const data = await api('/api/learning/graph');
    _journeyPayload = data || { nodes: [], stats: {} };
    main.innerHTML = _journeyRenderList(_journeyPayload);
    if (sidebar) sidebar.innerHTML = _journeyRenderStats(_journeyPayload.stats);
    if (_journeySelectedId) await journeySelectNode(_journeySelectedId, true);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    main.innerHTML = `<div class="insights-empty">${_journeyEsc(msg)}</div>`;
  }
}

async function journeySelectNode(nodeId, silent) {
  _journeySelectedId = nodeId;
  document.querySelectorAll('.journey-row').forEach((el) => {
    el.classList.toggle('journey-row-selected', el.dataset.journeyId === nodeId);
  });
  try {
    const detail = await api('/api/learning/node?id=' + encodeURIComponent(nodeId));
    _journeyRenderDetail(detail);
    if (!silent && typeof toast === 'function') toast(_journeyT('journey_loaded', 'Node loaded'));
  } catch (err) {
    _journeyRenderDetail(null);
    if (typeof toast === 'function') toast(err.message || String(err));
  }
}

async function journeyOpenEdit() {
  if (!_journeySelectedId) return;
  const detail = await api('/api/learning/node?id=' + encodeURIComponent(_journeySelectedId));
  if (!detail || !detail.ok) return;
  const next = window.prompt(_journeyT('journey_edit_prompt', 'Edit content:'), detail.content || '');
  if (next === null) return;
  try {
    await api('/api/learning/node', { method: 'PUT', body: JSON.stringify({ id: _journeySelectedId, content: next }) });
    if (typeof toast === 'function') toast(_journeyT('journey_saved', 'Saved'));
    _journeyPayload = null;
    await loadJourney(true);
    await journeySelectNode(_journeySelectedId, true);
  } catch (err) {
    if (typeof toast === 'function') toast(err.message || String(err));
  }
}

async function journeyConfirmDelete() {
  if (!_journeySelectedId) return;
  const detail = await api('/api/learning/node?id=' + encodeURIComponent(_journeySelectedId));
  const label = detail && detail.label ? detail.label : _journeySelectedId;
  const hint =
    detail && detail.kind === 'skill'
      ? _journeyT('journey_delete_skill_confirm', 'Archive this skill? Restore via intellect curator restore.')
      : _journeyT('journey_delete_memory_confirm', 'Delete this memory chunk?');
  if (!window.confirm(`${hint}\n\n${label}`)) return;
  try {
    await api('/api/learning/node', { method: 'DELETE', body: JSON.stringify({ id: _journeySelectedId }) });
    if (typeof toast === 'function') toast(_journeyT('journey_deleted', 'Deleted'));
    _journeySelectedId = null;
    _journeyPayload = null;
    _journeyRenderDetail(null);
    await loadJourney(true);
  } catch (err) {
    if (typeof toast === 'function') toast(err.message || String(err));
  }
}
