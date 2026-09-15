from fastapi import APIRouter

# A router is a group of endpoints. Each file under routes/ owns one router,
# and main.py plugs them into the application.
router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up and can answer HTTP.

    Must not depend on external systems. If this fails, the orchestrator
    restarts the container.
    """
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, object]:
    """Readiness: the service can accept traffic.

    External dependencies (database, LLM provider) are checked here as they
    are added. If this fails, the orchestrator stops routing traffic to this
    instance but does not restart it.
    """
    checks: dict[str, str] = {}  # e.g. {"database": "ok"} once PostgreSQL exists
    return {"status": "ready", "checks": checks}
