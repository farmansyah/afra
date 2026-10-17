FROM python:3.12-slim
WORKDIR /app
COPY relay/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY relay/relay.py .
COPY static/chat/ ./web/
ENV RELAY_DATA=/data WEB_DIR=/app/web
VOLUME /data
EXPOSE 8080
CMD ["uvicorn", "relay:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--ws-max-size", "131072"]
