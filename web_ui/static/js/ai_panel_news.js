(function () {
  const H = AiWarRoom;

  const SECTIONS = [
    { feed: 'product',     emoji: '📦', name: '產品發布',    limit: 4 },
    { feed: 'funding',     emoji: '💰', name: '融資 / 商業', limit: 4 },
    { feed: 'partnership', emoji: '🤝', name: '合作',        limit: 3 },
    { feed: 'policy',      emoji: '📜', name: '政策 / 法規', limit: 3 },
    { feed: 'other',       emoji: '📰', name: '其他動態',    limit: 4 },
    { feed: 'research',    emoji: '🔬', name: '研究前沿',    limit: 4 },
  ];

  function scoreClass(score) {
    const s = score || 0;
    if (s >= 8) return 'heat-fire';
    if (s >= 7) return 'heat-hot';
    if (s >= 6) return 'heat-warm';
    if (s >= 4) return 'heat-cold';
    return 'heat-flat';
  }
  function feedLabel(tag) {
    const m = { product: 'PROD', funding: 'FUND', partnership: 'PART', research: 'RES', policy: 'POL', other: 'OTHER' };
    return m[tag] || 'NEWS';
  }

  function pickHero(items) {
    // 優先從 product / funding / partnership / policy 選；且優先有 summary 或 ai_score
    const byPriority = (arr) => {
      const withSub = arr.filter(it => (it.summary && it.summary.length > 20) || (it.ai_score || 0) > 0);
      return withSub.length ? withSub : arr;
    };
    const prefTags = items.filter(it => ['product','funding','partnership','policy'].includes(it.feed_tag));
    const pool = byPriority(prefTags.length ? prefTags : items);
    let best = pool[0], bestScore = best ? (best.ai_score || 0) : -1;
    for (const it of pool) {
      const s = it.ai_score || 0;
      if (s > bestScore) { best = it; bestScore = s; }
    }
    return best;
  }

  // 同一類別內：有 summary / 有分數的排前
  function sortForSection(arr) {
    return arr.slice().sort((a, b) => {
      const aHas = (a.summary && a.summary.length > 20) ? 1 : 0;
      const bHas = (b.summary && b.summary.length > 20) ? 1 : 0;
      if (aHas !== bHas) return bHas - aHas;
      const aS = a.ai_score || 0, bS = b.ai_score || 0;
      if (aS !== bS) return bS - aS;
      return 0;
    });
  }

  function renderHero(hero, st, isAdmin) {
    if (!hero) return '';
    const heroScore = Math.round((hero.ai_score || 0) * 10) / 10;
    const heroCls = scoreClass(hero.ai_score);
    const heroTag = feedLabel(hero.feed_tag);
    const heroCompany = hero.company ? `<span class="mono heat-flat" style="text-transform:uppercase;">${H.escapeHtml(hero.company)}</span>` : '';
    const heroEp = (!isAdmin && st.usedSlugMap[String(hero.id)])
      ? `<a class="awr-episode-link" href="/episode/${encodeURIComponent(st.usedSlugMap[String(hero.id)])}">▶ 已製成影片</a>` : '';
    const heroActions = isAdmin ? `
      <div style="display:flex;gap:4px;margin-left:auto;">
        ${st.usedSet.has(String(hero.id)) ? '' : `<button class="awr-mini-btn" data-act="mark" data-id="${hero.id}">✓ 已用</button>`}
        <button class="awr-mini-btn" data-act="open" data-id="${hero.id}">→ 開集</button>
      </div>` : '';
    const summary = hero.summary ? `<div class="awr-news-hero-summary">${H.escapeHtml(hero.summary)}</div>` : '';
    return `
      <div class="awr-news-hero ${st.usedSet.has(String(hero.id)) ? 'used' : ''}">
        <div class="awr-news-hero-score">
          <div class="n ${heroCls}">${heroScore || '—'}</div>
          <div class="lbl">SCORE</div>
        </div>
        <div class="awr-news-hero-body">
          <a class="awr-news-hero-title" href="${H.escapeHtml(hero.url || '#')}" target="_blank" rel="noopener">
            ${H.escapeHtml(hero.title || '(無標題)')}
          </a>
          ${summary}
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
  }

  function renderRow(it, st, isAdmin, opts = {}) {
    const showSummary = opts.showSummary !== false;
    const used = st.usedSet.has(String(it.id));
    const score = Math.round((it.ai_score || 0) * 10) / 10;
    const sCls = scoreClass(it.ai_score);
    const tag = feedLabel(it.feed_tag);
    const epSlug = st.usedSlugMap[String(it.id)];
    const epLink = (!isAdmin && epSlug) ? `<a class="awr-episode-link" href="/episode/${encodeURIComponent(epSlug)}">▶ EP</a>` : '';
    const actions = isAdmin ? `
      ${used ? '<span class="mono" style="color:var(--awr-dim);font-size:10px;">USED</span>'
             : `<button class="awr-mini-btn" data-act="mark" data-id="${it.id}">✓</button>`}
      <button class="awr-mini-btn" data-act="open" data-id="${it.id}">→</button>
    ` : epLink;
    const summaryLine = (showSummary && it.summary) ? `
      <div class="awr-news-row-summary">${H.escapeHtml(it.summary)}</div>
    ` : '';

    return `
      <li class="awr-news-row2 ${used ? 'used' : ''}">
        <div class="r1">
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
        </div>
        ${summaryLine}
      </li>`;
  }

  function renderAllSections(items, st, isAdmin) {
    const hero = pickHero(items);
    const byFeed = { product: [], funding: [], partnership: [], research: [], policy: [], other: [] };
    for (const it of items) {
      if (hero && it.id === hero.id) continue;
      (byFeed[it.feed_tag] || byFeed.other).push(it);
    }

    const sectionsHtml = SECTIONS.map(sec => {
      const bucket = byFeed[sec.feed] || [];
      if (!bucket.length) return '';
      const sorted = sortForSection(bucket);
      const rows = sorted.slice(0, sec.limit).map(it => renderRow(it, st, isAdmin, { showSummary: true })).join('');
      const more = bucket.length > sec.limit
        ? `<div class="awr-news-section-more"><button class="awr-mini-btn" data-jump-feed="${sec.feed}">看全部 ${sec.name} (${bucket.length}) →</button></div>`
        : '';
      const summary = renderCategorySummary(sec, bucket.length, st, isAdmin);
      return `
        <div class="awr-news-section">
          <div class="awr-news-section-title">
            <span class="em">${sec.emoji}</span>
            <span class="nm">${sec.name}</span>
            <span class="cn">${bucket.length}</span>
          </div>
          ${summary}
          <ul class="awr-news-list">${rows}</ul>
          ${more}
        </div>`;
    }).filter(Boolean).join('');

    return renderHero(hero, st, isAdmin) + sectionsHtml;
  }

  function renderCategorySummary(sec, newsCount, st, isAdmin) {
    const cs = (st.categorySummaries || {})[sec.feed];
    const rerunBtn = isAdmin
      ? `<button class="awr-mini-btn awr-cat-rerun" data-feed="${sec.feed}">🔄 重生</button>`
      : '';
    if (!cs || !cs.summary_zh) {
      return `
        <div class="awr-cat-summary awr-cat-empty">
          <div class="awr-cat-summary-head">
            <span class="awr-cat-summary-label">📝 今日總結</span>
            <span class="awr-cat-summary-meta">尚未生成（每天 06:55 自動跑）</span>
            ${rerunBtn}
          </div>
          <div class="awr-cat-summary-body">
            點右側「🔄 重生」立刻產出 400~600 字的繁中總結（會跑 30~120 秒）。
          </div>
        </div>`;
    }
    const word = cs.word_count || (cs.summary_zh || '').length;
    const when = cs.generated_at ? H.fmtTime(cs.generated_at) : '';
    return `
      <div class="awr-cat-summary">
        <div class="awr-cat-summary-head">
          <span class="awr-cat-summary-label">📝 今日總結 · ${sec.name}</span>
          <span class="awr-cat-summary-meta">${cs.news_count || 0} 則新聞 · ${word} 字 · ${when}</span>
          ${rerunBtn}
        </div>
        <div class="awr-cat-summary-body">${H.escapeHtml(cs.summary_zh)}</div>
      </div>`;
  }

  function renderSingleFeed(items, st, isAdmin) {
    const hero = pickHero(items);
    const rest = items.filter(it => !hero || it.id !== hero.id);
    const rows = rest.map(it => renderRow(it, st, isAdmin, { showSummary: true })).join('');
    // 單一 feed 模式下，從 SECTIONS 反查當前 feed 的 emoji/name
    const sec = SECTIONS.find(s => s.feed === st.feed)
      || { feed: st.feed, emoji: '📰', name: st.feed };
    const summary = renderCategorySummary(sec, items.length, st, isAdmin);
    return renderHero(hero, st, isAdmin) + summary + `<ul class="awr-news-list">${rows}</ul>`;
  }

  H.register({
    id: 'panel-news',
    fetch: async (st) => {
      const r = await fetch(`/api/ai/latest-news?limit=60&feed=${encodeURIComponent(st.feed)}`);
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
      let list = items;
      if (isAdmin && st.hideUsed) {
        list = items.filter(it => !st.usedSet.has(String(it.id)));
      }
      if (!list.length) {
        body.innerHTML = '<div class="awr-empty">全部已用 — 取消「隱藏已用」可看</div>';
        return;
      }

      body.innerHTML = (st.feed === 'all')
        ? renderAllSections(list, st, isAdmin)
        : renderSingleFeed(list, st, isAdmin);

      // 「看全部」按鈕 → 切到該 feed tab
      body.querySelectorAll('[data-jump-feed]').forEach(btn => {
        btn.addEventListener('click', () => {
          const feed = btn.dataset.jumpFeed;
          const tabs = document.getElementById('awr-news-tabs');
          if (tabs) {
            const target = tabs.querySelector(`[data-feed="${feed}"]`);
            if (target) target.click();
          }
        });
      });

      // 「🔄 重生」類別摘要按鈕（admin only）
      body.querySelectorAll('.awr-cat-rerun').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.preventDefault(); e.stopPropagation();
          if (btn.disabled) return;
          btn.disabled = true;
          const orig = btn.textContent;
          btn.textContent = '⏳ 啟動中…';
          const ok = await H.rerunCategorySummary(btn.dataset.feed);
          if (!ok) {
            btn.disabled = false;
            btn.textContent = orig;
          } else {
            btn.textContent = '⏳ 60s 後更新';
          }
        });
      });

      // mark / open
      body.querySelectorAll('[data-act="mark"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.preventDefault(); e.stopPropagation();
          btn.disabled = true; btn.textContent = '...';
          const ok = await H.markNewsUsed(btn.dataset.id);
          if (!ok) { btn.disabled = false; btn.textContent = '✓'; }
        });
      });
      body.querySelectorAll('[data-act="open"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.preventDefault(); e.stopPropagation();
          if (btn.disabled) return;
          // 從同 row 裡找標題（hero 與 row2 都有 .awr-news-hero-title / .title）
          const card = btn.closest('.awr-news-hero, .awr-news-row2, .awr-news-row');
          const titleEl = card ? card.querySelector('.awr-news-hero-title, .title') : null;
          const title = titleEl ? titleEl.textContent.trim() : ('#' + btn.dataset.id);
          btn.disabled = true;
          const orig = btn.textContent;
          btn.textContent = '⏳';
          const result = await H.selectNews(btn.dataset.id, title);
          if (!result) {
            btn.disabled = false;
            btn.textContent = orig;
          } else {
            btn.textContent = '✓';
          }
        });
      });
    },
  });
})();
