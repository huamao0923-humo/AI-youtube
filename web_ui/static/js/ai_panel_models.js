(function () {
  const H = AiWarRoom;

  function dayOfRange(dateStr, rangeStart, rangeEnd) {
    // 回傳 0..1 的百分比位置
    const d = new Date(dateStr);
    if (isNaN(d)) return null;
    const span = rangeEnd - rangeStart;
    if (span <= 0) return null;
    return Math.max(0, Math.min(1, (d.getTime() - rangeStart) / span));
  }

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

      // 分組：per company
      const byCompany = {};
      for (const r of releases) {
        if (!r.company) continue;
        (byCompany[r.company] = byCompany[r.company] || []).push(r);
      }
      const companies = Object.keys(byCompany).sort((a, b) => byCompany[b].length - byCompany[a].length);

      // 時間範圍（近 180 天）
      const now = Date.now();
      const rangeStart = now - 180 * 86400 * 1000;
      const rangeEnd = now;

      const railsHtml = companies.length ? companies.map(co => {
        const dots = byCompany[co].map(r => {
          const pos = dayOfRange(r.date, rangeStart, rangeEnd);
          if (pos === null) return '';
          return `<div class="dot" style="left:${(pos * 100).toFixed(1)}%;" title="${H.escapeHtml(r.title || '')} · ${r.date}" onclick="window.open('${H.escapeHtml(r.url || '')}','_blank')"></div>`;
        }).join('');
        return `
          <div class="awr-model-rail">
            <div class="co">${H.escapeHtml(co)}</div>
            <div class="rail">${dots}</div>
          </div>`;
      }).join('') : '';

      // 時間軸刻度（-180d、-90d、-30d、今天）
      const axis = `
        <div class="awr-model-rail-axis">
          <div></div>
          <div class="ticks">
            <span>-180d</span><span>-90d</span><span>-30d</span><span>今</span>
          </div>
        </div>`;

      // Benchmarks — 按 LMArena 排
      let benchHtml = '';
      if (benchmarks.length) {
        const withLm = benchmarks.filter(b => typeof b.lmarena === 'number');
        if (withLm.length) {
          withLm.sort((a, b) => b.lmarena - a.lmarena);
          const max = Math.max(...withLm.map(b => b.lmarena));
          benchHtml = `
            <div class="awr-bench-title">🏆 LMArena TOP ${Math.min(10, withLm.length)}</div>
            ${withLm.slice(0, 10).map(b => `
              <div class="awr-bench-row">
                <div class="model" title="${H.escapeHtml(b.model)}">${H.escapeHtml(b.model)}</div>
                <div class="bar"><span style="width:${Math.round(100 * b.lmarena / max)}%;"></span></div>
                <div class="val">${b.lmarena}</div>
              </div>`).join('')}`;
        }
      }

      body.innerHTML = `
        ${railsHtml ? `
          <div class="awr-panel-meta" style="margin-bottom:4px;">模型發布時間軸（近 180 天，點 dot 開連結）</div>
          <div class="awr-model-rails">${railsHtml}</div>
          ${axis}
        ` : '<div class="awr-empty">近 180 天未偵測到模型發布</div>'}
        ${benchHtml}`;
    },
  });
})();
