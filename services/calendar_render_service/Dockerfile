FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app
ENV PYTHONPATH=/app

COPY scripts/xibo_sync /app/xibo_sync
COPY services/calendar_render_service /app/calendar_render_service
COPY scripts/templates/calendar /app/templates/calendar
COPY scripts/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

CMD ["python", "-m", "calendar_render_service.service"]