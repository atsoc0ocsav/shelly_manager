FROM ghcr.io/home-assistant/base-python:3.13-alpine3.24

RUN apk add --no-cache git openssh-client

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock LICENSE ./
# uv is mounted for the build only, so it is not shipped in the image.
RUN --mount=from=ghcr.io/astral-sh/uv:0.9,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=from=ghcr.io/astral-sh/uv:0.9,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev

COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: Shelly Manager" \
    org.opencontainers.image.source="https://github.com/atsoc0ocsav/shelly_manager" \
    org.opencontainers.image.licenses="MIT"
