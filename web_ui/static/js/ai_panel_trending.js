(function () {
  const H = AiWarRoom;
  let _wcChart = null;
  // 已展開的 topic_id 集合（在 re-render 時保留展開狀態）
  const _expanded = new Set();

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

  function fmtScore(s) {
    if (s === null || s === undefined || isNaN(s)) return '—';
    return Number(s).toFixed(1);
  }

  function fmtDate(s) {
    if (!s) return '';
    return String(s).slice(0, 10);
  }

  function clip(s, n) {
    if (!s) return '';
    s = String(s);
    return s.length > n ? s.slice(0, n) + '…' : s;
  }

  function renderDetail(t) {
    const esc = H.escapeHtml;
    const summary = t.summary_zh ? `<div class="awr-detail-summary">📝 ${esc(t.summary_zh)}</div>`
                                 : `<div class="awr-detail-summary awr-empty">（彙總摘要尚未生成；可在系統頁觸發「主題彙總摘要」）</div>`;

    const angleParts = [];
    if (t.business_angle) angleParts.push(`<div class="awr-angle-row"><span class="lbl">商業角度</span>${esc(t.business_angle)}</div>`);
    if (t.why_audience_cares) angleParts.push(`<div class="awr-angle-row"><span class="lbl">觀眾在意</span>${esc(t.why_audience_cares)}</div>`);
    if (t.suggested_title) angleParts.push(`<div class="awr-angle-row"><span class="lbl">建議標題</span>${esc(t.suggested_title)}</div>`);
    const angles = angleParts.length ? `<div class="awr-detail-angles">${angleParts.join('')}</div>` : '';

    const chips = [];
    if (t.ai_score != null) chips.push(`<span class="awr-chip awr-chip-score">⭐ ${fmtScore(t.ai_score)}</span>`);
    if (t.model_release) chips.push(`<span class="awr-chip awr-chip-model">🆕 模型發布</span>`);
    (t.companies || []).forEach(c => chips.push(`<span class="awr-chip awr-chip-co">${esc(c)}</span>`));
    if (t.first_seen_date) chips.push(`<span class="awr-chip awr-chip-date">首見 ${fmtDate(t.first_seen_date)}</span>`);
    if (t.last_seen_date && t.last_seen_date !== t.first_seen_date) {
      chips.push(`<span class="awr-chip awr-chip-date">最新 ${fmtDate(t.last_seen_date)}</span>`);
    }
    const chipRow = chips.length ? `<div class="awr-detail-chips">${chips.join('')}</div>` : '';

    const newsList = (t.news_items || []).map(n => {
      const score = n.ai_score != null ? `<span class="awr-news-score">⭐${fmtScore(n.ai_score)}</span>` : '';
      const sum = n.summary_zh ? `<div class="awr-news-summary">${esc(clip(n.summary_zh, 110))}</div>` : '';
      return `
        <li class="awr-news-row">
          <a class="awr-news-link" href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title || '(無標題)')}</a>
          <span class="awr-news-meta">${score}<span class="awr-news-source">${esc(n.source_name || '')}</span></span>
          ${sum}
        </li>`;
    }).join('');
    const newsBlock = newsList
      ? `<ul class="awr-news-list">${newsList}</ul>`
      : `<div class="awr-empty">無相關新聞</div>`;

    const isAdmin = (H.state && H.state.mode === 'admin');
    const usedKey = 'topic_' + t.slug;
    const isUsed = H.state && H.state.usedSet && H.state.usedSet.has(usedKey);
    const selectBtn = (isAdmin && !isUsed)
      ? `<button class="awr-btn awr-btn-primary awr-btn-select"
                 data-topic-id="${t.topic_id}"
                 data-title="${esc(t.title)}">🎬 選為本集題目</button>`
      : (isUsed ? '<span class="awr-btn awr-btn-secondary" style="opacity:.5;cursor:default">已開集</span>' : '');
    const actions = `
      <div class="awr-detail-actions">
        ${selectBtn}
        <a class="awr-btn awr-btn-secondary" href="/topic/${encodeURIComponent(t.slug)}" target="_blank" rel="noopener">📂 完整詳情</a>
      </div>`;

    return `
      <div class="awr-trend-detail">
        ${summary}
        ${angles}
        ${chipRow}
        <div class="awr-detail-section-title">🔗 來源新聞 (${(t.news_items || []).length})</div>
        ${newsBlock}
        ${actions}
      </div>`;
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
          const expanded = _expanded.has(t.topic_id);
          const detailHtml = expanded ? renderDetail(t) : '';
          return `
            <li class="awr-trend-item ${expanded ? 'expanded' : ''}" data-topic-id="${t.topic_id}">
              <div class="awr-trend-row" data-slug="${H.escapeHtml(t.slug)}">
                <div class="rank">${i + 1}.</div>
                <div class="bar-wrap">
                  <div class="bar ${bgCls}" style="width:${Math.max(width, 3)}%;"></div>
                  <span class="title" title="${H.escapeHtml(t.title)}">${H.escapeHtml(t.title)}</span>
                </div>
                <div class="count">${t.news_count || 0}</div>
                <div class="delta ${hCls}">${arrow(pct)} ${pct > 0 ? '+' : ''}${pct}%</div>
                <div class="awr-trend-toggle">${expanded ? '▲' : '▼'}</div>
              </div>
              <div class="awr-trend-detail-wrap">${detailHtml}</div>
            </li>`;
        }).join('');
      })() : '<li class="awr-empty">暫無熱門主題（資料聚類後就會出現）</li>';

      const fallbackNote = data.fallback
        ? `<div class="awr-empty" style="padding:4px 0;font-size:10px;text-align:left;">⚠ 無 ${data.window || st.window} 內新主題，顯示全期熱度榜</div>`
        : '';
      body.innerHTML = `
        ${fallbackNote}
        <ul class="awr-trend-list">${listHtml}</ul>
        <div class="awr-trend-wc" id="awr-wc-container"></div>`;

      // 點 row → toggle 展開（不再跳 /topic/{slug}）
      body.querySelectorAll('.awr-trend-item').forEach(item => {
        const row = item.querySelector('.awr-trend-row');
        const wrap = item.querySelector('.awr-trend-detail-wrap');
        const toggle = item.querySelector('.awr-trend-toggle');
        const tid = parseInt(item.dataset.topicId, 10);
        const t = topics.find(x => x.topic_id === tid);
        row.addEventListener('click', (e) => {
          // 連結點擊（detail 區塊內的 <a>）不觸發 toggle
          if (e.target.closest('a')) return;
          const isExpanded = item.classList.toggle('expanded');
          if (isExpanded) {
            _expanded.add(tid);
            if (t && wrap.innerHTML.trim() === '') wrap.innerHTML = renderDetail(t);
            toggle.textContent = '▲';
          } else {
            _expanded.delete(tid);
            toggle.textContent = '▼';
          }
        });
        // detail 內的 <a> 與 button 不冒泡 toggle
        wrap.addEventListener('click', (e) => {
          if (e.target.closest('a') || e.target.closest('button')) e.stopPropagation();
        });
      });

      // 「🎬 選為本集題目」按鈕
      body.querySelectorAll('.awr-btn-select').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.preventDefault(); e.stopPropagation();
          if (btn.disabled) return;
          btn.disabled = true;
          const orig = btn.textContent;
          btn.textContent = '⏳ 建立中…';
          const result = await H.selectTopic(parseInt(btn.dataset.topicId, 10), btn.dataset.title);
          if (!result) {
            btn.disabled = false;
            btn.textContent = orig;
          } else {
            btn.textContent = '✓ 已開集';
          }
        });
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
