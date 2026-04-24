// 6 分類信號雷達 — ECharts radar，今日 vs 7 日均疊圖。
(function () {
  const el = document.getElementById('wm-radar-chart');
  if (!el || typeof echarts === 'undefined') return;

  const catLabels = {
    ai_model: 'AI 模型',
    business: '商業',
    policy: '政策',
    product: '產品',
    semiconductor: '半導體',
    other: '其他',
  };

  const chart = echarts.init(el, null, { renderer: 'canvas' });
  window.addEventListener('resize', () => chart.resize());

  fetch('/api/radar', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((data) => {
      const cats = data.categories || [];
      const today = cats.map((c) => data.today[c] || 0);
      const avg7 = cats.map((c) => data.avg_7d[c] || 0);
      const max = Math.max(...today, ...avg7, 1) * 1.2;

      const option = {
        backgroundColor: 'transparent',
        legend: {
          data: ['今日', '7 日平均'],
          textStyle: { color: '#a1a1aa', fontSize: 11 },
          top: 0,
        },
        tooltip: { trigger: 'item' },
        radar: {
          indicator: cats.map((c) => ({ name: catLabels[c] || c, max })),
          shape: 'polygon',
          splitNumber: 4,
          axisName: { color: '#d4d4d8', fontSize: 11 },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
          splitArea: {
            areaStyle: {
              color: ['rgba(139,92,246,0.02)', 'rgba(139,92,246,0.05)'],
            },
          },
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        },
        series: [{
          type: 'radar',
          data: [
            {
              value: today,
              name: '今日',
              areaStyle: { color: 'rgba(139,92,246,0.35)' },
              lineStyle: { color: '#8b5cf6', width: 2 },
              itemStyle: { color: '#8b5cf6' },
            },
            {
              value: avg7,
              name: '7 日平均',
              areaStyle: { color: 'rgba(34,197,94,0.12)' },
              lineStyle: { color: '#22c55e', width: 2, type: 'dashed' },
              itemStyle: { color: '#22c55e' },
            },
          ],
        }],
      };

      chart.setOption(option);
    })
    .catch((err) => {
      el.innerHTML = `<div class="wm-skeleton">雷達資料載入失敗（${err}）</div>`;
    });
})();
