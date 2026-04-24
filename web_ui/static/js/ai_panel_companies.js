(function () {
  const H = AiWarRoom;
  H.register({
    id: 'panel-companies',
    fetch: async () => {
      const r = await fetch('/api/ai/company-activity');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    },
    render: (root, items) => {
      const body = root.querySelector('.awr-panel-body');
      if (!items || !items.length) {
        body.innerHTML = '<div class="awr-empty">暫無公司活動</div>';
        return;
      }
      const cards = items.map(c => {
        const title = c.latest ? H.escapeHtml((c.latest.title || '').slice(0, 80)) : '';
        const when = c.latest ? H.fmtTime(c.latest.published_at) : '尚無動態';
        const logo = c.logo
          ? `<img src="${H.escapeHtml(c.logo)}" alt="${H.escapeHtml(c.name || '')}" loading="lazy" onerror="this.style.display='none'">`
          : '';
        return `
          <div class="awr-company-card" title="${title}" ${c.latest ? `onclick="window.open('${H.escapeHtml(c.latest.url)}','_blank')"` : ''}>
            <div class="awr-company-logo">${logo}</div>
            <div class="awr-company-name">${H.escapeHtml(c.name || c.company_key)}</div>
            <div class="awr-company-counts">
              <span class="v24" title="24h 新聞數">24h: ${c.count_24h || 0}</span>
              <span class="v7" title="7d 新聞數">7d: ${c.count_7d || 0}</span>
            </div>
            <div style="font-size:10px;color:var(--awr-muted);margin-top:2px;">${when}</div>
          </div>`;
      }).join('');
      body.innerHTML = `<div class="awr-companies">${cards}</div>`;
    },
  });
})();
