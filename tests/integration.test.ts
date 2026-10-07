import { beforeAll, afterAll, describe, expect, it } from 'vitest';
import { prisma, countQueries } from '../src/db.js';
import { graphql } from 'graphql';
import { context, schema } from '../src/graphql.js';

let available = false;
beforeAll(async () => { if (!process.env.DATABASE_URL) return; try { await prisma.$queryRaw`SELECT 1`; available = true; } catch { available = false; } });
afterAll(async () => { if (available) await prisma.$disconnect(); });

describe('PostgreSQL integration', () => {
  it('confirms the realistic seed volume', async () => {
    if (!available) return;
    const [authors, posts, comments, tags] = await Promise.all([prisma.author.count(), prisma.post.count(), prisma.comment.count(), prisma.tag.count()]);
    expect(authors).toBeGreaterThanOrEqual(50); expect(posts).toBeGreaterThanOrEqual(500); expect(comments).toBeGreaterThanOrEqual(3000); expect(tags).toBeGreaterThanOrEqual(20);
  });
  it('measures fewer deep-thread author queries with DataLoader', async () => {
    if (!available) return;
    const post = await prisma.post.findFirstOrThrow({ include: { comments: { take: 40 } } });
    const query = `{ post(id: ${post.id}) { comments { author { id postCount } } } }`;
    process.env.DATALOADER_ENABLED = 'false'; const without = { count: 0 }; const stopWithout = countQueries(without); await graphql({ schema, source: query, contextValue: context() }); stopWithout();
    process.env.DATALOADER_ENABLED = 'true'; const withLoader = { count: 0 }; const stopWith = countQueries(withLoader); await graphql({ schema, source: query, contextValue: context() }); stopWith();
    expect(withLoader.count).toBeLessThan(without.count); expect(withLoader.count).toBeLessThan(10);
  });
});
