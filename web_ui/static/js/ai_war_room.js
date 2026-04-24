/**
 * AI 戰情室核心 — panel registry + polling + state。
 *
 * 用法：各 panel JS 先 AiWarRoom.register({id, fetch, render}) 自己，
 *       最後在頁面呼叫 AiWarRoom.init()。
 */
window.AiWarRoom = (function () {
  const MODE = window.__AWR_MODE__ || 'admin';
  const POLL_MS = 60 * 1000;

  const state = {
    mode: MODE,
    window: '24h',            // '24h' | '7d'
    feed: 'all',              // news tab
    hideUsed: true,           // admin only
    usedSet: new Set(),       // news_id string set
    usedSlugMap: {},          // news_id → episode slug
  };

  const panels = [];

  function register(p) {
    // p = { id, fetch(state)->Promise<data>, render(rootEl, data, state) }
    panels.push(p);
  }

  async function _refreshUsedSet() {
    try {
      const r = await fetch('/api/ai/used-set?type=news');
      if (!r.ok) return;
      const d = await r.json();
      state.usedSet = new Set((d.ids || []).map(String));
      state.usedSlugMap = d.slug_map || {};
    } catch (e) {
      // ignore — panels 仍可渲染
    }
  }

  async function _runPanel(p) {
    const root = document.getElementById(p.id);
    if (!root) return;
    const body = root.querySelector('.awr-panel-body');
    try {
      const data = await p.fetch(state);
      p.render(root, data, state);
    } catch (e) {
      if (body) body.innerHTML = `<div class="awr-error">載入失敗：${e.message || e}</div>`;
      // 某 panel 失敗不影響其他
    }
  }

  async function refreshAll() {
    await _refreshUsedSet();
    // 平行刷所有 panel，互不阻塞
    panels.forEach(_runPanel);
  }

  function _bindControls() {
    // window toggle
    document.querySelectorAll('.awr-window').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.awr-window').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.window = btn.dataset.window;
        document.querySelectorAll('[data-role="window"]').forEach(e => e.textContent = state.window);
        refreshAll();
      });
    });
    // news tabs
    const tabs = document.getElementById('awr-news-tabs');
    if (tabs) {
      tabs.addEventListener('click', (e) => {
        const btn = e.target.closest('.awr-tab');
        if (!btn) return;
        tabs.querySelectorAll('.awr-tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.feed = btn.dataset.feed || 'all';
        const p = panels.find(x => x.id === 'panel-news');
        if (p) _runPanel(p);
      });
    }
    // hide used
    const hu = document.getElementById('awr-hide-used');
    if (hu) {
      hu.addEventListener('change', () => {
        state.hideUsed = hu.checked;
        const p = panels.find(x => x.id === 'panel-news');
        if (p) _runPanel(p);
      });
    }
    // manual refresh
    const btn = document.getElementById('awr-refresh');
    if (btn) btn.addEventListener('click', refreshAll);
  }

  function init() {
    _bindControls();
    refreshAll();
    setInterval(refreshAll, POLL_MS);
  }

  async function markNewsUsed(newsId) {
    try {
      const r = await fetch('/api/ai/mark-used', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entity_type: 'news', entity_id: String(newsId), used_in_slug: 'skip_' + new Date().toISOString().slice(0, 10) }),
      });
      if (!r.ok) throw new Error('mark failed');
      state.usedSet.add(String(newsId));
      // 重刷 news panel
      const p = panels.find(x => x.id === 'panel-news');
      if (p) _runPanel(p);
      return true;
    } catch (e) { return false; }
  }

  // utilities exposed for panels
  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return iso.slice(0, 16);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return '剛剛';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 時前';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
    return iso.slice(0, 10);
  }

  return { register, init, refreshAll, state, markNewsUsed, escapeHtml, fmtTime };
})();
