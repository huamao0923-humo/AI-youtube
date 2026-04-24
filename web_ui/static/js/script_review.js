/**
 * 腳本審閱頁互動：
 *  - 章節 chunk tab 切換
 *  - 單段就地編輯（contenteditable + debounced 儲存）
 *  - 章節「標記已審」+ 進度條 + 解鎖確認按鈕
 *  - AI 一鍵審閱（整份 or 單章）
 *  - AI diff modal：勾選接受、套用
 */
(() => {
  const tabs = document.querySelectorAll('.chunk-tab');
  const panels = document.querySelectorAll('.chunk-panel');
  if (!tabs.length) return;

  const totalChunks = tabs.length;
  const approvedKey = () => `script_approved_chunks_${location.pathname}`;
  const approved = new Set(JSON.parse(localStorage.getItem(approvedKey()) || '[]').map(Number));

  // ─── Chunk 切換 ───
  function showChunk(idx) {
    tabs.forEach(t => t.classList.toggle('active', Number(t.dataset.chunkIdx) === idx));
    panels.forEach(p => p.classList.toggle('active', Number(p.dataset.chunkPanel) === idx));
  }
  tabs.forEach(t => {
    t.addEventListener('click', () => showChunk(Number(t.dataset.chunkIdx)));
  });

  // ─── 進度 + 解鎖 ───
  function refreshProgress() {
    document.getElementById('review-progress-count').textContent = approved.size;
    const pct = (approved.size / totalChunks * 100) || 0;
    document.getElementById('review-progress-fill').style.width = pct + '%';

    tabs.forEach(t => {
      const idx = Number(t.dataset.chunkIdx);
      const st = t.querySelector(`[data-chunk-status="${idx}"]`);
      if (approved.has(idx)) {
        st.textContent = '✓ 已審';
        st.classList.add('done');
      } else {
        st.textContent = '未審';
        st.classList.remove('done');
      }
    });

    const btn = document.getElementById('btn-approve-script');
    const hint = document.getElementById('approve-hint');
    if (approved.size >= totalChunks) {
      btn.disabled = false;
      hint.textContent = '✅ 所有章節已審，可以確認';
      hint.style.color = 'var(--green)';
    } else {
      btn.disabled = true;
      hint.textContent = `還有 ${totalChunks - approved.size} 個章節未標記已審`;
      hint.style.color = '';
    }
  }

  function markApproved(idx) {
    approved.add(idx);
    localStorage.setItem(approvedKey(), JSON.stringify([...approved]));
    refreshProgress();
  }

  document.querySelectorAll('.btn-chunk-approve').forEach(b => {
    b.addEventListener('click', () => {
      const idx = Number(b.dataset.chunkIdx);
      markApproved(idx);
    });
  });
  document.querySelectorAll('.btn-chunk-next').forEach(b => {
    b.addEventListener('click', () => {
      const idx = Number(b.dataset.chunkIdx);
      markApproved(idx);
      if (idx + 1 < totalChunks) showChunk(idx + 1);
    });
  });

  // ─── 單段就地編輯 ───
  const saveTimers = new Map();
  document.querySelectorAll('.editable-narration').forEach(el => {
    el.addEventListener('input', () => {
      const sid = el.dataset.sectionId;
      const statusEl = document.querySelector(`[data-save-status="${sid}"]`);
      statusEl.textContent = '✏️ 編輯中…';
      statusEl.className = 'section-save-status editing';

      if (saveTimers.has(sid)) clearTimeout(saveTimers.get(sid));
      saveTimers.set(sid, setTimeout(() => saveSection(sid, el), 1200));
    });
    el.addEventListener('blur', () => {
      const sid = el.dataset.sectionId;
      if (saveTimers.has(sid)) {
        clearTimeout(saveTimers.get(sid));
        saveSection(sid, el);
      }
    });
  });

  async function saveSection(sid, el) {
    const narration = (el.innerText || '').trim();
    const original = el.dataset.original || '';
    if (narration === original) return;
    const statusEl = document.querySelector(`[data-save-status="${sid}"]`);
    statusEl.textContent = '💾 儲存中…';
    try {
      const r = await fetch(`/api/script/section/${sid}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({narration}),
      });
      if (!r.ok) throw new Error(String(r.status));
      el.dataset.original = narration;
      statusEl.textContent = '✓ 已儲存';
      statusEl.className = 'section-save-status saved';
      setTimeout(() => statusEl.textContent = '', 2000);
    } catch (e) {
      statusEl.textContent = '✗ 儲存失敗';
      statusEl.className = 'section-save-status error';
    }
  }

  // ─── AI 審閱 ───
  const aiStatus = document.getElementById('ai-review-status');
  const modal = document.getElementById('ai-diff-modal');

  async function runAiReview(sectionIds = null) {
    const btn1 = document.getElementById('btn-ai-review-all');
    const btn2 = document.getElementById('btn-ai-review-chunk');
    btn1.disabled = btn2.disabled = true;
    aiStatus.textContent = '🤖 Claude 審閱中，可能需要 1-3 分鐘…';
    aiStatus.style.color = 'var(--accent2)';
    try {
      const r = await fetch('/api/script/ai-review', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(sectionIds ? {section_ids: sectionIds} : {}),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || String(r.status));
      aiStatus.textContent = `✓ ${data.summary}`;
      aiStatus.style.color = 'var(--green)';
      showDiffModal(data);
    } catch (e) {
      aiStatus.textContent = '✗ 審閱失敗：' + e.message;
      aiStatus.style.color = 'var(--red)';
    } finally {
      btn1.disabled = btn2.disabled = false;
    }
  }

  document.getElementById('btn-ai-review-all').addEventListener('click', () => runAiReview(null));
  document.getElementById('btn-ai-review-chunk').addEventListener('click', () => {
    const activeTab = document.querySelector('.chunk-tab.active');
    const ids = JSON.parse(activeTab.dataset.sectionIds || '[]');
    runAiReview(ids);
  });

  // ─── Diff modal ───
  let currentChanges = [];

  function showDiffModal(data) {
    currentChanges = (data.changes || []).map((c, i) => ({...c, _idx: i, _accept: true}));
    document.getElementById('ai-diff-summary').textContent = data.summary || '';
    const list = document.getElementById('ai-diff-list');

    if (!currentChanges.length) {
      list.innerHTML = '<div class="ai-diff-empty">AI 認為腳本品質良好，無需修改 ✅</div>';
    } else {
      list.innerHTML = currentChanges.map((c, i) => `
        <div class="ai-diff-item">
          <div class="ai-diff-item-head">
            <label>
              <input type="checkbox" data-change-idx="${i}" checked>
              <span class="ai-diff-badge type-${c.type}">${c.type}</span>
              <span class="ai-diff-section">段落 ${c.section_id}</span>
            </label>
            <button class="btn btn-ghost btn-jump" data-jump-section="${c.section_id}">跳至段落</button>
          </div>
          <div class="ai-diff-reason">💡 ${escapeHtml(c.reason || '')}</div>
          <div class="ai-diff-before">
            <div class="ai-diff-label">原文</div>
            <div class="ai-diff-text">${escapeHtml(c.before || '')}</div>
          </div>
          <div class="ai-diff-after">
            <div class="ai-diff-label">AI 建議</div>
            <div class="ai-diff-text">${escapeHtml(c.after || '')}</div>
          </div>
        </div>
      `).join('');
    }
    modal.style.display = 'flex';
    updateAcceptCount();
  }

  function updateAcceptCount() {
    const checked = document.querySelectorAll('#ai-diff-list input[type=checkbox]:checked').length;
    document.getElementById('accept-count').textContent = checked;
  }

  document.getElementById('btn-close-diff').addEventListener('click', () => {
    modal.style.display = 'none';
  });
  document.getElementById('btn-accept-none').addEventListener('click', () => {
    document.querySelectorAll('#ai-diff-list input[type=checkbox]').forEach(cb => cb.checked = false);
    updateAcceptCount();
  });

  document.getElementById('ai-diff-list').addEventListener('change', updateAcceptCount);
  document.getElementById('ai-diff-list').addEventListener('click', e => {
    const jump = e.target.closest('[data-jump-section]');
    if (jump) {
      const sid = jump.dataset.jumpSection;
      const sec = document.querySelector(`.script-section[data-section-id="${sid}"]`);
      if (sec) {
        // 找到該段所在 chunk
        const panel = sec.closest('.chunk-panel');
        if (panel) showChunk(Number(panel.dataset.chunkPanel));
        modal.style.display = 'none';
        setTimeout(() => sec.scrollIntoView({behavior: 'smooth', block: 'center'}), 100);
      }
    }
  });

  document.getElementById('btn-accept-selected').addEventListener('click', async () => {
    const selected = [];
    document.querySelectorAll('#ai-diff-list input[type=checkbox]:checked').forEach(cb => {
      const i = Number(cb.dataset.changeIdx);
      selected.push(currentChanges[i]);
    });
    if (!selected.length) {
      alert('未勾選任何變更');
      return;
    }
    if (!confirm(`確定套用 ${selected.length} 處變更？\n（會覆蓋原文，但會保留修改歷史）`)) return;

    const btn = document.getElementById('btn-accept-selected');
    btn.disabled = true;
    btn.textContent = '套用中…';
    try {
      const r = await fetch('/api/script/apply-changes', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({accepted: selected}),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || String(r.status));
      alert(`✓ 已套用 ${data.applied} 處變更。頁面將重新載入。`);
      location.reload();
    } catch (e) {
      alert('套用失敗：' + e.message);
      btn.disabled = false;
      btn.textContent = '套用勾選的變更';
    }
  });

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // ─── 初始化 ───
  refreshProgress();
})();
