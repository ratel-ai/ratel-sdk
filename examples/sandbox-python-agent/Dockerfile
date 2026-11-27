# A slim image: `ratel_ai.serve` uses the standard library only, so the image needs no wheel.
# `pnpm vendor` copies serve.py into vendor/ first. With ratel-ai >= 0.15 from PyPI, replace the
# first COPY with `RUN pip install --no-cache-dir ratel-ai`.
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY vendor/ratel_ai ./ratel_ai
COPY agent.py tools.py ./
USER nobody
EXPOSE 8080
CMD ["python", "agent.py"]
