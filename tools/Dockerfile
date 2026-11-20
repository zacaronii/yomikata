# The Yomikata tools with everything they need (chdman for CHD images), so anyone can run them the same way:
#   docker build -t yomikata-tools tools/
#   docker run --rm -v "$PWD:/catalog" -v /path/to/games:/games:ro yomikata-tools identify /games/*.chd
# The same image runs the sample inbox: `yomikata serve` (see docs/inbox.md).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends mame-tools && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]" && rm -rf /root/.cache
WORKDIR /catalog
USER 99:100
EXPOSE 8080
ENTRYPOINT ["yomikata"]
