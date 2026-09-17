FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app
ENV PYTHONPATH=/app

ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.35/supercronic-linux-amd64 \
	SUPERCRONIC_SHA1SUM=fcecf7184eb918d6252e213fa4a64d4ed26690c5

COPY scripts/xibo_sync /app/xibo_sync
COPY services/calendar_render_service /app/calendar_render_service
COPY scripts/templates/calendar /app/templates/calendar
COPY scripts/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

RUN curl -fsSLO "$SUPERCRONIC_URL" \
	&& echo "${SUPERCRONIC_SHA1SUM}  supercronic-linux-amd64" | sha1sum -c - \
	&& chmod +x supercronic-linux-amd64 \
	&& mv supercronic-linux-amd64 /usr/local/bin/supercronic

CMD ["python", "-m", "calendar_render_service.scheduler"]