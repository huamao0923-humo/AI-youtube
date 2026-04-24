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
        const when = c.latest ? H.fmtTime(c.latest.published_at) : '尚無動態';
        const logo = c.logo
          ? `<img src="${H.escapeHtml(c.logo)}" alt="${H.escapeHtml(c.name || '')}" loading="lazy" onerror="this.style.display='none'">`
          : '';
        const clickable = (c.count_7d > 0 || c.count_24h > 0) ? `data-company="${H.escapeHtml(c.company_key)}" data-name="${H.escapeHtml(c.name || '')}"` : '';
        return `
          <div class="awr-company-card" ${clickable}>
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

      // 綁定卡片點擊事件 → 開 modal
      body.querySelectorAll('[data-company]').forEach(card => {
        card.addEventListener('click', () => _openCompanyModal(card.dataset.company, card.dataset.name));
      });
    },
  });

  async function _openCompanyModal(companyKey, companyName) {
    let dlg = document.getElementById('awr-company-modal');
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.id = 'awr-company-modal';
      dlg.className = 'awr-company-modal';
      dlg.innerHTML = `
        <div class="awr-company-modal-inner">
          <div class="awr-company-modal-head">
            <h3 id="awr-company-modal-title">...</h3>
            <button class="awr-mini-btn" onclick="document.getElementById('awr-company-modal').close()">✕ 關閉</button>
          </div>
          <div class="awr-company-modal-body" id="awr-company-modal-body">
            <div class="awr-loading">載入中…</div>
          </div>
        </div>`;
      document.body.appendChild(dlg);
      // 點背景關閉
      dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    }
    document.getElementById('awr-company-modal-title').textContent = `🏢 ${companyName} 近期新聞`;
    const bodyEl = document.getElementById('awr-company-modal-body');
    bodyEl.innerHTML = '<div class="awr-loading">載入中…</div>';
    if (typeof dlg.showModal === 'function') dlg.showModal();

    try {
      const r = await fetch(`/api/ai/company-news/${encodeURIComponent(companyKey)}?limit=30`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const items = await r.json();
      if (!items.length) {
        bodyEl.innerHTML = '<div class="awr-empty">此公司近期無相關新聞</div>';
        return;
      }
      bodyEl.innerHTML = `
        <ul class="awr-news-list" style="max-height:70vh;">
          ${items.map(it => `
            <li class="awr-news-item">
              <div style="flex:1;">
                <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:2px;">
                  ${it.category ? `<span class="awr-news-badge">${H.escapeHtml(it.category)}</span>` : ''}
                  <a class="awr-news-title" href="${H.escapeHtml(it.url || '#')}" target="_blank" rel="noopener">${H.escapeHtml(it.title || '(無標題)')}</a>
                </div>
                <div class="awr-news-meta">${H.escapeHtml(it.source || '')} · ${H.fmtTime(it.published_at)}</div>
              </div>
            </li>`).join('')}
        </ul>`;
    } catch (e) {
      bodyEl.innerHTML = `<div class="awr-error">載入失敗：${e.message || e}</div>`;
    }
  }
})();
