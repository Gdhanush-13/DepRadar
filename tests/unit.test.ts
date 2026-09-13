import { describe, expect, it } from 'vitest';
import { printSchema } from 'graphql';
import { schema } from '../src/graphql.js';
import { rateLimitConfig } from '../src/rate.js';

describe('Overfetch contract', () => {
  it('exposes the documented GraphQL schema and deprecation', () => {
    const printed = printSchema(schema);
    expect(printed).toContain('legacyTitle: String @deprecated(reason: "Use title instead")');
    expect(printed).toContain('search(');
  });
  it('uses a bounded expensive-request rate limit', () => {
    expect(rateLimitConfig).toEqual({ requests: 20, windowMinutes: 10 });
  });
});
