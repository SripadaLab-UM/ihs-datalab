# DataLab agent image: Codex CLI plus the R and Python research toolkit.
#
# One image serves data sessions, research sessions, and workflow R steps.
# It holds no credentials. At runtime DataLab mounts the session's workspace
# and Codex home, attaches the container only to the session's internal
# network, and drives Codex with `docker exec -i ... codex app-server`.
#
# Build context: images/agent
#   docker build -t datalab-agent:dev images/agent
#
# Everything is pinned so every colleague runs the same tools:
# - R: the Rocker r-ver image (exact R version), with CRAN packages from a
#   dated Posit Package Manager snapshot;
# - Codex CLI and Node: exact versions;
# - Python packages: requirements.txt (see requirements.in).

FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS node
ARG CODEX_VERSION=0.157.1
RUN npm install -g "@openai/codex@${CODEX_VERSION}" && npm cache clean --force

FROM rocker/r-ver:4.6.1@sha256:4e6e6696ff54a86625c7f80046478e67014a2184e942b85eeff43db7b992d93e

# Freeze CRAN at a date, so rebuilding the image gives the same R packages.
ARG CRAN_SNAPSHOT=https://p3m.dev/cran/__linux__/noble/2026-09-25
ENV CRAN=${CRAN_SNAPSHOT} \
    DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl file findutils git jq less procps ripgrep sqlite3 tree unzip zip \
        pandoc poppler-utils qpdf tesseract-ocr ghostscript \
        fonts-dejavu-core fonts-liberation \
        python3 python3-venv \
        libxml2 libfontconfig1 libfreetype6 libharfbuzz0b libfribidi0 libpng16-16 libtiff6 libjpeg-turbo8 \
    && rm -rf /var/lib/apt/lists/*

# R packages: binaries from the snapshot above.
RUN echo "options(repos = c(CRAN = '${CRAN}'))" >> "${R_HOME}/etc/Rprofile.site" \
    && install2.r --error --skipinstalled --ncpus -1 \
        tidyverse data.table lubridate readxl openxlsx vroom jsonlite yaml glue withr \
        DBI RSQLite arrow \
        broom lme4 lmerTest survival mgcv \
        ggplot2 patchwork cowplot ggrepel viridis plotly svglite ragg \
        rmarkdown knitr flextable officer skimr \
        testthat lintr \
    && rm -rf /tmp/downloaded_packages

# Node and the Codex CLI, from the first stage.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex && codex --version

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/datalab-python \
    && /opt/datalab-python/bin/pip install --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
ENV PATH="/opt/datalab-python/bin:${PATH}"

# Helper commands, app skills, and the base instructions Codex reads.
COPY bin/ /usr/local/bin/
COPY skills/ /etc/codex/skills/
COPY AGENTS.md /etc/datalab/AGENTS.md
RUN chmod 0755 /usr/local/bin/profile-data /usr/local/bin/render-report \
    && find /etc/codex/skills -type d -exec chmod 0755 {} + \
    && find /etc/codex/skills /etc/datalab -type f -exec chmod 0644 {} +

# Non-root. DataLab mounts /work, /codex-home, /inputs, and /data/oracle.
RUN useradd --create-home --uid 10004 --shell /bin/bash agent \
    && mkdir -p /work /codex-home /inputs /data/oracle \
    && chown agent:agent /work /codex-home
USER agent
WORKDIR /work
ENV HOME=/home/agent \
    CODEX_HOME=/codex-home \
    MPLCONFIGDIR=/tmp/matplotlib \
    R_LIBS_USER=/tmp/r-library

# Smoke checks for the tools agents rely on most.
RUN codex --version \
    && python -c "import duckdb, pandas, polars, numpy, scipy, statsmodels, sklearn, matplotlib, seaborn, altair, pyarrow, pdfplumber" \
    && Rscript -e "suppressMessages({library(dplyr); library(tidyr); library(data.table); library(lubridate); library(ggplot2); library(lme4); library(arrow); library(testthat)})" \
    && printf 'a,b\n1,2\n3,\n' > /tmp/smoke.csv && profile-data /tmp/smoke.csv > /dev/null && rm /tmp/smoke.csv

ENTRYPOINT []
# Put the base instructions where Codex reads them, then wait: DataLab runs
# Codex with `docker exec`.
CMD ["sh", "-c", "cp /etc/datalab/AGENTS.md \"$CODEX_HOME/AGENTS.md\" && exec sleep infinity"]
