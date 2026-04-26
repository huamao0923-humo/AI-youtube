/**
 * AI 戰情室 — 「今日已選」右側 sidepanel + 「今日節奏條」（pipeline bar）。
 *
 * - 載入後立即拉一次 /api/episodes/today 與 /api/system/scheduler-status
 * - 每 30 秒輪詢
 * - 暴露 window.AwrSelected.refresh() 給 selectTopic/selectNews 成功後呼叫
 */
(function () {
  const POLL_MS = 30 * 1000;
  const esc = window.AiWarRoom ? window.AiWarRoom.escapeHtml : (s) => String(s || '');
  const fmtTime = window.AiWarRoom ? window.AiWarRoom.fmtTime : (s) => s;

  function stageLabel(stage) {
    const map = {
      selected: '已選題', researching: '研究中', scripting: '寫腳本',
      tts: '配音', prefetch: '抓素材', images: '產圖',
      compositing: '合成中', upload_ready: '待上傳',
      uploading: '上傳中', done: '完成',
      idle: '待開始', cancelled: '已取消',
    };
    return map[stage] || stage || '—';
  }

  function renderSelected(data) {
    const list = document.getElementById('awr-selected-list');
    const cnt  = document.getElementById('awr-selected-count');
    if (!list || !cnt) return;

    const eps = (data && data.episodes) || [];
    cnt.textContent = String(eps.length);

    if (!eps.length) {
      list.innerHTML = '<li class="awr-side-empty">尚未選題 — 從下方熱度榜或焦點新聞點「🎬 選為本集題目」</li>';
      return;
    }

    list.innerHTML = eps.map(e => {
      const pct = Math.max(0, Math.min(100, e.stage_pct || 0));
      const errBadge = e.error_msg ? `<span class="awr-side-err" title="${esc(e.error_msg)}">⚠</span>` : '';
      const ytLink = e.youtube_id ? ` <a href="https://youtu.be/${esc(e.youtube_id)}" target="_blank" rel="noopener" title="YouTube">▶</a>` : '';
      return `
        <li class="awr-side-item">
          <a class="awr-side-title-link" href="/episode/${encodeURIComponent(e.slug)}">${esc(e.title)}</a>
          <div class="awr-side-meta">
            <span class="awr-side-stage">${esc(stageLabel(e.stage))}</span>
            ${errBadge}${ytLink}
            <span class="awr-side-time">${esc(fmtTime(e.updated_at) || '')}</span>
          </div>
          <div class="awr-stage-bar"><div class="awr-stage-bar-fill" style="width:${pct}%"></div></div>
        </li>
      `;
    }).join('');
  }

  function renderPipelineBar(data) {
    if (!data || !data.last_runs) return;
    const runs = data.last_runs || {};
    const pendingEl = document.getElementById('awr-pipeline-pending-count');

    document.querySelectorAll('.awr-pipeline-step[data-step]').forEach(stepEl => {
      const jobId = stepEl.dataset.step;
      if (jobId === 'select') return; // 留給 sidepanel 算
      const statusEl = stepEl.querySelector('[data-role="status"]');
      const info = runs[jobId];
      if (!info) {
        stepEl.classList.remove('done', 'fail');
        if (statusEl) statusEl.textContent = '—';
        return;
      }
      const ok = info.success !== false;
      stepEl.classList.toggle('done', ok);
      stepEl.classList.toggle('fail', !ok);
      if (statusEl) {
        const t = info.last_run ? fmtTime(info.last_run) : '';
        statusEl.innerHTML = (ok ? '✓ ' : '✗ ') + esc(t || '—');
      }
    });

    const warnEl = document.getElementById('awr-pipeline-warn');
    if (warnEl) {
      if (data.warning) {
        warnEl.style.display = 'inline-block';
        warnEl.textContent = '⚠ ' + data.warning;
      } else if (data.cloud_enabled === false) {
        warnEl.style.display = 'inline-block';
        warnEl.textContent = '⚠ 此 process 未啟動排程器（SCHEDULER_ENABLED=0）';
      } else {
        warnEl.style.display = 'none';
      }
    }

    if (pendingEl) {
      // pending count 從 selected sidepanel 推得
      const cnt = (document.getElementById('awr-selected-count') || {}).textContent || '0';
      pendingEl.textContent = cnt + ' 集';
    }
  }

  async function refresh() {
    try {
      const [epResp, schedResp] = await Promise.all([
        fetch('/api/episodes/today').then(r => r.ok ? r.json() : Promise.reject(r.status)).catch(() => null),
        fetch('/api/system/scheduler-status').then(r => r.ok ? r.json() : null).catch(() => null),
      ]);
      if (epResp) renderSelected(epResp);
      if (schedResp) renderPipelineBar(schedResp);
    } catch (e) {
      // ignore — 不影響其他 panel
    }
  }

  function bindToggle() {
    const btn = document.getElementById('awr-side-toggle');
    const aside = document.getElementById('awr-selected-today');
    if (!btn || !aside) return;
    btn.addEventListener('click', () => {
      const collapsed = aside.classList.toggle('collapsed');
      btn.textContent = collapsed ? '+' : '−';
    });
  }

  window.AwrSelected = { refresh };

  document.addEventListener('DOMContentLoaded', () => {
    bindToggle();
    refresh();
    setInterval(refresh, POLL_MS);
  });
})();
