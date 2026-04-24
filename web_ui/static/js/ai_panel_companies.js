(function () {
  const H = AiWarRoom;

  function sparkline(count7d, max) {
    const slots = 6;
    const filled = max > 0 ? Math.min(slots, Math.ceil(count7d / max * slots)) : 0;
    const bars = [];
    for (let i = 0; i < slots; i++) {
      let cls = '';
      if (i < filled) {
        if (filled >= slots) cls = 'fire';
        else if (filled >= slots - 1) cls = 'hot';
        else cls = 'on';
      }
      bars.push(`<div class="bar ${cls}" style="height:${4 + i * 1.2}px;"></div>`);
    }
    return `<div class="awr-spark">${bars.join('')}</div>`;
  }

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

      // 分活躍 / 靜默
      const active = items.filter(c => (c.count_24h || 0) > 0 || (c.count_7d || 0) >= 3);
      const silent = items.filter(c => !((c.count_24h || 0) > 0 || (c.count_7d || 0) >= 3));

      // 活躍度排序
      active.sort((a, b) => (b.count_24h || 0) - (a.count_24h || 0) || (b.count_7d || 0) - (a.count_7d || 0));

      const max7d = Math.max(1, ...active.map(c => c.count_7d || 0));

      const activeHtml = active.map(c => {
        const when = c.latest ? H.fmtTime(c.latest.published_at) : '—';
        const latestTitle = c.latest ? H.escapeHtml((c.latest.title || '').slice(0, 70)) : '<span style="color:var(--awr-dim);">尚無動態</span>';
        const c24 = c.count_24h || 0, c7 = c.count_7d || 0;
        const cls24 = c24 >= 3 ? 'heat-fire' : c24 >= 1 ? 'heat-hot' : 'heat-flat';
        const logo = c.logo ? `<img src="${H.escapeHtml(c.logo)}" alt="" loading="lazy" onerror="this.style.display='none'">` : '';
        return `
          <div class="awr-cos-row" data-company="${H.escapeHtml(c.company_key)}" data-name="${H.escapeHtml(c.name || c.company_key)}">
            <div class="logo">${logo}</div>
            <div class="name">${H.escapeHtml(c.name || c.company_key)}</div>
            <div class="counts">
              <span class="n ${cls24}">${c24}</span><span class="lbl">24H</span>
              <span class="n" style="color:var(--awr-muted);">${c7}</span><span class="lbl">7D</span>
              ${sparkline(c7, max7d)}
            </div>
            <div class="latest" title="${latestTitle}">
              <span class="when">${when}</span>${latestTitle}
            </div>
          </div>`;
      }).join('');

      const silentHtml = silent.length ? `
        <div class="awr-cos-silent-title">靜默公司（${silent.length}）</div>
        <div class="awr-cos-silent-bar">
          ${silent.map(c => {
            const logo = c.logo ? `<img src="${H.escapeHtml(c.logo)}" alt="" onerror="this.style.display='none'">` : '';
            return `<div class="sil" title="${H.escapeHtml(c.name || c.company_key)} · 無近期動態">${logo}${H.escapeHtml(c.name || c.company_key)}</div>`;
          }).join('')}
        </div>` : '';

      body.innerHTML = `
        <div class="awr-cos-list">${activeHtml || '<div class="awr-empty">當日無活躍公司</div>'}</div>
        ${silentHtml}`;

      // 綁定 row 點擊 → 開公司 modal
      body.querySelectorAll('[data-company]').forEach(row => {
        row.addEventListener('click', () => _openCompanyModal(row.dataset.company, row.dataset.name));
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
      dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    }
    document.getElementById('awr-company-modal-title').textContent = `🏢 ${companyName} · 近期新聞`;
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
        <ul class="awr-news-list">
          ${items.map(it => `
            <li class="awr-news-row">
              <div class="badge">${H.escapeHtml((it.category || '').toUpperCase().slice(0, 6))}</div>
              <div class="score"></div>
              <a class="title" href="${H.escapeHtml(it.url || '#')}" target="_blank" rel="noopener">${H.escapeHtml(it.title || '(無標題)')}</a>
              <div class="meta">
                <span style="color:var(--awr-muted);">${H.escapeHtml(it.source || '')}</span>
                <span>${H.fmtTime(it.published_at)}</span>
              </div>
            </li>`).join('')}
        </ul>`;
    } catch (e) {
      bodyEl.innerHTML = `<div class="awr-error">載入失敗：${e.message || e}</div>`;
    }
  }
})();
