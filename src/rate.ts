import type { Request, Response, NextFunction } from 'express';
const LIMIT = 20; const WINDOW_MS = 600_000; const buckets = new Map<string, number[]>();
export function rateLimit(req: Request, res: Response, next: NextFunction): void {
  if (req.method !== 'POST' && !req.path.startsWith('/benchmark/')) { next(); return; }
  const ip = req.headers['x-forwarded-for']?.toString().split(',')[0].trim() || req.ip || 'unknown'; const now = Date.now();
  const recent = (buckets.get(ip) ?? []).filter((stamp) => now - stamp < WINDOW_MS);
  if (recent.length >= LIMIT) { res.status(429).json({ error: 'Too many expensive requests; try again later' }); return; }
  recent.push(now); buckets.set(ip, recent); if (buckets.size > 10_000) buckets.delete(buckets.keys().next().value ?? ip); next();
}
export const rateLimitConfig = { requests: LIMIT, windowMinutes: 10 };
