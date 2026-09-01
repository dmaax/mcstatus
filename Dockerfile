FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Dependencies first so code changes don't invalidate the install layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8080

# exec so gunicorn replaces the shell and receives Fly's signals directly.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 8 --timeout 45 --access-logfile -"]
