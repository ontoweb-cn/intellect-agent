/**
 * Variable-height transcript virtual window (W4 / P1-B).
 * Mirrored by webui/api/transcript_virtual_window.py — keep algorithms in sync.
 *
 * Do NOT use SESSION_VIRTUAL_ROW_HEIGHT / sidebar fixed pads for #msgInner.
 */
(function (global) {
  'use strict';

  var MSG_VIRTUAL_THRESHOLD = 80;
  var DEFAULT_USER_ROW_HEIGHT = 72;
  var DEFAULT_ASSISTANT_ROW_HEIGHT = 120;

  function buildPrefixSums(heights) {
    var prefix = [0];
    for (var i = 0; i < heights.length; i++) {
      var h = Number(heights[i]) || 0;
      prefix.push(prefix[prefix.length - 1] + Math.max(0, h));
    }
    return prefix;
  }

  /**
   * @param {number[]} heights
   * @param {{scrollTop?:number,viewportHeight?:number,bufferPx?:number,threshold?:number,pinIndex?:number,forceStart?:number}} opts
   */
  function variableHeightVirtualWindow(heights, opts) {
    opts = opts || {};
    var total = heights ? heights.length : 0;
    var viewport = Math.max(1, Number(opts.viewportHeight) || 600);
    var buf = opts.bufferPx != null ? Number(opts.bufferPx) : viewport * 1.5;
    var scroll = Math.max(0, Number(opts.scrollTop) || 0);
    var threshold = opts.threshold != null ? Number(opts.threshold) : MSG_VIRTUAL_THRESHOLD;

    if (total <= Math.max(1, threshold)) {
      var p0 = buildPrefixSums(heights || []);
      return {
        virtualized: false,
        start: 0,
        end: total,
        topPad: 0,
        bottomPad: 0,
        total: total,
        totalHeight: p0[p0.length - 1] || 0,
      };
    }

    var prefix = buildPrefixSums(heights);
    var totalH = prefix[prefix.length - 1];
    var start;
    var end;

    if (opts.forceStart != null && isFinite(Number(opts.forceStart))) {
      start = Math.max(0, Math.min(Number(opts.forceStart) | 0, Math.max(0, total - 1)));
      var targetBottom = prefix[start] + viewport + buf;
      end = start;
      while (end < total && prefix[end] < targetBottom) end++;
      end = Math.max(end, Math.min(total, start + 1));
    } else {
      var targetTop = Math.max(0, scroll - buf);
      targetBottom = scroll + viewport + buf;
      start = 0;
      while (start < total && prefix[start + 1] <= targetTop) start++;
      end = start;
      while (end < total && prefix[end] < targetBottom) end++;
      end = Math.max(end, Math.min(total, start + 1));
    }

    if (opts.pinIndex != null && isFinite(Number(opts.pinIndex))) {
      var pin = Number(opts.pinIndex) | 0;
      if (pin >= 0 && pin < total && (pin < start || pin >= end)) {
        var approxRows = Math.max(
          1,
          Math.floor((viewport + 2 * buf) / Math.max(1, DEFAULT_ASSISTANT_ROW_HEIGHT))
        );
        start = Math.max(0, pin - Math.floor(approxRows / 3));
        end = Math.min(total, start + approxRows);
        if (end <= start) end = Math.min(total, start + 1);
        while (end < total && prefix[end] - prefix[start] < viewport + buf) end++;
        while (start > 0 && prefix[end] - prefix[start] < viewport + buf) start--;
      }
    }

    return {
      virtualized: true,
      start: start,
      end: end,
      topPad: prefix[start],
      bottomPad: Math.max(0, totalH - prefix[end]),
      total: total,
      totalHeight: totalH,
    };
  }

  function expandToTurnBoundaries(start, end, roles) {
    var total = roles ? roles.length : 0;
    if (!total) return { start: 0, end: 0 };
    var s = Math.max(0, Math.min(start | 0, total));
    var e = Math.max(s, Math.min(end | 0, total));
    while (s > 0 && roles[s] === 'assistant' && roles[s - 1] === 'assistant') s--;
    while (e < total && e > 0 && roles[e - 1] === 'assistant' && roles[e] === 'assistant') e++;
    return { start: s, end: e };
  }

  function messageVirtualSpacer(height, where) {
    var spacer = document.createElement('div');
    spacer.className = 'message-virtual-spacer';
    spacer.dataset.virtualSpacer = where || 'gap';
    spacer.setAttribute('aria-hidden', 'true');
    spacer.style.height = Math.max(0, Math.round(height || 0)) + 'px';
    spacer.style.flex = '0 0 auto';
    spacer.style.width = '100%';
    return spacer;
  }

  global.MSG_VIRTUAL_THRESHOLD = MSG_VIRTUAL_THRESHOLD;
  global.DEFAULT_USER_ROW_HEIGHT = DEFAULT_USER_ROW_HEIGHT;
  global.DEFAULT_ASSISTANT_ROW_HEIGHT = DEFAULT_ASSISTANT_ROW_HEIGHT;
  global.buildPrefixSums = buildPrefixSums;
  global.variableHeightVirtualWindow = variableHeightVirtualWindow;
  global.expandToTurnBoundaries = expandToTurnBoundaries;
  global.messageVirtualSpacer = messageVirtualSpacer;
})(typeof window !== 'undefined' ? window : globalThis);
