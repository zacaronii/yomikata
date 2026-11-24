# The Yomikata tools with everything they need (chdman for CHD images, the reader runtime for `yomikata run`), so
# anyone can run them the same way. Built from the repository's root, so that it sees runtime/:
#   docker build -t yomikata-tools -f tools/Dockerfile .
#   docker run --rm -v "$PWD:/catalog" -v /path/to/games:/games:ro yomikata-tools identify /games/*.chd
# The same image runs the sample inbox: `yomikata serve` (see docs/inbox.md).

# The reader runtime (runtime/README.md), with the C++ library inside it: the image below has no compiler.
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends g++ cmake make && rm -rf /var/lib/apt/lists/*
COPY runtime /src/runtime
RUN cmake -S /src/runtime -B /build -DCMAKE_SHARED_LINKER_FLAGS="-static-libstdc++ -static-libgcc" \
    && cmake --build /build -j

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends mame-tools && rm -rf /var/lib/apt/lists/*
COPY --from=runtime /build/libyomikata_reader.so /usr/local/lib/
ENV YOMIKATA_READER_LIB=/usr/local/lib/libyomikata_reader.so
WORKDIR /src
COPY tools/pyproject.toml ./
COPY tools/src ./src
RUN pip install --no-cache-dir ".[server]" && rm -rf /root/.cache
WORKDIR /catalog
USER 99:100
EXPOSE 8080
ENTRYPOINT ["yomikata"]
