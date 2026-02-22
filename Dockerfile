FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8080 8181

CMD ["./start.sh"]
