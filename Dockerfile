# ── Stage 1: Build Rust binaries ─────────────────────────
FROM rust:1.88-bookworm AS builder

WORKDIR /app

# Copy manifests first for dependency caching
COPY Cargo.toml Cargo.lock ./
COPY api/Cargo.toml api/Cargo.toml
COPY cli/Cargo.toml cli/Cargo.toml
COPY core/Cargo.toml core/Cargo.toml
COPY mcp/Cargo.toml mcp/Cargo.toml
COPY mcp-runtime/Cargo.toml mcp-runtime/Cargo.toml

# Create dummy sources to cache dependency compilation
RUN mkdir -p api/src cli/src core/src mcp/src mcp-runtime/src && \
    echo "fn main() {}" > api/src/main.rs && \
    echo "fn main() {}" > cli/src/main.rs && \
    echo "" > core/src/lib.rs && \
    echo "fn main() {}" > mcp/src/main.rs && \
    echo "" > mcp-runtime/src/lib.rs

# Build dependencies only (cached layer)
RUN cargo build --release -p kura-api -p kura-cli 2>/dev/null || true

# Copy real source code + migrations
COPY api/src api/src
COPY cli/src cli/src
COPY core/src core/src
COPY migrations migrations

# Touch source files to invalidate the dummy build
RUN touch api/src/main.rs cli/src/main.rs core/src/lib.rs

# Build real binaries
RUN cargo build --release -p kura-api -p kura-cli

# ── Stage 2: API runtime ────────────────────────────────
FROM debian:bookworm-slim AS api

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/kura-api /usr/local/bin/kura-api

EXPOSE 3000
CMD ["kura-api"]

# ── Stage 3: CLI only ───────────────────────────────────
FROM debian:bookworm-slim AS cli

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/kura /usr/local/bin/kura

ENTRYPOINT ["kura"]
