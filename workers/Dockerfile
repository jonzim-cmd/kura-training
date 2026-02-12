FROM python:3.12-slim

WORKDIR /app

# curl for Docker healthcheck, g++ for PyTensor C compilation (PyMC MCMC)
RUN apt-get update && apt-get install -y --no-install-recommends curl g++ && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install dependencies
RUN uv pip install --system .

CMD ["kura-worker"]
