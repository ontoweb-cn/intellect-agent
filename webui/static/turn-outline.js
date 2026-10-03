/**
 * Question outline for the chat turn navigator.
 * Mirrored by webui/api/turn_outline.py — keep the preview and pick rules in sync.
 *
 * No DOM. Row positions passed in are already relative to the scrollport top
 * (getBoundingClientRect delta), not offsetTop.
 */
(function (global) {
  'use strict';

  var TITLE_LIMIT = 96;
  var REPLY_LIMIT = 160;
  var ACTIVE_THRESHOLD = 140;
  var TURN_TOP_OFFSET = 56;
  var WS_V1 = /^\s*\[Workspace::v1:\s*(?:\\.|[^\]\\])+\]\s*/;
  var WS_LEGACY = /^\s*\[Workspace:[^\]]+\]\s*/;
  var LINK = /\[([^\]]+)\]\([^)]*\)/g;
  var PRESERVED = /^\s*\[your active task list was preserved across context compression\]/i;
  var COMPACT_BRACKET = /^\s*\[context compaction/i;
  var COMPACT_PLAIN = /^\s*context compaction/i;

  function messageText(message) {
    if (!message || typeof message !== 'object') return '';
    var content = message.content || '';
    if (Array.isArray(content)) {
      var parts = [];
      for (var i = 0; i < content.length; i++) {
        var part = content[i];
        if (part && part.type === 'text') parts.push(String(part.text || ''));
      }
      return parts.join('').trim();
    }
    return String(content).trim();
  }

  function stripWorkspacePrefix(text) {
    var value = String(text || '');
    var stripped = value.replace(WS_V1, '');
    if (stripped !== value) return stripped.trim();
    return value.replace(WS_LEGACY, '').trim();
  }

  function plainPreview(text, limit) {
    var s = stripWorkspacePrefix(text);
    s = s.replace(LINK, '$1');
    s = s.replace(/[`*#]/g, '');
    s = s.replace(/\s+/g, ' ').trim();
    if (s.length <= limit) return s;
    return s.slice(0, limit);
  }

  function attachmentName(message) {
    var attachments = message && message.attachments;
    if (!Array.isArray(attachments) || !attachments.length) return '';
    var first = attachments[0];
    var label = '';
    if (typeof first === 'string') label = first;
    else if (first && typeof first === 'object') label = first.name || first.filename || first.path || '';
    label = String(label).replace(/\\/g, '/').split('/').pop() || '';
    return label.trim();
  }

  function isPreservedTaskList(message) {
    if (!message || message.role !== 'user') return false;
    var text = messageText(message) || String(message.content || '');
    return PRESERVED.test(text);
  }

  function isContextCompaction(message) {
    if (!message || !message.role || message.role === 'tool') return false;
    var text = messageText(message) || String(message.content || '');
    return COMPACT_BRACKET.test(text) || COMPACT_PLAIN.test(text);
  }

  function buildTurnOutline(messages) {
    var entries = [];
    if (!messages || !messages.length) return entries;
    var users = [];
    for (var i = 0; i < messages.length; i++) {
      var message = messages[i];
      if (!message || message.role !== 'user') continue;
      if (isPreservedTaskList(message) || isContextCompaction(message)) continue;
      var title = plainPreview(messageText(message), TITLE_LIMIT);
      if (!title) title = plainPreview(attachmentName(message), TITLE_LIMIT);
      if (!title) continue;
      users.push({ index: i, title: title });
    }
    for (var n = 0; n < users.length; n++) {
      var end = n + 1 < users.length ? users[n + 1].index : messages.length;
      var reply = '';
      for (var j = users[n].index + 1; j < end; j++) {
        var candidate = messages[j];
        if (!candidate || candidate.role !== 'assistant') continue;
        if (isContextCompaction(candidate)) continue;
        var preview = plainPreview(messageText(candidate), REPLY_LIMIT);
        if (preview) {
          reply = preview;
          break;
        }
      }
      entries.push({ rawIdx: users[n].index, title: users[n].title, reply: reply });
    }
    return entries;
  }

  function candidateRelTop(item) {
    if (item.relTop != null) return Number(item.relTop);
    return Number(item.rel_top);
  }

  function candidateRawIdx(item) {
    if (item.rawIdx != null) return item.rawIdx;
    return item.raw_idx;
  }

  function pickActiveTurn(candidates, threshold) {
    if (!candidates || !candidates.length) return null;
    var line = threshold == null ? ACTIVE_THRESHOLD : Number(threshold);
    var passed = [];
    for (var i = 0; i < candidates.length; i++) {
      if (candidateRelTop(candidates[i]) <= line) passed.push(candidates[i]);
    }
    if (passed.length) return candidateRawIdx(passed[passed.length - 1]);
    var best = candidates[0];
    for (var j = 1; j < candidates.length; j++) {
      if (candidateRelTop(candidates[j]) < candidateRelTop(best)) best = candidates[j];
    }
    return candidateRawIdx(best);
  }

  function scrollTopForTurn(scrollTop, rowTopInContainer, topOffset) {
    var offset = topOffset == null ? TURN_TOP_OFFSET : Number(topOffset);
    var next = Number(scrollTop || 0) + Number(rowTopInContainer || 0) - offset;
    return next > 0 ? next : 0;
  }

  global.TURN_OUTLINE_TITLE_LIMIT = TITLE_LIMIT;
  global.TURN_OUTLINE_REPLY_LIMIT = REPLY_LIMIT;
  global.TURN_NAV_ACTIVE_THRESHOLD = ACTIVE_THRESHOLD;
  global.TURN_NAV_TOP_OFFSET = TURN_TOP_OFFSET;
  global.buildTurnOutline = buildTurnOutline;
  global.pickActiveTurn = pickActiveTurn;
  global.scrollTopForTurn = scrollTopForTurn;
})(typeof window !== 'undefined' ? window : globalThis);
