"""Read-only GET router; no Store initialization, import, or database migration."""
from pathlib import Path
from fastapi import APIRouter
from hr_zones import query_analysis


def create_router(data_dir: Path) -> APIRouter:
    router = APIRouter()

    @router.get('/api/activities/{act_id}/hr-zones')
    def heart_rate_zones(act_id: int):
        return query_analysis(data_dir, act_id)

    return router
