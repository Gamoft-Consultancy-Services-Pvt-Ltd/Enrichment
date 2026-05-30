"""Application entry point. Builds the FastAPI app and exposes /health."""

from fastapi import FastAPI

from core.lifespan import lifespan

app = FastAPI(title="Lead Intelligence Engine", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint used by Docker, CI, and load balancers."""
    return {"status": "ok"}
