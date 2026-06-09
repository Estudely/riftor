# riftor — offensive-security AI agent TUI
# Build (minimal):  docker build -t riftor .
# Build (+ tools):  docker build --build-arg INSTALL_TOOLS=1 -t riftor:full .
# Run:              docker run -it --rm -e ANTHROPIC_API_KEY -v "$PWD:/work" riftor
#
# The minimal image has no recon binaries. Pass INSTALL_TOOLS=1 (or use the
# `full` service in docker-compose.yml) to bundle nmap/curl/dnsutils/etc.

FROM python:3.12-slim AS build
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim
LABEL org.opencontainers.image.title="riftor" \
      org.opencontainers.image.source="https://github.com/Estudely/riftor" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

# Optional recon tooling. apt packages that exist in Debian slim; heavier tools
# (httpx/nuclei/ffuf) are best layered on top or run from the host.
ARG INSTALL_TOOLS=0
RUN if [ "$INSTALL_TOOLS" = "1" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends \
            nmap curl dnsutils whatweb nikto ncat ripgrep && \
        rm -rf /var/lib/apt/lists/*; \
    fi

RUN useradd --create-home riftor
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

USER riftor
WORKDIR /work
ENTRYPOINT ["riftor"]
