// Trending Topics 榜 — 以 /api/trending 資料渲染 Top N。
(function () {
  const root = document.getElementById('wm-trending');
  if (!root) return;

  const arrowChar = { up: '▲', down: '▼', flat: '▬' };
  const catLabels = {
    ai_model: 'AI 模型',
    business: '商業',
    policy: '政策',
    product: '產品',
    semiconductor: '半導體',
    other: '其他',
  };
  const regionLabels = { taiwan: '🇹🇼', global: '🌐', mixed: '🌍' };

  function escape(s) {
    return String(s || '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function renderRow(row, idx) {
    const arrowCls = 'wm-arrow-' + (row.arrow || 'flat');
    const pct = row.heat_delta_pct;
    const pctLabel = (pct > 0 ? '+' : '') + (pct || 0).toFixed(1) + '%';
    const rankCls = idx < 3 ? 'wm-trow-rank top' : 'wm-trow-rank';
    const sample = (row.sample_titles || [])[0] || '';

    return `
      <a class="wm-trow" href="/topic/${encodeURIComponent(row.slug)}">
        <div class="${rankCls}">${idx + 1}</div>
        <div class="wm-trow-body">
          <div class="wm-trow-title">${escape(row.title)}</div>
          <div class="wm-trow-meta">
            ${row.category ? `<span class="cat">${escape(catLabels[row.category] || row.category)}</span>` : ''}
            <span>${regionLabels[row.region] || ''}</span>
            <span>${row.news_count} 則</span>
            ${sample ? `<span title="${escape(sample)}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px;">· ${escape(sample)}</span>` : ''}
          </div>
        </div>
        <div class="wm-trow-right">
          <div class="wm-trow-heat">${(row.heat || 0).toFixed(1)}</div>
          <div class="wm-trow-delta ${arrowCls}">${arrowChar[row.arrow] || '▬'} ${pctLabel}</div>
        </div>
      </a>
    `;
  }

  fetch('/api/trending?limit=10', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((rows) => {
      if (!rows || rows.length === 0) {
        root.innerHTML = '<div class="wm-empty">今日訊號平靜，請先執行 <code>python daily_pipeline.py --brief</code></div>';
        return;
      }
      root.innerHTML = rows.map(renderRow).join('');
    })
    .catch((err) => {
      root.innerHTML = `<div class="wm-empty">載入失敗（${err}）</div>`;
    });
})();
