"""Application entry point. Builds the FastAPI app and exposes /health."""

from fastapi import FastAPI

app = FastAPI(title="Lead Intelligence Engine", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint used by Docker, CI, and load balancers."""
    return {"status": "ok"}
