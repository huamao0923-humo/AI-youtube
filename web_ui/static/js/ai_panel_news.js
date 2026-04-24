(function () {
  const H = AiWarRoom;

  function scoreClass(score) {
    const s = score || 0;
    if (s >= 8) return 'heat-fire';
    if (s >= 7) return 'heat-hot';
    if (s >= 6) return 'heat-warm';
    if (s >= 4) return 'heat-cold';
    return 'heat-flat';
  }

  function feedBadgeLabel(tag) {
    const m = { product: 'PROD', funding: 'FUND', partnership: 'PART', research: 'RES', policy: 'POL' };
    return m[tag] || tag || 'NEWS';
  }

  function pickHero(items) {
    // 選 ai_score 最高的 1 則；若全無分數用第一則
    if (!items.length) return null;
    let best = items[0], bestScore = best.ai_score || 0;
    for (const it of items) {
      const s = it.ai_score || 0;
      if (s > bestScore) { best = it; bestScore = s; }
    }
    return best;
  }

  H.register({
    id: 'panel-news',
    fetch: async (st) => {
      const r = await fetch(`/api/ai/latest-news?limit=40&feed=${encodeURIComponent(st.feed)}`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    },
    render: (root, items, st) => {
      const body = root.querySelector('.awr-panel-body');
      if (!items || !items.length) {
        body.innerHTML = '<div class="awr-empty">暫無新聞</div>';
        return;
      }

      const isAdmin = st.mode === 'admin';
      // 先過濾 used（admin 且勾了 hideUsed）
      let list = items;
      if (isAdmin && st.hideUsed) {
        list = items.filter(it => !st.usedSet.has(String(it.id)));
      }
      if (!list.length) {
        body.innerHTML = '<div class="awr-empty">全部已用 — 取消「隱藏已用」可看</div>';
        return;
      }

      const hero = pickHero(list);
      const rest = list.filter(it => it.id !== hero.id).slice(0, 6);

      // Hero block
      const heroScore = Math.round((hero.ai_score || 0) * 10) / 10;
      const heroCls = scoreClass(hero.ai_score);
      const heroTag = feedBadgeLabel(hero.feed_tag);
      const heroCompany = hero.company ? `<span class="mono heat-flat" style="text-transform:uppercase;">${H.escapeHtml(hero.company)}</span>` : '';
      const heroEp = (!isAdmin && st.usedSlugMap[String(hero.id)])
        ? `<a class="awr-episode-link" href="/episode/${encodeURIComponent(st.usedSlugMap[String(hero.id)])}">▶ 已製成影片</a>` : '';
      const heroActions = isAdmin ? `
        <div style="display:flex;gap:4px;">
          ${st.usedSet.has(String(hero.id)) ? '' : `<button class="awr-mini-btn" data-act="mark" data-id="${hero.id}">✓ 已用</button>`}
          <button class="awr-mini-btn" data-act="open" data-id="${hero.id}">→ 開集</button>
        </div>` : '';

      const heroHtml = `
        <div class="awr-news-hero ${st.usedSet.has(String(hero.id)) ? 'used' : ''}">
          <div class="awr-news-hero-score">
            <div class="n ${heroCls}">${heroScore || '—'}</div>
            <div class="lbl">SCORE</div>
          </div>
          <div class="awr-news-hero-body">
            <a class="awr-news-hero-title" href="${H.escapeHtml(hero.url || '#')}" target="_blank" rel="noopener">
              ${H.escapeHtml(hero.title || '(無標題)')}
            </a>
            <div class="awr-news-hero-meta">
              <span class="mono" style="color:var(--awr-muted);text-transform:uppercase;letter-spacing:0.04em;">${heroTag}</span>
              ${heroCompany}
              <span style="color:var(--awr-dim);">${H.escapeHtml(hero.source || '')}</span>
              <span class="mono" style="color:var(--awr-dim);">${H.fmtTime(hero.published_at)}</span>
              ${heroEp}
              ${heroActions}
            </div>
          </div>
        </div>`;

      // Compact rows
      const rowsHtml = rest.map(it => {
        const used = st.usedSet.has(String(it.id));
        const score = Math.round((it.ai_score || 0) * 10) / 10;
        const sCls = scoreClass(it.ai_score);
        const tag = feedBadgeLabel(it.feed_tag);
        const epSlug = st.usedSlugMap[String(it.id)];
        const epLink = (!isAdmin && epSlug) ? `<a class="awr-episode-link" href="/episode/${encodeURIComponent(epSlug)}">▶ EP</a>` : '';
        const actions = isAdmin ? `
          ${used ? '<span class="mono" style="color:var(--awr-dim);font-size:10px;">USED</span>'
                 : `<button class="awr-mini-btn" data-act="mark" data-id="${it.id}">✓</button>`}
          <button class="awr-mini-btn" data-act="open" data-id="${it.id}">→</button>
        ` : epLink;

        return `
          <li class="awr-news-row ${used ? 'used' : ''}">
            <div class="badge">${tag}</div>
            <div class="score ${sCls}">${score || '—'}</div>
            <a class="title" href="${H.escapeHtml(it.url || '#')}" target="_blank" rel="noopener">
              ${H.escapeHtml(it.title || '(無標題)')}
            </a>
            <div class="meta">
              ${it.company ? `<span style="color:var(--awr-muted);">${H.escapeHtml(it.company)}</span>` : ''}
              <span>${H.fmtTime(it.published_at)}</span>
              ${actions}
            </div>
          </li>`;
      }).join('');

      body.innerHTML = heroHtml + `<ul class="awr-news-list">${rowsHtml}</ul>`;

      // bind mark / open buttons
      body.querySelectorAll('[data-act="mark"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.preventDefault(); e.stopPropagation();
          btn.disabled = true; btn.textContent = '...';
          const ok = await H.markNewsUsed(btn.dataset.id);
          if (!ok) { btn.disabled = false; btn.textContent = '✓'; }
        });
      });
      body.querySelectorAll('[data-act="open"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault(); e.stopPropagation();
          const f = document.createElement('form');
          f.method = 'POST'; f.action = '/select';
          f.innerHTML = `<input name="news_id" value="${btn.dataset.id}"><input name="angle" value="A">`;
          document.body.appendChild(f); f.submit();
        });
      });
    },
  });
})();
