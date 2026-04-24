(function () {
  const H = AiWarRoom;
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
      const lines = [];
      for (const it of items) {
        const used = st.usedSet.has(String(it.id));
        if (used && st.hideUsed && isAdmin) continue;
        const badge = it.feed_tag ? `<span class="awr-news-badge">${H.escapeHtml(it.feed_tag)}</span>` : '';
        const company = it.company ? `<span class="awr-news-badge" title="${H.escapeHtml(it.company)}">${H.escapeHtml(it.company)}</span>` : '';
        const epSlug = st.usedSlugMap[String(it.id)];
        const epLink = (!isAdmin && epSlug) ? `<a class="awr-episode-link" href="/episode/${encodeURIComponent(epSlug)}">👉 已製成影片</a>` : '';
        const actions = isAdmin
          ? `<div class="awr-news-actions">
               ${used ? '' : `<button class="awr-mini-btn" data-act="mark" data-id="${it.id}">✓ 標記</button>`}
               <a class="awr-mini-btn" href="/select" onclick="
                 const f=document.createElement('form');f.method='POST';f.action='/select';
                 f.innerHTML='<input name=news_id value=${it.id}><input name=angle value=A>';
                 document.body.appendChild(f);f.submit();return false;">→ 開集</a>
             </div>`
          : '';
        lines.push(`
          <li class="awr-news-item ${used ? 'used' : ''}">
            <div style="flex:1;">
              <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:2px;">
                ${badge}${company}
                <a class="awr-news-title" href="${H.escapeHtml(it.url || '#')}" target="_blank" rel="noopener">${H.escapeHtml(it.title || '(無標題)')}</a>
              </div>
              <div class="awr-news-meta">${H.escapeHtml(it.source || '')} · ${H.fmtTime(it.published_at)} ${epLink}</div>
            </div>
            ${actions}
          </li>`);
      }
      body.innerHTML = `<ul class="awr-news-list">${lines.join('') || '<div class="awr-empty">全部已用 — 可取消「隱藏已用」</div>'}</ul>`;
      // bind mark buttons
      body.querySelectorAll('[data-act="mark"]').forEach(btn => {
        btn.addEventListener('click', async () => {
          btn.disabled = true; btn.textContent = '處理中…';
          const ok = await H.markNewsUsed(btn.dataset.id);
          if (!ok) { btn.disabled = false; btn.textContent = '✓ 標記'; }
        });
      });
    },
  });
})();
