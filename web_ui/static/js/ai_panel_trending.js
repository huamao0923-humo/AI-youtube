(function () {
  const H = AiWarRoom;
  let _wcChart = null;

  H.register({
    id: 'panel-trending',
    fetch: async (st) => {
      const r = await fetch(`/api/ai/trending-topics?window=${encodeURIComponent(st.window)}`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    },
    render: (root, data) => {
      const body = root.querySelector('.awr-panel-body');
      const topics = data.topics || [];
      const wordcloud = data.wordcloud || [];
      body.innerHTML = `
        <div class="awr-trending-wrap">
          <div class="awr-trending-list">
            ${topics.length ? topics.map((t, i) => `
              <div class="awr-trending-item">
                <span class="rank">${i + 1}</span>
                <a class="title" href="/topic/${encodeURIComponent(t.slug)}" title="${H.escapeHtml(t.title)}">${H.escapeHtml(t.title.slice(0, 50))}</a>
                <span class="arrow ${t.arrow}">${t.arrow === 'up' ? '▲' : t.arrow === 'down' ? '▼' : '—'}</span>
                <span style="color:var(--awr-muted);font-size:11px;">${t.news_count} 則</span>
              </div>`).join('') : '<div class="awr-empty">暫無熱門主題</div>'}
          </div>
          <div class="awr-trending-wc" id="awr-wc-container"></div>
        </div>`;
      const wcEl = body.querySelector('#awr-wc-container');
      if (!wcEl || !window.echarts) return;
      if (_wcChart) { try { _wcChart.dispose(); } catch (e) {} _wcChart = null; }
      if (!wordcloud.length) return;
      _wcChart = window.echarts.init(wcEl, null, { renderer: 'canvas' });
      _wcChart.setOption({
        tooltip: { show: true },
        series: [{
          type: 'wordCloud',
          shape: 'circle',
          sizeRange: [12, 38],
          rotationRange: [-30, 30],
          gridSize: 6,
          drawOutOfBound: false,
          textStyle: {
            fontFamily: 'sans-serif',
            color: () => `rgb(${80 + Math.floor(Math.random() * 100)},${140 + Math.floor(Math.random() * 100)},${200 + Math.floor(Math.random() * 55)})`,
          },
          data: wordcloud,
        }],
      });
      window.addEventListener('resize', () => { if (_wcChart) _wcChart.resize(); });
    },
  });
})();
