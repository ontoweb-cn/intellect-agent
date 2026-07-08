/** Journey panel — learned skills + memory timeline (HP-401g–k). */

let _journeyPayload = null;
let _journeySelectedId = null;
let _journeyViewMode = 'list';       // 'list' | 'timeline'
let _journeyTimelineData = null;    // cached render_frames result
let _journeyReveal = 1.0;           // 0..1 timeline scrubber

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

function _journeyRenderTimelineGrid(framesData) {
  if (!framesData || !framesData.frames || !framesData.frames.length) {
    return `<div class="journey-empty">${_journeyEsc(_journeyT('journey_empty_title', 'No timeline data'))}</div>`;
  }
  // Pick the frame closest to _journeyReveal
  const total = framesData.frames.length;
  const idx = Math.min(total - 1, Math.max(0, Math.round(_journeyReveal * (total - 1))));
  const frame = framesData.frames[idx];
  // Build a monospace <pre> from the text grid runs
  let text = '';
  (frame.grid || []).forEach(function(row) {
    (row || []).forEach(function(run) {
      text += (run[0] || '');
    });
    text += '\n';
  });
  const date = frame.date || '';
  const visible = frame.visible || 0;
  const total_nodes = framesData.count || 0;
  const pct = Math.round(_journeyReveal * 100);
  return (
    `<div class="journey-timeline">` +
    `<pre class="journey-timeline-grid">${_journeyEsc(text)}</pre>` +
    `<div class="journey-timeline-controls">` +
    `<input type="range" min="0" max="1" step="0.02" value="${_journeyReveal}" class="journey-reveal-slider" oninput="journeySetReveal(parseFloat(this.value))">` +
    `<span class="journey-reveal-label">${_journeyEsc(date)} · ${visible}/${total_nodes} revealed · ${pct}%</span>` +
    `</div></div>`
  );
}

async function _journeyLoadTimeline(force) {
  if (!force && _journeyTimelineData) return _journeyTimelineData;
  const cols = Math.max(44, Math.floor((document.getElementById('journeyContent')?.clientWidth || 80) * 0.12));
  const rows = 20;
  const frames = 48;
  try {
    _journeyTimelineData = await api('/api/learning/frames?cols=' + cols + '&rows=' + rows + '&frames=' + frames);
    return _journeyTimelineData;
  } catch (_) {
    return null;
  }
}

function journeySetReveal(value) {
  _journeyReveal = Math.max(0, Math.min(1, value));
  if (_journeyViewMode === 'timeline' && _journeyTimelineData) {
    const main = $('journeyContent');
    if (main) main.innerHTML = _journeyRenderTimelineGrid(_journeyTimelineData);
  }
}

function journeyToggleView() {
  _journeyViewMode = _journeyViewMode === 'list' ? 'timeline' : 'list';
  const btn = $('journeyViewToggle');
  if (btn) btn.textContent = _journeyViewMode === 'list' ? '⏱' : '☰';
  loadJourney(true);
}

async function loadJourney(force) {
  const main = $('journeyContent');
  const sidebar = $('journeySidebar');
  if (!main) return;
  if (!force && _journeyViewMode === 'list' && _journeyPayload) {
    main.innerHTML = _journeyRenderList(_journeyPayload);
    if (sidebar) sidebar.innerHTML = _journeyRenderStats(_journeyPayload.stats);
    return;
  }
  const loading = `<div style="color:var(--muted);font-size:12px">${_journeyEsc(_journeyT('loading', 'Loading...'))}</div>`;
  main.innerHTML = loading;
  if (sidebar) sidebar.innerHTML = loading;
  try {
    if (_journeyViewMode === 'timeline') {
      const td = await _journeyLoadTimeline(force);
      main.innerHTML = _journeyRenderTimelineGrid(td);
      if (sidebar) sidebar.innerHTML = _journeyRenderStats((td && td.legend) ? {} : {});
    } else {
      const data = await api('/api/learning/graph');
      _journeyPayload = data || { nodes: [], stats: {} };
      main.innerHTML = _journeyRenderList(_journeyPayload);
      if (sidebar) sidebar.innerHTML = _journeyRenderStats(_journeyPayload.stats);
    }
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
