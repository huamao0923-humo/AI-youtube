(function () {
  const H = AiWarRoom;
  let _timelineChart = null;

  H.register({
    id: 'panel-models',
    fetch: async () => {
      const r = await fetch('/api/ai/model-timeline?days=180');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    },
    render: (root, data) => {
      const body = root.querySelector('.awr-panel-body');
      const releases = (data.releases || []).filter(x => x.date);
      const benchmarks = data.benchmarks || [];

      body.innerHTML = `
        <div class="awr-models-wrap">
          <div class="awr-timeline" id="awr-timeline-chart"></div>
          <div class="awr-benchmarks">
            <div style="font-size:12px;color:var(--awr-muted);margin-bottom:6px;">LMArena 排行</div>
            ${benchmarks.length ? _renderBench(benchmarks) : '<div class="awr-empty">尚無資料</div>'}
          </div>
        </div>
        ${releases.length === 0 ? '<div class="awr-empty" style="margin-top:10px;">近 180 天未偵測到模型發布</div>' : ''}`;

      const tEl = body.querySelector('#awr-timeline-chart');
      if (!tEl || !window.echarts) return;
      if (_timelineChart) { try { _timelineChart.dispose(); } catch (e) {} _timelineChart = null; }
      if (!releases.length) return;

      const byCompany = {};
      releases.forEach(r => {
        if (!r.company) return;
        (byCompany[r.company] = byCompany[r.company] || []).push(r);
      });
      const companies = Object.keys(byCompany);
      const series = companies.map((c, i) => ({
        name: c,
        type: 'scatter',
        symbolSize: 14,
        data: byCompany[c].map(r => ({
          value: [r.date, i],
          title: r.title,
          url: r.url,
        })),
      }));

      _timelineChart = window.echarts.init(tEl, null, { renderer: 'canvas' });
      _timelineChart.setOption({
        grid: { top: 20, left: 100, right: 20, bottom: 40 },
        tooltip: {
          formatter: p => `<b>${p.data.title || ''}</b><br>${p.seriesName} · ${p.data.value[0]}`,
        },
        xAxis: { type: 'time', axisLabel: { color: '#8a95a3' } },
        yAxis: {
          type: 'category',
          data: companies,
          axisLabel: { color: '#e5eaf0' },
        },
        series: series,
      });
      _timelineChart.off('click');
      _timelineChart.on('click', p => { if (p.data && p.data.url) window.open(p.data.url, '_blank'); });
      window.addEventListener('resize', () => { if (_timelineChart) _timelineChart.resize(); });
    },
  });

  function _renderBench(list) {
    const metric = 'lmarena';
    const withVal = list.filter(x => typeof x[metric] === 'number');
    if (!withVal.length) return '<div class="awr-empty">無 LMArena 分數</div>';
    const max = Math.max(...withVal.map(x => x[metric]));
    withVal.sort((a, b) => b[metric] - a[metric]);
    return withVal.slice(0, 10).map(x => `
      <div class="awr-bench-row">
        <div class="model" title="${H.escapeHtml(x.model)}">${H.escapeHtml(x.model)}</div>
        <div class="bar"><span style="width:${Math.round(100 * x[metric] / max)}%"></span></div>
        <div class="val">${x[metric]}</div>
      </div>`).join('');
  }
})();
