FROM golang:1.24-bookworm AS builder

RUN git clone --depth 1 \
    --branch RELEASE.2025-10-15T17-29-55Z \
    https://github.com/minio/minio.git /src

WORKDIR /src
RUN CGO_ENABLED=0 go build -p 2 -trimpath -o /out/minio .

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /out/minio /usr/local/bin/minio
COPY --from=builder /src/LICENSE /usr/share/licenses/minio/LICENSE

EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
