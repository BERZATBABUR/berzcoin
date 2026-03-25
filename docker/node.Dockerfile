FROM python:3.11-slim

LABEL maintainer="BerzCoin Team"
LABEL description="BerzCoin Node"

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash berzcoin

COPY requirements.txt pyproject.toml README.md LICENSE /opt/berzcoin/
COPY node /opt/berzcoin/node
COPY shared /opt/berzcoin/shared
COPY cli /opt/berzcoin/cli

WORKDIR /opt/berzcoin

RUN pip3 install --no-cache-dir -r requirements.txt \
    && pip3 install --no-cache-dir -e .

USER berzcoin

VOLUME ["/var/lib/berzcoin"]

EXPOSE 8333 8332 18333 18332 18444 18443

ENTRYPOINT ["berzcoind"]
CMD ["-datadir", "/var/lib/berzcoin"]
