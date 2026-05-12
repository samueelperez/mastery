"""Prometheus scrape endpoint.

Returns the registry in the standard `text/plain; version=0.0.4` format
that Prometheus/Grafana Agent/VictoriaMetrics scrape. No auth — same
posture as `/health`: metrics are non-sensitive aggregates, and prod
deployments should restrict via ingress (Caddy/nginx ACL or VPN).
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/metrics", tags=["observability"])
async def metrics() -> Response:
    """Expose the default Prometheus registry. `generate_latest` is sync
    (CPU-bound text serialization), fast enough that running inside the
    async event loop is fine for hundreds of metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
