// lgtm[js/xss-through-dom]: rendered markdown is server-sanitized before display
/**
 * Lightweight Canvas editor (P3-5).
 * Markdown text editor with auto-save and "Send to Composer" button.
 * Attached to the Canvas panel in sidebar navigation.
 */

let _canvasData = null;       // {content: "...", updated_at: ..., filename: "canvas.md"}
let _canvasAutoSaveTimer = null;
const _CANVAS_AUTOSAVE_MS = 2000;
const _CANVAS_STORAGE_KEY = 'intellect-webui-canvas';

function _canvasPanel() {
  return document.getElementById('panelCanvas') || document.getElementById('mainCanvas');
}

async function loadCanvas() {
  const panel = _canvasPanel();
  if (!panel) return;
  try {
    const data = await api('/api/canvas/load');
    _canvasData = data || {content: '', updated_at: null, filename: 'canvas.md'};
    _renderCanvas();
  } catch (e) {
    // Fall back to localStorage
    try {
      const saved = localStorage.getItem(_CANVAS_STORAGE_KEY);
      _canvasData = saved ? JSON.parse(saved) : {content: '', updated_at: null, filename: 'canvas.md'};
    } catch (_) {
      _canvasData = {content: '', updated_at: null, filename: 'canvas.md'};
    }
    _renderCanvas();
  }
}

function _renderCanvas() {
  const panel = _canvasPanel();
  if (!panel) return;
  const d = _canvasData || {content: '', filename: 'canvas.md'};
  const ts = d.updated_at ? new Date(d.updated_at * 1000).toLocaleString() : 'never';

  panel.innerHTML =
    '<div class="canvas-toolbar">'
    + '<span class="canvas-filename">' + esc(d.filename || 'canvas.md') + '</span>'
    + '<span class="canvas-meta">Last saved: ' + esc(ts) + '</span>'
    + '<span style="flex:1"></span>'
    + '<button class="sm-btn" onclick="void _canvasSendToComposer()">Send to Composer</button>'
    + '<button class="sm-btn" onclick="void _canvasClear()">Clear</button>'
    + '</div>'
    + '<textarea id="canvasEditor" class="canvas-editor" placeholder="Start writing... Markdown supported." oninput="_canvasOnInput()"></textarea>'
    + '<div id="canvasPreview" class="canvas-preview"></div>';

  const ta = document.getElementById('canvasEditor');
  if (ta) ta.value = d.content || '';
  _canvasUpdatePreview();
}

function _canvasOnInput() {
  const ta = document.getElementById('canvasEditor');
  if (!ta) return;
  _canvasData.content = ta.value;
  _canvasData.updated_at = Math.floor(Date.now() / 1000);
  clearTimeout(_canvasAutoSaveTimer);
  _canvasAutoSaveTimer = setTimeout(_canvasSave, _CANVAS_AUTOSAVE_MS);
  _canvasUpdatePreview();
}

async function _canvasSave() {
  if (!_canvasData) return;
  try {
    localStorage.setItem(_CANVAS_STORAGE_KEY, JSON.stringify(_canvasData));
    await api('/api/canvas/save', {method: 'POST', body: JSON.stringify(_canvasData)});
  } catch (_) {
    // Silently degrade — localStorage is the fallback
  }
}

function _canvasUpdatePreview() {
  const preview = document.getElementById('canvasPreview');
  const ta = document.getElementById('canvasEditor');
  if (!preview || !ta) return;
  const md = ta.value || '';
  if (typeof renderMd === 'function') {
    preview.innerHTML = renderMd(md);
  } else {
    preview.textContent = md;
  }
}

function _canvasSendToComposer() {
  const ta = document.getElementById('canvasEditor');
  if (!ta || !ta.value.trim()) return;
  const composer = typeof $ === 'function' ? $('msg') : document.getElementById('msg');
  if (composer) {
    composer.value = ta.value.trim();
    composer.focus();
  }
}

function _canvasClear() {
  _canvasData = {content: '', updated_at: Math.floor(Date.now() / 1000), filename: 'canvas.md'};
  _renderCanvas();
  clearTimeout(_canvasAutoSaveTimer);
  _canvasSave();
}
