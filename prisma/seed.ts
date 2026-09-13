import { PrismaClient } from '@prisma/client';
const prisma = new PrismaClient();
const tags = await Promise.all(Array.from({ length: 20 }, (_, i) => prisma.tag.upsert({ where: { name: `topic-${i + 1}` }, update: {}, create: { name: `topic-${i + 1}` } })));
const authors = await Promise.all(Array.from({ length: 50 }, (_, i) => prisma.author.upsert({ where: { email: `author${i + 1}@overfetch.dev` }, update: {}, create: { name: `Author ${i + 1}`, email: `author${i + 1}@overfetch.dev` } })));
const existing = await prisma.post.count();
if (existing < 500) for (let i = existing; i < 500; i++) { const p = await prisma.post.create({ data: { title: `Engineering note ${i + 1}`, body: `A realistic seeded post body for benchmark scenario ${i + 1}.`, authorId: authors[i % authors.length].id, status: i % 7 === 0 ? 'draft' : 'published', tags: { create: [{ tagId: tags[i % tags.length].id }, { tagId: tags[(i + 3) % tags.length].id }] } } }); await prisma.comment.createMany({ data: Array.from({ length: 6 }, (_, j) => ({ body: `Thoughtful comment ${j + 1}`, postId: p.id, authorId: authors[(i + j) % authors.length].id })) }); }
await prisma.$disconnect();
