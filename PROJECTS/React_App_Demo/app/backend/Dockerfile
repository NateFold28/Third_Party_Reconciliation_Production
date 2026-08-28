FROM python:3.11-slim

WORKDIR /app

# Copy backend source
COPY app/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/backend/ .

# Copy SQL files so the backend can read them at runtime
# Build context is the project root (see build_and_push.ps1)
COPY sql/ ./sql/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
