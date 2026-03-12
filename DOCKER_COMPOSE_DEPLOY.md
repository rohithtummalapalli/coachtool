# Docker Compose Deployment (Chainlit + Django + MCP)

## 1. Prepare environment

1. Copy `.env.docker.example` to `.env`.
2. Fill all required values, especially:
   - `AZURE_OPENAI_*`
   - `DATABASE_URL` (RDS PostgreSQL, sync URL)
   - `CHAINLIT_DATABASE_URL` (RDS PostgreSQL, async URL)
   - `OPENWEBUI_API_TOKEN`

## 2. Build and run

```bash
docker compose build
docker compose up -d
```

Services:
- Chainlit: `http://localhost` (host port 80)
- Django: internal-only service (`backend:8001`)
- MCP: internal-only service (`mcp:8765`)

## 3. Verify health

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f mcp
docker compose logs -f chainlit
```

## 4. Elastic Beanstalk notes (Docker Compose)

- Use Elastic Beanstalk Docker platform with source bundle containing:
  - `docker-compose.yml`
  - `Dockerfile`
  - `docker/` scripts
- Configure env vars in Beanstalk Environment Configuration (Secrets Manager source).
- For production, expose only the Chainlit service through the load balancer.
- Keep `DATABASE_URL` and `CHAINLIT_DATABASE_URL` pointed to RDS.
