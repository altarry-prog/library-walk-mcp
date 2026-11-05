# Hosted image for 「도서관 산책 읽기전용」 MCP server. No secrets are baked in.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER 10001:10001
EXPOSE 8080
CMD ["python", "main.py"]
