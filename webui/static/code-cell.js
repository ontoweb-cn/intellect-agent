/**
 * Code Cell component (P3-6b).
 * Interactive code execution panel with language selector and output display.
 */

let _codeCellExecutor = null;
let _codeCellRunning = false;

// ── Initialization ──────────────────────────────────────────────────────

async function _initCodeCellExecutor() {
  if (_codeCellExecutor) return _codeCellExecutor;
  try {
    const resp = await api('/api/code/status');
    _codeCellExecutor = resp;
    return resp;
  } catch (_) {
    _codeCellExecutor = { docker_available: false, languages: [] };
    return _codeCellExecutor;
  }
}

// ── Rendering ───────────────────────────────────────────────────────────

function _renderCodeCell(container, cellData) {
  const lang = cellData.language || 'python';
  const code = cellData.code || '';
  const output = cellData.output || null;
  const error = cellData.error || null;
  const isRunning = cellData._running === true;

  container.innerHTML =
    '<div class="code-cell" data-language="' + esc(lang) + '">'
    + '<div class="code-cell-header">'
    + '<select class="code-cell-lang" onchange="_codeCellLangChange(this)">'
    + '<option value="python"' + (lang === 'python' ? ' selected' : '') + '>Python</option>'
    + '<option value="bash"' + (lang === 'bash' ? ' selected' : '') + '>Bash</option>'
    + '<option value="javascript"' + (lang === 'javascript' ? ' selected' : '') + '>JavaScript</option>'
    + '</select>'
    + '<div class="code-cell-actions">'
    + '<button class="sm-btn code-cell-run-btn" onclick="_codeCellRun(this)" '
    + (isRunning ? 'disabled' : '') + '>' + (isRunning ? 'Running…' : 'Run') + '</button>'
    + '<button class="sm-btn code-cell-clear-btn" onclick="_codeCellClear(this)">Clear</button>'
    + '</div>'
    + '</div>'
    + '<textarea class="code-cell-editor" placeholder="Write code here…" spellcheck="false" onkeydown="_codeCellKeydown(event, this)">' + esc(code) + '</textarea>'
    + (output || error
      ? '<div class="code-cell-output' + (error ? ' code-cell-output-error' : '') + '">'
        + (output ? '<pre>' + esc(output) + '</pre>' : '')
        + (error ? '<pre class="code-cell-error">' + esc(error) + '</pre>' : '')
        + '</div>'
      : '<div class="code-cell-output code-cell-output-empty">Click Run to execute</div>')
    + '</div>';
}

function _codeCellLangChange(select) {
  const cell = select.closest('.code-cell');
  if (cell) cell.dataset.language = select.value;
}

function _codeCellKeydown(e, textarea) {
  // Ctrl+Enter to run
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    const cell = textarea.closest('.code-cell');
    if (cell) {
      const btn = cell.querySelector('.code-cell-run-btn');
      if (btn) _codeCellRun(btn);
    }
  }
}

// ── Execution ───────────────────────────────────────────────────────────

async function _codeCellRun(btn) {
  if (_codeCellRunning) return;
  const cell = btn.closest('.code-cell');
  if (!cell) return;

  const editor = cell.querySelector('.code-cell-editor');
  const output = cell.querySelector('.code-cell-output') || cell.appendChild(document.createElement('div'));
  const lang = cell.dataset.language || 'python';
  const code = editor ? editor.value.trim() : '';

  if (!code) return;

  _codeCellRunning = true;
  btn.disabled = true;
  btn.textContent = 'Running…';
  output.className = 'code-cell-output';
  output.innerHTML = '<pre>Running…</pre>';

  try {
    const resp = await api('/api/code/execute', {
      method: 'POST',
      body: JSON.stringify({ code, language: lang }),
    });
    if (resp.error) {
      output.className = 'code-cell-output code-cell-output-error';
      output.innerHTML = '<pre class="code-cell-error">' + esc(resp.error) + '</pre>';
    } else {
      const parts = [];
      if (resp.stdout) parts.push('<pre>' + esc(resp.stdout) + '</pre>');
      if (resp.stderr) parts.push('<pre class="code-cell-stderr">' + esc(resp.stderr) + '</pre>');
      if (!parts.length) parts.push('<pre>(no output)</pre>');
      const meta = 'Exit code: ' + resp.exit_code
        + ' · ' + (resp.execution_time_ms / 1000).toFixed(2) + 's'
        + (resp.truncated ? ' · output truncated' : '');
      output.innerHTML = parts.join('') + '<div class="code-cell-meta">' + esc(meta) + '</div>';
    }
  } catch (e) {
    output.className = 'code-cell-output code-cell-output-error';
    output.innerHTML = '<pre class="code-cell-error">' + esc(e.message || String(e)) + '</pre>';
  } finally {
    _codeCellRunning = false;
    btn.disabled = false;
    btn.textContent = 'Run';
  }
}

function _codeCellClear(btn) {
  const cell = btn.closest('.code-cell');
  if (!cell) return;
  const editor = cell.querySelector('.code-cell-editor');
  const output = cell.querySelector('.code-cell-output');
  if (editor) editor.value = '';
  if (output) {
    output.className = 'code-cell-output code-cell-output-empty';
    output.innerHTML = 'Click Run to execute';
  }
}
