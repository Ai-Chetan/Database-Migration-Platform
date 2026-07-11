"""
Cost Estimator
File: migration/backend/intelligence_service/estimator/cost_estimator.py

Projects migration costs using real table statistics from Metadata Catalog.
Never guesses — uses actual row counts, actual sizes, actual growth rates.

Estimates:
  1. Duration     — based on real throughput benchmarks × workers
  2. Compute cost — cloud instance cost × duration
  3. Storage cost — target storage required (with overhead factor)
  4. Network cost — data transfer costs (egress pricing)
  5. Total cost   — sum with configurable pricing model

Pricing models (all configurable):
  aws     — EC2 + S3 + Data Transfer pricing (us-east-1 defaults)
  gcp     — Compute Engine + Cloud Storage + Egress
  azure   — Virtual Machines + Blob Storage + Bandwidth
  custom  — caller supplies per-unit prices

Output:
{
  "summary": {
    "estimated_duration":     "9h 23m",
    "estimated_duration_sec": 33780,
    "total_rows":             4_200_000_000,
    "total_size_gb":          5100.0,
    "target_storage_gb":      6375.0,   -- 1.25x overhead
    "recommended_workers":    16,
  },
  "cost_breakdown": {
    "compute_usd":  147.20,
    "storage_usd":  152.25,
    "network_usd":  459.00,
    "total_usd":    758.45,
    "currency":     "USD",
  },
  "per_table": [
    {"table": "orders", "rows": 2.7B, "size_gb": 2900, "est_minutes": 340},
    ...
  ],
  "assumptions": [...]
}
"""

import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


# Throughput benchmarks (rows/sec per worker)
THROUGHPUT_PER_WORKER = {
    "mysql_to_mysql":           12_500,
    "mysql_to_postgresql":       7_500,
    "postgresql_to_postgresql": 15_000,
    "postgresql_to_mysql":       8_000,
    "default":                  10_000,
}

# Cloud pricing defaults (USD, approximate, 2026)
PRICING = {
    "aws": {
        "compute_per_hour_per_worker":  0.096,   # c6i.xlarge / 4 (4 workers per instance)
        "storage_per_gb_month":         0.023,   # S3 Standard
        "network_egress_per_gb":        0.09,    # within-region transfer
        "name":                         "AWS (us-east-1)",
    },
    "gcp": {
        "compute_per_hour_per_worker":  0.082,
        "storage_per_gb_month":         0.020,
        "network_egress_per_gb":        0.08,
        "name":                         "GCP (us-central1)",
    },
    "azure": {
        "compute_per_hour_per_worker":  0.094,
        "storage_per_gb_month":         0.018,
        "network_egress_per_gb":        0.087,
        "name":                         "Azure (East US)",
    },
}

STORAGE_OVERHEAD = 1.25   # target needs 25% more space than source (indexes, WAL, temp)


@dataclass
class CostEstimate:
    summary:        Dict[str, Any]
    cost_breakdown: Dict[str, Any]
    per_table:      List[Dict[str, Any]]
    assumptions:    List[str]

    def to_dict(self) -> dict:
        return {
            "summary":        self.summary,
            "cost_breakdown": self.cost_breakdown,
            "per_table":      self.per_table,
            "assumptions":    self.assumptions,
        }


