# Overfetch

REST vs GraphQL. Same data. Real numbers, not opinions.

Overfetch is a TypeScript/Express/Prisma publishing API that runs the same realistic blog screens through REST and GraphQL over one PostgreSQL database. The report viewer measures HTTP requests, bytes, wall-clock time, and Prisma query events; it does not use mocked benchmark values.

## Run locally

```bash
cp .env.example .env
docker compose up --build
docker compose exec app npx prisma db seed
```

Open http://localhost:3000. JSON REST is under `/api`; GraphQL is at `/graphql`; benchmark JSON is at `/benchmark/homepage-feed`.

## Seed volume

The seed creates 50 authors, 500 posts, 3,000 comments, 20 tags, and two tags per post. The GraphQL context creates real DataLoader instances when `DATALOADER_ENABLED=true`; set it to `false` to expose the N+1 query pattern.

## Configuration

`DATABASE_URL`, `PORT`, `OVERFETCH_ENV`, `AUTH_SECRET`, and `DATALOADER_ENABLED` are documented in `.env.example`. Production startup rejects missing or obvious placeholder secrets. Benchmark and mutation POST endpoints are limited to 20 requests per IP per 10-minute window, using the first `X-Forwarded-For` address behind Render.

## Quality

```bash
npm ci && npm run prisma:generate
npm run typecheck
npm test
```

Prisma migrations are checked in under `prisma/migrations`; deployment uses Docker and Render with Neon PostgreSQL.
