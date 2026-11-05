FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY server.py planner.py providers.py ./
COPY data/ ./data/
USER 10001:10001
EXPOSE 8000
CMD ["python", "server.py"]
