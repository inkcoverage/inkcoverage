FROM python:3.12-slim-bookworm AS ghostscript

# Match the locally tested security release; do not rely on an older distro GS.
ARG GS_VERSION=10.08.0
ARG GS_RELEASE=gs10080
ARG GS_BUILD_JOBS=2
ARG GS_SHA512=8006e2a32d03759a905b9548bdd83d4563173041006750e17e428b9eea24ad519aa4452ceecd7c073d3c420a68bf1b07ccc9e0533b1a05935f6b39ea8d9ce875
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential curl ca-certificates xz-utils && \
    curl --fail --location --output /tmp/gs.tar.xz \
      "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/${GS_RELEASE}/ghostscript-${GS_VERSION}.tar.xz" && \
    echo "${GS_SHA512}  /tmp/gs.tar.xz" | sha512sum --check --strict && \
    mkdir /tmp/gs && tar -xJf /tmp/gs.tar.xz -C /tmp/gs --strip-components=1 && \
    cd /tmp/gs && ./configure --prefix=/usr/local --without-x --disable-cups --disable-gtk && \
    make -j"${GS_BUILD_JOBS}" && make install && test "$(/usr/local/bin/gs --version)" = "${GS_VERSION}"

FROM python:3.12-slim-bookworm
RUN python -c "import sys; assert sys.version_info >= (3, 12, 15), 'Use a current Python security image'"
COPY --from=ghostscript /usr/local/bin/gs /usr/local/bin/gs
COPY --from=ghostscript /usr/local/share/ghostscript/ /usr/local/share/ghostscript/

# Install Ghostscript and fonts for og:image generation
RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-liberation && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the application bundle, excluding deployment archives and logs.
COPY app.py workload.py lifetime.py outcomes.py pdf_colors.py localization.py heatmap.py channel_preview.py ./
COPY static/ ./static/

# Create temp directory with correct permissions
RUN mkdir -p /tmp/inkcoverage && chown appuser:appuser /tmp/inkcoverage

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "32"]
