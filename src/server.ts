import express from 'express';
import { prisma, countQueries } from './db.js';
import { context } from './graphql.js';
import { rateLimit } from './rate.js';
import { createYoga } from 'graphql-yoga';
import { schema } from './graphql.js';
export const app = express(); const port = Number(process.env.PORT ?? 3000);
if (process.env.OVERFETCH_ENV === 'production' && (!process.env.DATABASE_URL || !process.env.AUTH_SECRET || process.env.AUTH_SECRET.includes('change-me'))) throw new Error('DATABASE_URL and a real AUTH_SECRET are required in production');
app.use(express.json({ limit: '64kb' })); app.use(rateLimit); app.use(express.static('public'));
app.get('/healthz', async (_req, res) => { await prisma.$queryRaw`SELECT 1`; res.json({ status: 'ok' }); });
app.get('/api/posts', async (req, res) => { const limit = Math.min(Number(req.query.limit ?? 10), 100); const posts = await prisma.post.findMany({ take: limit, orderBy: { createdAt: 'desc' }, include: { author: true, _count: { select: { comments: true } } } }); res.json(posts); });
app.get('/api/authors/:id', async (req, res) => { const row = await prisma.author.findUnique({ where: { id: Number(req.params.id) }, include: { posts: { take: 5, orderBy: { createdAt: 'desc' }, include: { tags: { include: { tag: true } } } } } }); if (!row) { res.status(404).json({ error: 'Author not found' }); return; } res.json(row); });
app.get('/api/posts/:id/thread', async (req, res) => { if (process.env.FLAKY_RECOMMENDATIONS === 'fail') { res.status(500).json({ error: 'Recommendation service unavailable' }); return; } const row = await prisma.post.findUnique({ where: { id: Number(req.params.id) }, include: { comments: { include: { author: true } }, author: true } }); if (!row) { res.status(404).json({ error: 'Post not found' }); return; } res.json(row); });
app.post('/api/posts/:id/comments', async (req, res) => { const row = await prisma.comment.create({ data: { postId: Number(req.params.id), authorId: Number(req.body.authorId), body: String(req.body.body ?? '') } }); res.status(201).json(row); });
const yoga = createYoga({ schema, graphqlEndpoint: '/graphql', context: ({ request }) => context(request.headers.get('authorization')?.replace('Bearer ', '')) });
app.use('/graphql', (req, res, next) => {
  if (req.method === 'POST' && typeof req.body?.query === 'string') {
    const fieldCount = (req.body.query.match(/\b[_A-Za-z][_0-9A-Za-z]*\b/g) ?? []).length;
    if (fieldCount > 80) { res.status(400).json({ errors: [{ message: 'Query complexity exceeded the limit of 80 fields' }] }); return; }
  }
  next();
});
app.use('/graphql', yoga);
app.get('/benchmark/:scenario', async (req, res) => { const scenario = String(req.params.scenario); const restStarted = performance.now(); const restPosts = await prisma.post.findMany({ take: 10, orderBy: { createdAt: 'desc' }, include: { author: true, _count: { select: { comments: true } } } }); const restBody = JSON.stringify(restPosts); const restMs = performance.now() - restStarted; const gqlStarted = performance.now(); const counter = { count: 0 }; const stop = countQueries(counter); try { const gql = await yoga.fetch('http://localhost/graphql', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ query: '{ posts(limit: 10) { id title author { name } comments { id } } }' }) }); const gqlBody = await gql.text(); res.json({ scenario, rest: { requests: 1, bytes: restBody.length, milliseconds: Math.round(restMs) }, graphql: { requests: 1, bytes: gqlBody.length, milliseconds: Math.round(performance.now() - gqlStarted) }, databaseQueries: { dataloaderEnabled: counter.count } }); } finally { stop(); } });
if (process.env.VITEST !== 'true') app.listen(port, () => console.log(`Overfetch listening on ${port}`));
