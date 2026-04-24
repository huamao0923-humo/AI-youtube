"""進度心跳 — 長時間阻塞操作的定期回報。

用途：claude CLI / Pexels 抓取等阻塞 10 分鐘以上的呼叫，
在呼叫期間每隔 N 秒更新一次 progress_detail，讓前端 Hero 不會卡在「準備中…」。

用法：
    with Heartbeat(slug="20260420_foo", base_msg="✍️ Claude 生成腳本中", expected_sec=900):
        result = subprocess.run(...)  # 執行中每 15 秒更新進度
"""
from __future__ import annotations

import threading
import time


class Heartbeat:
    def __init__(
        self,
        slug: str | None,
        base_msg: str,
        expected_sec: int,
        interval: int = 15,
    ) -> None:
        self.slug = slug
        self.base = base_msg
        self.expected = expected_sec
        self.interval = interval
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self._start: float = 0.0

    def __enter__(self) -> "Heartbeat":
        if not self.slug:
            return self
        self._start = time.time()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=2)

    def _loop(self) -> None:
        # 延遲導入避免循環依賴
        from modules.database import db_manager
        while not self._stop.wait(self.interval):
            elapsed = int(time.time() - self._start)
            try:
                db_manager.update_episode_progress(
                    self.slug, f"{self.base}｜已等候 {elapsed}s / {self.expected}s"
                )
            except Exception:
                pass
