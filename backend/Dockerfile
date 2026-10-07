# Backend Go del CRM (SQLite puro Go: sin CGO)
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/server ./cmd/server \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/kommo-seed ./cmd/kommo-seed \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/jmd ./cmd/jmd

FROM alpine:3.22
RUN apk add --no-cache ca-certificates tzdata && adduser -D -u 10001 app && mkdir -p /data && chown app /data
COPY --from=build /out/server /out/kommo-seed /out/jmd /usr/local/bin/
USER app
ENV PORT=8080 DATA_DIR=/data TZ=America/Lima
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/local/bin/server"]
