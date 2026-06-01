FROM python:3.12-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY gateways ./gateways
COPY scripts ./scripts

RUN pip install --no-cache-dir -e .
RUN npm ci --prefix gateways/edopro-ocgcore

ENV YGOTRAIN_DATA_DIR=/data
ENV PORT=8765

EXPOSE 8765

CMD ["bash", "scripts/fly-start.sh"]
