FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv sync --frozen || uv sync

EXPOSE 8000

CMD ["uv", "run", "python", "server.py"]
