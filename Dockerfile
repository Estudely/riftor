# riftor — offensive-security AI agent TUI
# Build:  docker build -t riftor .
# Run:    docker run -it --rm -e ANTHROPIC_API_KEY -v "$PWD:/work" riftor
#
# Note: this is a minimal image (no nmap/httpx/etc.). For full recon tooling,
# run riftor on a host that has the tools, or extend this image.

FROM python:3.12-slim AS build
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim
LABEL org.opencontainers.image.title="riftor" \
      org.opencontainers.image.source="https://github.com/Estudely/riftor" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

RUN useradd --create-home riftor
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

USER riftor
WORKDIR /work
ENTRYPOINT ["riftor"]
