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
    categorySummaries: {},    // feed → { summary_zh, news_count, generated_at, ... }
    categorySummariesDate: null,
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

  async function _refreshCategorySummaries() {
    try {
      const r = await fetch('/api/ai/category-summaries');
      if (!r.ok) return;
      const d = await r.json();
      state.categorySummaries = d.summaries || {};
      state.categorySummariesDate = d.date;
    } catch (e) {
      // ignore
    }
  }

  async function rerunCategorySummary(feed) {
    try {
      const r = await fetch('/api/ai/category-summaries/run-now', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feed: feed || null, force: true }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) {
        _showToast('啟動失敗：' + (d.error || r.status), 'error');
        return false;
      }
      _showToast('已在背景啟動，30~120 秒後自動更新', 'success');
      // 等 60 秒後 refresh 一次
      setTimeout(() => {
        _refreshCategorySummaries().then(() => {
          const p = panels.find(x => x.id === 'panel-news');
          if (p) _runPanel(p);
        });
      }, 60000);
      return true;
    } catch (e) {
      _showToast('啟動失敗：' + e.message, 'error');
      return false;
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
    await Promise.all([_refreshUsedSet(), _refreshCategorySummaries()]);
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

  function _showToast(msg, kind) {
    kind = kind || 'success';
    let stack = document.getElementById('toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'toast-stack';
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    const icon = { success: '✓', info: 'ℹ', warn: '⚠', error: '✕' }[kind] || 'ℹ';
    const el = document.createElement('div');
    el.className = 'toast toast-' + kind;
    el.innerHTML = '<span class="toast-icon">' + icon + '</span><div class="toast-body"></div><button class="toast-close" type="button">×</button>';
    el.querySelector('.toast-body').textContent = msg;
    el.querySelector('.toast-close').addEventListener('click', () => el.remove());
    stack.appendChild(el);
    setTimeout(() => { el.classList.add('toast-out'); setTimeout(() => el.remove(), 250); }, 4500);
  }

  /**
   * 從戰情室選題建立 Episode（topic_id 模式）。
   * 成功後不離開頁面，更新右側 sidepanel + 標記 used。
   */
  async function selectTopic(topicId, title) {
    const ok = await confirmModal({
      title: '建立新集',
      body: `將以「<b>${escapeHtml(title || ('topic ' + topicId))}</b>」建立新集，<br>之後可在「製作集數」追蹤進度。`,
      confirmText: '建立',
      icon: '🎬',
    });
    if (!ok) return false;
    try {
      const fd = new FormData();
      fd.append('topic_id', String(topicId));
      const r = await fetch('/select-topic', {
        method: 'POST',
        headers: { 'X-Requested-With': 'fetch' },
        body: fd,
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        _showToast('建立失敗：' + (data.error || r.status), 'error');
        return false;
      }
      _showToast('已開集：' + (data.title || data.slug), 'success');
      if (window.AwrSelected) window.AwrSelected.refresh();
      // refresh trending panel 把該 topic 標 used
      _refreshUsedSet().then(() => panels.forEach(_runPanel));
      return data;
    } catch (e) {
      _showToast('建立失敗：' + e.message, 'error');
      return false;
    }
  }

  /**
   * 從戰情室選題建立 Episode（news_id 模式，焦點新聞用）。
   */
  async function selectNews(newsId, title) {
    const ok = await confirmModal({
      title: '建立新集',
      body: `將以新聞「<b>${escapeHtml(title || ('#' + newsId))}</b>」建立新集。`,
      confirmText: '建立',
      icon: '🎬',
    });
    if (!ok) return false;
    try {
      const fd = new FormData();
      fd.append('news_id', String(newsId));
      fd.append('angle', 'A');
      const r = await fetch('/select', {
        method: 'POST',
        headers: { 'X-Requested-With': 'fetch' },
        body: fd,
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        _showToast('建立失敗：' + (data.error || r.status), 'error');
        return false;
      }
      _showToast('已開集：' + (data.title || data.slug), 'success');
      if (window.AwrSelected) window.AwrSelected.refresh();
      _refreshUsedSet().then(() => panels.forEach(_runPanel));
      return data;
    } catch (e) {
      _showToast('建立失敗：' + e.message, 'error');
      return false;
    }
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

  return { register, init, refreshAll, state, markNewsUsed, selectTopic, selectNews, rerunCategorySummary, escapeHtml, fmtTime };
})();
