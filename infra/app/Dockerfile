FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY services/app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/__init__.py services/__init__.py
COPY services/app services/app
COPY services/graph services/graph
COPY data/policies data/policies
COPY data/injected_alerts.json data/injected_alerts.json
RUN useradd --create-home app && mkdir -p /app/.cache/agents && chown -R app /app/.cache
USER app
CMD exec uvicorn services.app.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 75