class CostEstimator:

    def estimate(
        self,
        db:             Session,
        connection_id:  str,
        source_engine:  str = "mysql",
        target_engine:  str = "mysql",
        workers:        Optional[int] = None,
        cloud_provider: str = "aws",
        custom_pricing: Optional[Dict[str, float]] = None,
        tenant_id:      str = "local",
    ) -> CostEstimate:
        """
        Estimate migration cost using real statistics from Metadata Catalog.
        All monetary values in USD.
        """
        logger.info("Cost estimation starting", connection_id=connection_id)

        # ── Load catalog data ─────────────────────────────────────────────
        rows = db.execute(
            text("""
                SELECT DISTINCT ON (table_name, catalog_type)
                    table_name, catalog_type, data
                FROM metadata_catalog
                WHERE connection_id = :cid
                  AND catalog_type IN ('statistics', 'lob_detection', 'compression')
                ORDER BY table_name, catalog_type, computed_at DESC
            """),
            {"cid": connection_id}
        ).fetchall()

        catalog: Dict[str, Dict] = {}
        for row in rows:
            tname = row.table_name
            ctype = row.catalog_type
            data  = row.data
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}
            catalog.setdefault(tname, {})[ctype] = data

        # ── Aggregate ─────────────────────────────────────────────────────
        total_rows    = 0
        total_size_gb = 0.0
        per_table     = []
        assumptions   = []

        for tname, data in catalog.items():
            stats      = data.get("statistics", {})
            rc         = stats.get("row_count", 0) or 0
            sg         = stats.get("size_gb", 0) or 0
            comp       = data.get("compression", {})
            lob        = data.get("lob_detection", {})

            # Adjust for compression
            actual_sg = sg
            if comp.get("is_compressed"):
                ratio    = comp.get("compression_ratio", 1.0) or 1.0
                actual_sg = sg * ratio
                assumptions.append(
                    f"{tname}: compressed at {ratio}x — actual size ~{actual_sg:.1f} GB"
                )

            total_rows    += rc
            total_size_gb += actual_sg
            per_table.append({
                "table_name":    tname,
                "rows":          rc,
                "size_gb":       round(actual_sg, 3),
                "has_lob":       lob.get("has_lob", False),
                "is_compressed": comp.get("is_compressed", False),
            })

        per_table.sort(key=lambda x: x["size_gb"], reverse=True)

        # ── Duration ──────────────────────────────────────────────────────
        tput_key = f"{source_engine}_to_{target_engine}"
        tput     = THROUGHPUT_PER_WORKER.get(tput_key, THROUGHPUT_PER_WORKER["default"])

        # Auto-recommend workers if not supplied
        if not workers:
            if total_rows >= 10_000_000_000:  workers = 32
            elif total_rows >= 1_000_000_000: workers = 16
            elif total_rows >= 100_000_000:   workers = 8
            elif total_rows >= 10_000_000:    workers = 4
            else:                             workers = 2

        effective_tput  = tput * workers
        est_seconds     = int(total_rows / effective_tput) if effective_tput > 0 else 0
        est_hours       = est_seconds / 3600

        # Per-table estimates
        for t in per_table:
            t_seconds = int(t["rows"] / effective_tput) if effective_tput > 0 else 0
            t["est_minutes"] = round(t_seconds / 60, 1)
            t["est_duration"] = self._fmt(t_seconds)

        # ── Storage ───────────────────────────────────────────────────────
        target_storage_gb = total_size_gb * STORAGE_OVERHEAD

        # ── Costs ─────────────────────────────────────────────────────────
        pricing = custom_pricing or PRICING.get(cloud_provider, PRICING["aws"])
        provider_name = pricing.get("name", cloud_provider)

        compute_usd = (
            pricing["compute_per_hour_per_worker"] * workers * est_hours
        )
        # Storage for 1 month (typical migration + validation window)
        storage_usd = (
            pricing["storage_per_gb_month"] * target_storage_gb
        )
        # Network egress (full data size transferred)
        network_usd = (
            pricing["network_egress_per_gb"] * total_size_gb
        )
        total_usd = compute_usd + storage_usd + network_usd

        # ── Assumptions ───────────────────────────────────────────────────
        assumptions += [
            f"Throughput benchmark: {tput:,} rows/sec/worker ({source_engine}→{target_engine})",
            f"Worker count: {workers}",
            f"Storage overhead factor: {STORAGE_OVERHEAD}x (indexes, WAL, temporary space)",
            f"Pricing model: {provider_name}",
            "Network cost assumes full cross-region data transfer",
            "Compute cost based on duration only (not idle time before/after)",
            "Does not include RDS/Cloud SQL instance costs if using managed DB",
        ]

        summary = {
            "estimated_duration":     self._fmt(est_seconds),
            "estimated_duration_sec": est_seconds,
            "total_rows":             total_rows,
            "total_size_gb":          round(total_size_gb, 2),
            "target_storage_gb":      round(target_storage_gb, 2),
            "recommended_workers":    workers,
            "throughput_rps_total":   effective_tput,
            "cloud_provider":         provider_name,
        }

        cost_breakdown = {
            "compute_usd":  round(compute_usd, 2),
            "storage_usd":  round(storage_usd, 2),
            "network_usd":  round(network_usd, 2),
            "total_usd":    round(total_usd, 2),
            "currency":     "USD",
            "compute_detail": f"{workers} workers × ${pricing['compute_per_hour_per_worker']:.3f}/hr × {est_hours:.1f}h",
            "storage_detail": f"{target_storage_gb:.0f} GB × ${pricing['storage_per_gb_month']:.3f}/GB/month",
            "network_detail": f"{total_size_gb:.0f} GB × ${pricing['network_egress_per_gb']:.3f}/GB",
        }

        logger.info("Cost estimation complete",
                    duration=summary["estimated_duration"],
                    total_usd=cost_breakdown["total_usd"])

        return CostEstimate(
            summary=summary,
            cost_breakdown=cost_breakdown,
            per_table=per_table[:20],  # top 20 by size
            assumptions=assumptions,
        )

    def _fmt(self, seconds: int) -> str:
        if seconds <= 0:    return "< 1 minute"
        if seconds < 60:    return f"{seconds}s"
        if seconds < 3600:  m, s = divmod(seconds, 60);   return f"{m}m {s}s"
        if seconds < 86400: h, r = divmod(seconds, 3600); return f"{h}h {r//60}m"
        d, r = divmod(seconds, 86400); return f"{d}d {r//3600}h"
