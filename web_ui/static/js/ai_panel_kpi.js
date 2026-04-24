(function () {
  const H = AiWarRoom;

  function heatClass(pct) {
    if (pct < -10) return 'heat-cold';
    if (pct <= 10) return 'heat-flat';
    if (pct <= 30) return 'heat-warm';
    if (pct <= 60) return 'heat-hot';
    return 'heat-fire';
  }
  function arrow(pct) {
    if (pct > 10) return '▲';
    if (pct < -10) return '▼';
    return '—';
  }

  H.register({
    id: 'panel-kpi',
    fetch: async () => {
      const r = await fetch('/api/ai/kpi');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    },
    render: (root, d) => {
      const grid = root.querySelector('.awr-kpi-grid');
      if (!grid) return;
      const today = d.today_news || {};
      const active = d.active_cos || {};
      const model = d.model_release || {};
      const hot = d.hot_topic;

      const deltaCls = heatClass(today.delta_pct || 0);
      const deltaStr = (today.delta_pct > 0 ? '+' : '') + (today.delta_pct || 0) + '%';
      const hotPct = hot ? (hot.delta_pct || 0) : 0;
      const hotCls = heatClass(hotPct);

      grid.innerHTML = `
        <div class="awr-kpi-card">
          <div class="awr-kpi-label">今日 AI 新聞</div>
          <div class="awr-kpi-value">${today.value || 0}</div>
          <div class="awr-kpi-delta ${deltaCls}">
            ${arrow(today.delta_pct || 0)} ${deltaStr}
            <span style="color:var(--awr-dim);">昨日 ${today.yesterday || 0}</span>
          </div>
        </div>
        <div class="awr-kpi-card">
          <div class="awr-kpi-label">24H 活躍公司</div>
          <div class="awr-kpi-value">${active.value || 0}<span style="color:var(--awr-dim);font-size:14px;"> / ${active.total || 0}</span></div>
          <div class="awr-kpi-delta" style="color:var(--awr-muted);">
            ${Math.round(100 * (active.value || 0) / Math.max(active.total || 1, 1))}% 在動
          </div>
        </div>
        <div class="awr-kpi-card">
          <div class="awr-kpi-label">模型發布 / 30天</div>
          <div class="awr-kpi-value">${model.value || 0}</div>
          <div class="awr-kpi-delta" style="color:var(--awr-muted);">
            滾動統計
          </div>
        </div>
        <div class="awr-kpi-card" ${hot ? `onclick="location.href='/topic/${encodeURIComponent(hot.slug)}'" style="cursor:pointer;"` : ''}>
          <div class="awr-kpi-label">最熱主題 · 24H</div>
          <div class="awr-kpi-value" style="font-size:14px;line-height:1.3;font-weight:700;">
            ${hot ? H.escapeHtml(hot.title.slice(0, 34)) : '—'}
          </div>
          <div class="awr-kpi-delta ${hotCls}">
            ${hot ? `${arrow(hotPct)} ${hotPct > 0 ? '+' : ''}${hotPct}%` : ''}
            ${hot ? `<span style="color:var(--awr-dim);">${hot.news_count} 則</span>` : ''}
          </div>
        </div>`;
    },
  });
})();
