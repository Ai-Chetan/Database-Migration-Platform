"""
Knowledge Base
File: migration/backend/knowledge_base/store/knowledge_base.py

Every completed migration stores structured knowledge for future reference.
The AI Copilot (Part 14) queries this to answer "what worked before
for Oracle→PostgreSQL?" without hallucinating.

Entry types:
    migration_outcome      → what happened: success/failure, duration, rows, errors
    type_mapping_pattern   → which type conversions worked/failed for a source→target pair
    performance_pattern    → what worker count / chunk strategy performed best
    error_pattern          → what errors occurred and how they were fixed
    schema_pattern         → schema characteristics that affected the migration
    cdc_pattern            → CDC-specific outcomes (lag, cutover time, drift events)

Recording happens automatically:
    - After every completed migration (record_migration_outcome)
    - After schema drift detection (record_drift_pattern)
    - After CDC cutover (record_cdc_pattern)

Querying:
    - find_similar()     → "find migrations like this one"
    - get_patterns()     → "what type mappings worked for mysql→postgresql?"
    - get_error_fixes()  → "what fixed this error before?"
    - get_performance()  → "what worker count worked best for this data volume?"
"""

import datetime
import uuid
import json
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


class KnowledgeBase:

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_migration_outcome(
        self,
        db:        Session,
        job_id:    str,
        tenant_id: str = "local",
    ) -> Optional[Dict[str, Any]]:
        """
        Called after a migration completes.
        Extracts key facts and stores as a knowledge_base entry.
        """
        try:
            # Load job data
            job = db.execute(
                text("SELECT * FROM migration_jobs WHERE id=:id"), {"id": job_id}
            ).fetchone()
            if not job:
                return None
            j = self._to_dict(job)

            # Load chunk stats
            stats = db.execute(
                text("""
                    SELECT
                        COUNT(*)                                    AS total_chunks,
                        COUNT(*) FILTER (WHERE status='completed') AS completed,
                        COUNT(*) FILTER (WHERE status='failed')    AS failed,
                        SUM(rows_processed)                        AS total_rows,
                        AVG(duration_ms)                           AS avg_chunk_ms
                    FROM migration_chunks WHERE job_id=:jid
                """),
                {"jid": job_id}
            ).fetchone()
            s = self._to_dict(stats)

            # Compute duration
            started   = j.get("started_at")
            completed = j.get("completed_at")
            duration_s = None
            if started and completed:
                try:
                    st = datetime.datetime.fromisoformat(started) if isinstance(started, str) else started
                    ct = datetime.datetime.fromisoformat(completed) if isinstance(completed, str) else completed
                    duration_s = int((ct - st).total_seconds())
                except Exception:
                    pass

            # Load error patterns if failed
            errors = []
            if int(s.get("failed") or 0) > 0:
                err_rows = db.execute(
                    text("""
                        SELECT DISTINCT last_error FROM migration_chunks
                        WHERE job_id=:jid AND last_error IS NOT NULL LIMIT 10
                    """),
                    {"jid": job_id}
                ).fetchall()
                errors = [r[0] for r in err_rows if r[0]]

            content = {
                "job_id":          job_id,
                "status":          j.get("status"),
                "source_engine":   j.get("source_engine"),
                "target_engine":   j.get("target_engine"),
                "worker_count":    j.get("worker_count", 4),
                "chunk_strategy":  j.get("chunk_strategy", "size_based"),
                "total_rows":      int(s.get("total_rows") or 0),
                "duration_seconds": duration_s,
                "success_rate":    round(int(s.get("completed") or 0) /
                                         max(int(s.get("total_chunks") or 1), 1), 3),
                "avg_chunk_ms":    round(float(s.get("avg_chunk_ms") or 0)),
                "failed_chunks":   int(s.get("failed") or 0),
                "error_samples":   errors[:5],
                "lessons_learned": self._extract_lessons(j, s, errors),
            }

            src = j.get("source_engine", "unknown")
            tgt = j.get("target_engine", "unknown")
            title = (f"{src}→{tgt} migration: "
                     f"{int(s.get('total_rows') or 0):,} rows, "
                     f"status={j.get('status')}")

            return self._save_entry(
                db=db,
                job_id=job_id,
                source_engine=src,
                target_engine=tgt,
                entry_type="migration_outcome",
                title=title,
                content=content,
                tags=[src, tgt, j.get("status", "unknown"), "outcome"],
                tenant_id=tenant_id,
            )

        except Exception as e:
            logger.warning("Failed to record migration outcome", job_id=job_id, error=str(e))
            return None

    def record_type_mapping_pattern(
        self,
        db:            Session,
        source_engine: str,
        target_engine: str,
        mappings:      List[Dict],
        job_id:        Optional[str] = None,
        tenant_id:     str = "local",
    ) -> Dict[str, Any]:
        """
        Record which type mappings were used and whether they were safe.
        Useful for the AI Advisor to suggest proven mappings for the same engine pair.
        """
        safe     = [m for m in mappings if m.get("conversion_safety") == "safe"]
        lossy    = [m for m in mappings if m.get("conversion_safety") == "lossy"]
        unsafe   = [m for m in mappings if m.get("conversion_safety") == "unsafe"]

        content = {
            "source_engine": source_engine,
            "target_engine": target_engine,
            "total_mappings": len(mappings),
            "safe_mappings":  safe[:20],
            "lossy_mappings": lossy[:10],
            "unsafe_mappings": unsafe[:10],
            "summary": {
                "safe": len(safe),
                "lossy": len(lossy),
                "unsafe": len(unsafe),
            },
        }
        title = (f"Type mappings for {source_engine}→{target_engine}: "
                 f"{len(safe)} safe, {len(lossy)} lossy, {len(unsafe)} unsafe")

        return self._save_entry(
            db=db, job_id=job_id,
            source_engine=source_engine, target_engine=target_engine,
            entry_type="type_mapping_pattern", title=title, content=content,
            tags=[source_engine, target_engine, "type_mapping"],
            tenant_id=tenant_id,
        )

    def record_error_pattern(
        self,
        db:            Session,
        error_message: str,
        resolution:    str,
        source_engine: str,
        target_engine: str,
        context:       Dict[str, Any] = None,
        job_id:        Optional[str] = None,
        tenant_id:     str = "local",
    ) -> Dict[str, Any]:
        """Record an error and how it was resolved for future reference."""
        content = {
            "error_message": error_message[:500],
            "resolution":    resolution,
            "source_engine": source_engine,
            "target_engine": target_engine,
            "context":       context or {},
        }
        title = f"Error pattern ({source_engine}→{target_engine}): {error_message[:100]}"

        return self._save_entry(
            db=db, job_id=job_id,
            source_engine=source_engine, target_engine=target_engine,
            entry_type="error_pattern", title=title, content=content,
            tags=[source_engine, target_engine, "error", "resolution"],
            tenant_id=tenant_id,
        )

    # ── Querying ──────────────────────────────────────────────────────────────

    def find_similar(
        self,
        db:            Session,
        source_engine: str,
        target_engine: str,
        entry_type:    Optional[str] = None,
        tenant_id:     str = "local",
        limit:         int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Find knowledge base entries for a similar migration scenario.
        Returns entries ordered by usefulness_score descending.
        """
        conditions = [
            "(tenant_id=:tid OR is_public=TRUE)",
            "(source_engine=:src OR source_engine IS NULL)",
            "(target_engine=:tgt OR target_engine IS NULL)",
        ]
        params: Dict[str, Any] = {
            "tid": tenant_id, "src": source_engine,
            "tgt": target_engine, "lim": limit,
        }
        if entry_type:
            conditions.append("entry_type=:etype")
            params["etype"] = entry_type

        rows = db.execute(
            text(f"""
                SELECT id, entry_type, title, content, tags,
                       usefulness_score, reference_count, created_at
                FROM knowledge_base
                WHERE {' AND '.join(conditions)}
                ORDER BY usefulness_score DESC, reference_count DESC
                LIMIT :lim
            """),
            params
        ).fetchall()

        result = [self._to_dict(r) for r in rows]

        # Increment reference count
        if result:
            ids = "','".join(str(r["id"]) for r in result)
            db.execute(
                text(f"UPDATE knowledge_base SET reference_count=reference_count+1 WHERE id IN ('{ids}')")
            )
            db.commit()

        return result

    def get_error_fixes(
        self,
        db:            Session,
        error_fragment: str,
        source_engine: str,
        target_engine: str,
        tenant_id:     str = "local",
    ) -> List[Dict[str, Any]]:
        """Find recorded resolutions for similar errors."""
        rows = db.execute(
            text("""
                SELECT id, title, content, usefulness_score, created_at
                FROM knowledge_base
                WHERE entry_type='error_pattern'
                AND (tenant_id=:tid OR is_public=TRUE)
                AND source_engine=:src AND target_engine=:tgt
                AND content->>'error_message' ILIKE :frag
                ORDER BY usefulness_score DESC
                LIMIT 5
            """),
            {
                "tid": tenant_id, "src": source_engine, "tgt": target_engine,
                "frag": f"%{error_fragment[:100]}%",
            }
        ).fetchall()
        return [self._to_dict(r) for r in rows]

    def get_performance_patterns(
        self,
        db:            Session,
        source_engine: str,
        target_engine: str,
        approx_rows:   Optional[int] = None,
        tenant_id:     str = "local",
    ) -> List[Dict[str, Any]]:
        """
        Return performance patterns for a source→target pair.
        Optionally filtered by data volume bucket.
        """
        rows = db.execute(
            text("""
                SELECT content, usefulness_score, created_at
                FROM knowledge_base
                WHERE entry_type='migration_outcome'
                AND (tenant_id=:tid OR is_public=TRUE)
                AND source_engine=:src AND target_engine=:tgt
                AND (content->>'status') = 'completed'
                ORDER BY usefulness_score DESC, created_at DESC
                LIMIT 10
            """),
            {"tid": tenant_id, "src": source_engine, "tgt": target_engine}
        ).fetchall()

        entries = [self._to_dict(r) for r in rows]

        # Filter by volume if specified
        if approx_rows:
            factor  = 5
            entries = [
                e for e in entries
                if e.get("content", {}).get("total_rows") and
                abs(int(e["content"]["total_rows"]) - approx_rows) < approx_rows * factor
            ]

        return entries

    def rate_entry(
        self,
        db:       Session,
        entry_id: str,
        rating:   float,   # 0.0 to 1.0
    ) -> Dict[str, Any]:
        """Update usefulness score based on operator feedback."""
        rating = max(0.0, min(1.0, rating))
        db.execute(
            text("""
                UPDATE knowledge_base SET
                    usefulness_score = (usefulness_score * reference_count + :rating) /
                                       (reference_count + 1),
                    reference_count  = reference_count + 1,
                    updated_at       = :now
                WHERE id = :id
            """),
            {"rating": rating, "now": datetime.datetime.utcnow(), "id": entry_id}
        )
        db.commit()
        return {"entry_id": entry_id, "new_rating": rating, "updated": True}

    def list_entries(
        self,
        db:         Session,
        tenant_id:  str = "local",
        entry_type: Optional[str] = None,
        limit:      int = 50,
    ) -> List[Dict[str, Any]]:
        conditions = ["(tenant_id=:tid OR is_public=TRUE)"]
        params: Dict[str, Any] = {"tid": tenant_id, "lim": limit}
        if entry_type:
            conditions.append("entry_type=:etype")
            params["etype"] = entry_type

        rows = db.execute(
            text(f"""
                SELECT id, entry_type, source_engine, target_engine,
                       title, tags, usefulness_score, reference_count, created_at
                FROM knowledge_base
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC LIMIT :lim
            """),
            params
        ).fetchall()
        return [self._to_dict(r) for r in rows]

    def get_entry(self, db: Session, entry_id: str) -> Optional[Dict]:
        row = db.execute(
            text("SELECT * FROM knowledge_base WHERE id=:id"), {"id": entry_id}
        ).fetchone()
        return self._to_dict(row) if row else None

    def get_summary(self, db: Session, tenant_id: str = "local") -> Dict[str, Any]:
        """Knowledge base statistics."""
        row = db.execute(
            text("""
                SELECT
                    COUNT(*)                                        AS total_entries,
                    COUNT(DISTINCT source_engine||'->'||target_engine) AS engine_pairs,
                    COUNT(*) FILTER (WHERE entry_type='migration_outcome')  AS outcomes,
                    COUNT(*) FILTER (WHERE entry_type='error_pattern')      AS error_fixes,
                    COUNT(*) FILTER (WHERE entry_type='type_mapping_pattern') AS type_mappings,
                    AVG(usefulness_score)                           AS avg_usefulness
                FROM knowledge_base
                WHERE tenant_id=:tid OR is_public=TRUE
            """),
            {"tid": tenant_id}
        ).fetchone()
        return self._to_dict(row)

    # ── Private ───────────────────────────────────────────────────────────────

    def _save_entry(
        self, db, job_id, source_engine, target_engine,
        entry_type, title, content, tags, tenant_id
    ) -> Dict[str, Any]:
        eid = str(uuid.uuid4())
        now = datetime.datetime.utcnow()
        db.execute(
            text("""
                INSERT INTO knowledge_base
                    (id, tenant_id, job_id, source_engine, target_engine,
                     entry_type, title, content, tags, created_at, updated_at)
                VALUES
                    (:id, :tid, :jid, :src, :tgt,
                     :etype, :title, :content::jsonb, :tags, :now, :now)
            """),
            {
                "id":      eid, "tid": tenant_id, "jid": job_id,
                "src":     source_engine, "tgt": target_engine,
                "etype":   entry_type, "title": title[:500],
                "content": json.dumps(content, default=str),
                "tags":    tags, "now": now,
            }
        )
        db.commit()
        logger.info("Knowledge base entry saved",
                    entry_type=entry_type, entry_id=eid)
        return {"entry_id": eid, "entry_type": entry_type, "title": title[:100]}

    def _extract_lessons(self, job: Dict, stats: Dict, errors: List[str]) -> List[str]:
        """Extract human-readable lessons from migration data."""
        lessons = []
        total    = int(stats.get("total_chunks") or 0)
        failed   = int(stats.get("failed") or 0)
        workers  = job.get("worker_count", 4)
        strategy = job.get("chunk_strategy", "size_based")

        if job.get("status") == "completed" and failed == 0:
            lessons.append(
                f"{workers} workers with {strategy} chunks worked well for this scenario."
            )
        if failed > 0:
            fail_pct = round(failed / max(total, 1) * 100, 1)
            lessons.append(f"{fail_pct}% chunk failure rate — review error patterns.")
        for err in errors[:2]:
            if err:
                lessons.append(f"Error encountered: {str(err)[:100]}")
        return lessons

    def _to_dict(self, row) -> dict:
        if not row:
            return {}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d
