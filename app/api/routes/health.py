from fastapi import APIRouter, Request, Response, status

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
def ready(request: Request, response: Response) -> dict[str, object]:
    """Readiness: the service can accept traffic.

    External dependencies are checked here as they are added. If this fails,
    the orchestrator stops routing traffic to this instance but does not
    restart it.
    """
    llm_configured = getattr(request.app.state, "analysis_service", None) is not None
    checks: dict[str, str] = {"llm": "ok" if llm_configured else "not_configured"}
    if not llm_configured:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "checks": checks}
    return {"status": "ready", "checks": checks}
