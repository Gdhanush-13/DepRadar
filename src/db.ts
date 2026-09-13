import { PrismaClient } from '@prisma/client';
export const prisma = new PrismaClient({ log: [{ emit: 'event', level: 'query' }, 'error'] });
export type QueryCounter = { count: number };
let activeCounter: QueryCounter | undefined;
prisma.$on('query', () => { if (activeCounter) activeCounter.count += 1; });
export function countQueries(counter: QueryCounter): () => void {
  activeCounter = counter;
  return () => { activeCounter = undefined; };
}
