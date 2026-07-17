# Multi-stage build: frontend (Vite + TS) then backend (Python)

# --- Stage 1: Frontend build ---
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
RUN corepack enable && corepack prepare pnpm@9 --install
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# --- Stage 2: Backend ---
FROM python:3.13-slim AS backend
WORKDIR /app/backend

# Install deps
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

ENV SONIOX_API_KEY=""
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:8765/health').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]