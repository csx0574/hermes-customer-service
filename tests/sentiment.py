"""
Hermes 智能客服 - 情绪曲线 (W6 任务)

ponytail: 评估层用关键词命中 (-1~1), 不引 LLM.
  - 强烈正面: +1.0 (谢谢, 棒, 太好了)
  - 正面: +0.5 (不错, 还行)
  - 中性: 0.0
  - 负面: -0.5 (不满, 慢)
  - 强烈负面: -1.0 (骗子, 垃圾, 投诉, 气死)
聚合: 5 秒窗口均值, 不每条写, 避免 DB 爆炸.
ceiling: 关键词覆盖 70%, LLM 流式 V1.1.
add when: 客户要"情绪曲线热力图" → LLM 流式 + Recharts 前端.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator, List, Optional

log = logging.getLogger("hermes.cs.sentiment")


class Sentiment(str, Enum):
    POSITIVE = "positive"  # > 0.3
    NEUTRAL = "neutral"    # -0.3 ~ 0.3
    NEGATIVE = "negative"  # -0.7 ~ -0.3
    ANGRY = "angry"       # < -0.7


_POS_STRONG = re.compile(r"太棒了|太好了|感谢|感恩|赞|辛苦了|完美|谢谢")
_POS_LIGHT = re.compile(r"不错|还好|还行|可以")
_NEG_LIGHT = re.compile(r"不满|不行|差劲|慢|卡|等太久|失望")
_NEG_STRONG = re.compile(r"骗子|垃圾|投诉|气死|滚|欺骗|骗人|骗我")


def evaluate(text: str) -> float:
    """关键词评估, 返回 -1.0 ~ 1.0."""
    if not text or not text.strip():
        return 0.0
    s = 0.0
    s += 1.0 * len(_POS_STRONG.findall(text))
    s += 0.5 * len(_POS_LIGHT.findall(text))
    s -= 0.5 * len(_NEG_LIGHT.findall(text))
    s -= 1.0 * len(_NEG_STRONG.findall(text))
    # ponytail: 上限 +/- 1.0, 不让单条文本刷爆曲线
    return max(-1.0, min(1.0, s))


def label(score: float) -> Sentiment:
    if score > 0.3:
        return Sentiment.POSITIVE
    if score < -0.7:
        return Sentiment.ANGRY
    if score < -0.3:
        return Sentiment.NEGATIVE
    return Sentiment.NEUTRAL


@dataclass(frozen=True)
class SentimentPoint:
    bucket_ts: float      # 桶起始时间戳 (5 秒对齐)
    channel: str
    user_id: str
    avg_score: float      # 桶内均值
    count: int            # 桶内消息数
    label: Sentiment      # 主导情绪


# ponytail: 5 秒聚合桶, 避免每条消息写库
_BUCKET_SECONDS = 5


def _bucket_key(ts: float) -> float:
    return (int(ts) // _BUCKET_SECONDS) * _BUCKET_SECONDS


class SentimentStore:
    """情绪聚合 + 写库 + 曲线查询."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (Path.home() / ".hermes" / "customer_service" / "sentiment.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS sentiment_buckets (
                    bucket_ts REAL NOT NULL,
                    channel TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    avg_score REAL NOT NULL,
                    count INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    PRIMARY KEY (bucket_ts, channel, user_id)
                )
                """
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_curve "
                "ON sentiment_buckets (user_id, channel, bucket_ts)"
            )

    def record(self, channel: str, user_id: str, text: str, ts: Optional[float] = None) -> SentimentPoint:
        """单条消息入桶 — 同一 5 秒桶合并. 返回写入的点."""
        t = ts or time.time()
        score = evaluate(text)
        bk = _bucket_key(t)
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT avg_score, count FROM sentiment_buckets "
                "WHERE bucket_ts=? AND channel=? AND user_id=?",
                (bk, channel, user_id),
            ).fetchone()
            if row is None:
                # 新桶
                new_avg = score
                new_count = 1
            else:
                # 合并 — 加权平均
                old_count = row["count"]
                old_avg = row["avg_score"]
                new_count = old_count + 1
                new_avg = (old_avg * old_count + score) / new_count
            lab = label(new_avg).value
            c.execute(
                "INSERT OR REPLACE INTO sentiment_buckets "
                "VALUES (?,?,?,?,?,?)",
                (bk, channel, user_id, new_avg, new_count, lab),
            )
        return SentimentPoint(bk, channel, user_id, new_avg, new_count, label(new_avg))

    def curve(self, user_id: str, channel: Optional[str] = None, since_ts: Optional[float] = None) -> List[SentimentPoint]:
        """查曲线 — 按时间正序."""
        sql = "SELECT * FROM sentiment_buckets WHERE user_id=?"
        params: list = [user_id]
        if channel:
            sql += " AND channel=?"
            params.append(channel)
        if since_ts is not None:
            sql += " AND bucket_ts >= ?"
            params.append(since_ts)
        sql += " ORDER BY bucket_ts ASC"
        with self._lock, self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [
            SentimentPoint(
                bucket_ts=r["bucket_ts"],
                channel=r["channel"],
                user_id=r["user_id"],
                avg_score=r["avg_score"],
                count=r["count"],
                label=Sentiment(r["label"]),
            )
            for r in rows
        ]


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        s = SentimentStore(Path(d) / "s.db")
        # 单条评估
        assert evaluate("你们是骗子!") == -1.0
        assert evaluate("太棒了!") == 1.0
        assert evaluate("不错") == 0.5
        assert evaluate("") == 0.0
        assert label(0.8) == Sentiment.POSITIVE
        assert label(-0.9) == Sentiment.ANGRY
        # 同一 5 秒桶合并
        t0 = 1000.0
        p1 = s.record("wecom", "u1", "骗子垃圾", ts=t0)
        p2 = s.record("wecom", "u1", "算了", ts=t0 + 1)  # 同桶
        assert p1.count == 1
        assert p2.count == 2
        assert -1.0 <= p2.avg_score <= 0.0
        # 不同桶
        p3 = s.record("wecom", "u1", "谢谢", ts=t0 + 10)  # 跨桶
        assert p3.count == 1
        assert p3.avg_score == 1.0
        # 曲线查询
        curve = s.curve("u1")
        assert len(curve) == 2  # 2 个桶
        assert curve[0].avg_score < curve[1].avg_score  # 负 → 正
        print(f"OK: sentiment {p1.label.value}→{p3.label.value} 2 buckets 跑通")
