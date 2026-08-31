const assert = require('assert/strict');
const http = require('http');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = 'http://supabase.invalid';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.JWT_SECRET = 'test-jwt-secret';
process.env.INTERNAL_API_KEY = 'test-internal-api-key';
process.env.CORS_ALLOWED_ORIGINS = 'http://allowed.example';
process.env.AUTH_RATE_LIMIT_WINDOW_MS = '60000';
process.env.AUTH_RATE_LIMIT_MAX = '2';
process.env.TRUST_PROXY_HOPS = '1';
process.env.REQUIRE_IDEMPOTENCY_TABLE = 'false';

let readinessMode = 'missing';
let readinessChecks = 0;
const fakeSupabase = {
  from() {
    return {
      select() {
        return this;
      },
      limit() {
        readinessChecks += 1;
        if (readinessMode === 'reject') {
          return Promise.reject(new Error('provider SQL details must stay private'));
        }
        if (readinessMode === 'ok') {
          return Promise.resolve({ error: null });
        }
        return Promise.resolve({
          error: {
            code: 'PGRST205',
            message: 'provider SQL details must stay private',
          },
        });
      },
    };
  },
};

const dbModulePath = require.resolve('../src/config/db');
require.cache[dbModulePath] = {
  id: dbModulePath,
  filename: dbModulePath,
  loaded: true,
  exports: fakeSupabase,
};

const app = require('../src/app');
const aiControllerPath = require.resolve('../src/controllers/aiController');
const adminControllerPath = require.resolve('../src/controllers/adminController');

function parseResponse(response) {
  return response.text().then((text) => {
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = text;
    }
    return { response, body, text };
  });
}

async function request(baseUrl, path, options = {}) {
  return parseResponse(await fetch(`${baseUrl}${path}`, options));
}

async function main() {
  assert.equal(require.cache[aiControllerPath], undefined, 'legacy AI controllers must not load');
  assert.equal(require.cache[adminControllerPath], undefined, 'admin controllers must not load');

  const server = http.createServer(app);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const baseUrl = `http://127.0.0.1:${port}`;

  try {
    const legacy = await request(baseUrl, '/api/v1/ai/recommend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: 'other-user' }),
    });
    assert.equal(legacy.response.status, 410);
    assert.match(legacy.body.error, /no longer available/i);
    assert.equal(readinessChecks, 0, 'legacy routes must not query Supabase');

    const admin = await request(baseUrl, '/api/v1/admin/stats');
    assert.equal(admin.response.status, 404);
    assert.deepEqual(admin.body, { error: 'Not found.' });
    assert.equal(readinessChecks, 0, 'disabled admin routes must not query Supabase');

    const allowedCors = await request(baseUrl, '/api/v1/auth/login', {
      method: 'OPTIONS',
      headers: {
        Origin: 'http://allowed.example',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type',
      },
    });
    assert.equal(allowedCors.response.status, 204);
    assert.equal(allowedCors.response.headers.get('access-control-allow-origin'), 'http://allowed.example');
    assert.notEqual(allowedCors.response.headers.get('access-control-allow-credentials'), 'true');

    const deniedCors = await request(baseUrl, '/api/v1/auth/login', {
      method: 'OPTIONS',
      headers: {
        Origin: 'http://evil.example',
        'Access-Control-Request-Method': 'POST',
      },
    });
    assert.equal(deniedCors.response.headers.get('access-control-allow-origin'), null);
    assert.notEqual(deniedCors.response.headers.get('access-control-allow-credentials'), 'true');

    const readinessMissing = await request(baseUrl, '/api/readiness');
    assert.equal(readinessMissing.response.status, 200);
    assert.equal(readinessMissing.body.idempotency.table_status, 'missing');
    assert.equal(readinessMissing.body.idempotency.table_available, false);
    assert.equal(readinessMissing.body.idempotency.memory_fallback_enabled, true);
    assert.doesNotMatch(readinessMissing.text, /PGRST205|provider SQL details|ttl_ms|max_entries|statuses/i);

    readinessMode = 'ok';
    const readinessAvailable = await request(baseUrl, '/api/readiness');
    assert.equal(readinessAvailable.response.status, 200);
    assert.equal(readinessAvailable.body.idempotency.table_status, 'available');
    assert.equal(readinessAvailable.body.idempotency.table_available, true);
    assert.doesNotMatch(readinessAvailable.text, /PGRST205|provider SQL details|ttl_ms|max_entries|statuses/i);

    readinessMode = 'reject';
    const readinessRejected = await request(baseUrl, '/api/readiness');
    assert.equal(readinessRejected.response.status, 503);
    assert.equal(readinessRejected.body.ok, false);
    assert.equal(readinessRejected.body.idempotency.table_status, 'unavailable');
    assert.doesNotMatch(readinessRejected.text, /provider SQL details|ttl_ms|max_entries|statuses/i);

    const authAttempt = (path, forwardedFor) => request(baseUrl, path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Forwarded-For': forwardedFor,
      },
      body: '{}',
    });
    assert.equal((await authAttempt('/api/v1/auth/signup', '203.0.113.10')).response.status, 400);
    assert.equal((await authAttempt('/api/v1/auth/signup', '203.0.113.10')).response.status, 400);
    assert.equal((await authAttempt('/api/v1/auth/signup', '203.0.113.10')).response.status, 429);
    assert.equal((await authAttempt('/api/v1/auth/login', '198.51.100.20')).response.status, 400);
    assert.equal((await authAttempt('/api/v1/auth/login', '198.51.100.20')).response.status, 400);
    assert.equal((await authAttempt('/api/v1/auth/login', '198.51.100.20')).response.status, 429);

    const protectedRoute = await request(baseUrl, '/api/v1/users/profile');
    assert.equal(protectedRoute.response.status, 401, 'rate limit must not apply to user routes');

    console.log('[security-contracts] 10 scenarios passed');
  } finally {
    await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
