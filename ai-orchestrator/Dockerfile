# ai-orchestrator — imagen para Docker y Podman.
#
#   docker build -t ai-orchestrator .        |  podman build -t ai-orchestrator .
#
# Binario estático (musl) sobre Alpine: sin glibc ni certificados del sistema
# (rustls trae sus raíces). Nombres de imagen completos para que Podman no pregunte.

FROM docker.io/library/rust:1.97-alpine AS build
RUN apk add --no-cache musl-dev
WORKDIR /src
# Dependencias primero: se cachean mientras no cambie Cargo.toml/Cargo.lock.
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && touch src/lib.rs \
    && cargo build --release --locked && rm -rf src
COPY src ./src
COPY ui ./ui
COPY config.yaml ./
RUN touch src/main.rs src/lib.rs && cargo build --release --locked

FROM docker.io/library/alpine:3.22
RUN adduser -D -H -u 10001 aio && mkdir -p /data && chown aio /data
COPY --from=build /src/target/release/ai-orchestrator /usr/local/bin/ai-orchestrator
COPY config.yaml /app/config.yaml
ENV DATA_DIR=/data CONFIG_SEED=/app/config.yaml HOST=0.0.0.0 PORT=4000 RUST_LOG=info
USER aio
VOLUME /data
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD wget -qO- http://127.0.0.1:4000/health || exit 1
ENTRYPOINT ["/usr/local/bin/ai-orchestrator"]
