FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app ./app
COPY pipelines ./pipelines
COPY evaluation ./evaluation
COPY configs ./configs
COPY dataset/recipe_kb ./dataset/recipe_kb
RUN useradd --create-home mealagent && mkdir -p runtime artifacts && chown -R mealagent:mealagent runtime artifacts
USER mealagent
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).close()"
CMD ["python", "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
