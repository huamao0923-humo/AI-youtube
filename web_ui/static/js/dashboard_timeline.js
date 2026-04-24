// 事件時間軸 — ECharts scatter，30 日 snapshot 氣泡散點。
(function () {
  const el = document.getElementById('wm-timeline-chart');
  const filtersEl = document.getElementById('wm-timeline-filters');
  if (!el || typeof echarts === 'undefined') return;

  const CATEGORIES = ['ai_model', 'business', 'policy', 'product', 'semiconductor', 'other'];
  const catLabels = {
    ai_model: 'AI 模型', business: '商業', policy: '政策',
    product: '產品', semiconductor: '半導體', other: '其他',
  };
  const catColors = {
    ai_model: '#8b5cf6', business: '#f59e0b', policy: '#ef4444',
    product: '#3b82f6', semiconductor: '#22c55e', other: '#71717a',
  };

  let currentCategory = '';  // '' = 全部
  let cachedRows = null;

  const chart = echarts.init(el, null, { renderer: 'canvas' });
  window.addEventListener('resize', () => chart.resize());

  function render(rows) {
    const filtered = currentCategory ? rows.filter((r) => r.category === currentCategory) : rows;
    if (!filtered.length) {
      chart.setOption({
        backgroundColor: 'transparent',
        title: {
          text: '此分類無資料',
          left: 'center', top: 'middle',
          textStyle: { color: '#71717a', fontSize: 13, fontStyle: 'italic', fontWeight: 'normal' },
        },
        xAxis: { show: false }, yAxis: { show: false }, series: [],
      }, true);
      return;
    }
    // 以分類分組成多個 series（ECharts legend 可逐類切換）
    const groups = {};
    CATEGORIES.forEach((c) => (groups[c] = []));
    filtered.forEach((r) => {
      const cat = groups[r.category] ? r.category : 'other';
      groups[cat].push({
        value: [r.date, r.heat, r.news_count, r.title],
        topic_id: r.topic_id,
        slug: r.slug,
      });
    });

    const series = CATEGORIES.map((c) => ({
      name: catLabels[c],
      type: 'scatter',
      data: groups[c] || [],
      symbolSize: (v) => Math.max(6, Math.min(40, Math.sqrt((v[2] || 1)) * 8)),
      itemStyle: { color: catColors[c], opacity: 0.75 },
      emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1 } },
    }));

    chart.setOption({
      backgroundColor: 'transparent',
      legend: {
        data: CATEGORIES.map((c) => catLabels[c]),
        textStyle: { color: '#a1a1aa', fontSize: 11 },
        top: 0,
      },
      tooltip: {
        trigger: 'item',
        formatter: (p) => {
          const [date, heat, count, title] = p.value;
          return `<b>${title}</b><br/>${date} · ${p.seriesName}<br/>熱度 ${heat.toFixed(2)} · ${count} 則`;
        },
      },
      grid: { left: 50, right: 20, top: 40, bottom: 50 },
      xAxis: {
        type: 'time',
        axisLabel: { color: '#a1a1aa', fontSize: 11 },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        name: '熱度',
        nameTextStyle: { color: '#71717a', fontSize: 11 },
        axisLabel: { color: '#a1a1aa', fontSize: 11 },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      },
      series,
    }, true);

    // 點擊散點 → 跳 topic_detail
    chart.off('click');
    chart.on('click', (p) => {
      if (p && p.data && p.data.slug) {
        window.location = '/topic/' + encodeURIComponent(p.data.slug);
      }
    });
  }

  function setCategory(cat) {
    currentCategory = cat;
    filtersEl.querySelectorAll('.wm-chip').forEach((c) => c.classList.toggle('active', c.dataset.cat === cat));
    if (cachedRows) render(cachedRows);
  }

  // 篩選 chips
  if (filtersEl) {
    filtersEl.innerHTML = [''].concat(CATEGORIES)
      .map((c) => `<button type="button" class="wm-chip ${c === '' ? 'active' : ''}" data-cat="${c}">${c === '' ? '全部' : catLabels[c]}</button>`)
      .join('');
    filtersEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.wm-chip');
      if (btn) setCategory(btn.dataset.cat);
    });
  }

  fetch('/api/timeline?days=30', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((rows) => {
      if (!rows || rows.length === 0) {
        el.innerHTML = '<div class="wm-skeleton">尚未偵測到事件，請先執行 python daily_pipeline.py</div>';
        return;
      }
      cachedRows = rows;
      render(rows);
    })
    .catch((err) => {
      el.innerHTML = `<div class="wm-skeleton">時間軸載入失敗（${err}）</div>`;
    });
})();
