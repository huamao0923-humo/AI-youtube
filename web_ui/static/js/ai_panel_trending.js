(function () {
  const H = AiWarRoom;
  let _wcChart = null;

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
    id: 'panel-trending',
    fetch: async (st) => {
      const r = await fetch(`/api/ai/trending-topics?window=${encodeURIComponent(st.window)}`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    },
    render: (root, data) => {
      const body = root.querySelector('.awr-panel-body');
      const topics = (data.topics || []).slice(0, 10);
      const wordcloud = data.wordcloud || [];

      const listHtml = topics.length ? (() => {
        const maxCnt = Math.max(1, ...topics.map(t => t.news_count || 0));
        return topics.map((t, i) => {
          const pct = t.heat_delta_pct || 0;
          const hCls = heatClass(pct);
          const bgCls = `bg-${heatClass(pct).replace('heat-', 'heat-')}`;
          const width = Math.round(100 * (t.news_count || 0) / maxCnt);
          return `
            <li class="awr-trend-row" data-slug="${H.escapeHtml(t.slug)}">
              <div class="rank">${i + 1}.</div>
              <div class="bar-wrap">
                <div class="bar ${bgCls}" style="width:${Math.max(width, 3)}%;"></div>
                <span class="title" title="${H.escapeHtml(t.title)}">${H.escapeHtml(t.title)}</span>
              </div>
              <div class="count">${t.news_count || 0}</div>
              <div class="delta ${hCls}">${arrow(pct)} ${pct > 0 ? '+' : ''}${pct}%</div>
            </li>`;
        }).join('');
      })() : '<li class="awr-empty">暫無熱門主題</li>';

      body.innerHTML = `
        <ul class="awr-trend-list">${listHtml}</ul>
        <div class="awr-trend-wc" id="awr-wc-container"></div>`;

      // 主題 row 點擊 → 跳 topic 頁
      body.querySelectorAll('[data-slug]').forEach(row => {
        row.addEventListener('click', () => { window.location.href = '/topic/' + encodeURIComponent(row.dataset.slug); });
      });

      // 話題雲（壓縮高 120px）
      const wcEl = body.querySelector('#awr-wc-container');
      if (!wcEl || !window.echarts) return;
      if (_wcChart) { try { _wcChart.dispose(); } catch (e) {} _wcChart = null; }
      if (!wordcloud.length) return;
      _wcChart = window.echarts.init(wcEl, null, { renderer: 'canvas' });
      _wcChart.setOption({
        tooltip: { show: true },
        series: [{
          type: 'wordCloud',
          shape: 'rect',
          sizeRange: [10, 22],
          rotationRange: [0, 0],
          gridSize: 4,
          drawOutOfBound: false,
          textStyle: {
            fontFamily: 'JetBrains Mono, Consolas, monospace',
            color: () => {
              const palette = ['#4ea1ff', '#22c55e', '#f59e0b', '#ef4444', '#8a95a3'];
              return palette[Math.floor(Math.random() * palette.length)];
            },
          },
          data: wordcloud.slice(0, 50),
        }],
      });
      window.addEventListener('resize', () => { if (_wcChart) _wcChart.resize(); });
    },
  });
})();
