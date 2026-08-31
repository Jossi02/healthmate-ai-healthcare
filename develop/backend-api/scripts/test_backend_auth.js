const assert = require('node:assert/strict');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const jwt = require('jsonwebtoken');

const {
  SecurityConfigError,
  loadSecurityConfig,
  safeSecretEqual,
} = require('../src/config/security');
const { createAuthMiddleware } = require('../src/middleware/auth');
const { createInternalAuth } = require('../src/middleware/internalAuth');

function invoke(middleware, headers) {
  let statusCode = null;
  let nextCalled = false;
  const response = {
    status(code) {
      statusCode = code;
      return this;
    },
    json() {
      return this;
    },
  };
  middleware({ headers, method: 'GET', path: '/test' }, response, () => {
    nextCalled = true;
  });
  return { nextCalled, statusCode };
}

function startupWith(overrides) {
  return spawnSync(process.execPath, [path.join(__dirname, '..', 'src', 'server.js')], {
    env: {
      ...process.env,
      NODE_ENV: 'test',
      JWT_SECRET: 'test-jwt-secret',
      INTERNAL_API_KEY: 'test-internal-key',
      ...overrides,
    },
    encoding: 'utf8',
  });
}

assert.throws(
  () => loadSecurityConfig({ NODE_ENV: 'test', JWT_SECRET: '', INTERNAL_API_KEY: 'internal' }),
  /JWT_SECRET is required/
);
assert.throws(
  () => loadSecurityConfig({ NODE_ENV: 'test', JWT_SECRET: 'jwt', INTERNAL_API_KEY: '' }),
  /INTERNAL_API_KEY is required/
);
assert.throws(
  () => loadSecurityConfig({
    NODE_ENV: 'test',
    JWT_SECRET: 'your_jwt_secret_here',
    INTERNAL_API_KEY: 'internal',
  }),
  /placeholder/
);
assert.throws(
  () => loadSecurityConfig({
    NODE_ENV: 'test',
    JWT_SECRET: 'capstone_jwt_secret_key',
    INTERNAL_API_KEY: 'internal',
  }),
  /placeholder/
);
assert.throws(
  () => loadSecurityConfig({
    NODE_ENV: 'production',
    JWT_SECRET: 'short',
    INTERNAL_API_KEY: 'internal',
    CORS_ALLOWED_ORIGINS: 'https://frontend.example',
  }),
  /at least 32 characters/
);
assert.throws(
  () => loadSecurityConfig({
    NODE_ENV: 'test',
    JWT_SECRET: 'jwt',
    INTERNAL_API_KEY: 'internal',
    CORS_ALLOWED_ORIGINS: '*',
  }),
  /wildcard/
);
assert.throws(
  () => loadSecurityConfig({
    NODE_ENV: 'production',
    JWT_SECRET: 'j'.repeat(32),
    INTERNAL_API_KEY: 'i'.repeat(32),
  }),
  /CORS_ALLOWED_ORIGINS is required/
);
assert.throws(
  () => loadSecurityConfig({
    NODE_ENV: 'test',
    JWT_SECRET: 'jwt',
    INTERNAL_API_KEY: 'internal',
    TRUST_PROXY_HOPS: '-1',
  }),
  /non-negative integer/
);
const validConfig = loadSecurityConfig({
    NODE_ENV: 'test',
    JWT_SECRET: 'jwt',
    INTERNAL_API_KEY: 'internal',
    CORS_ALLOWED_ORIGINS: 'http://localhost:3000,http://localhost:3000',
  });
assert.deepEqual(validConfig.corsAllowedOrigins, ['http://localhost:3000']);
assert.equal(validConfig.trustProxyHops, 0);
assert.equal(safeSecretEqual('', ''), false);
assert.notEqual(startupWith({ JWT_SECRET: '' }).status, 0);
assert.notEqual(startupWith({ INTERNAL_API_KEY: '' }).status, 0);

const jwtSecret = 'test-jwt-secret';
const auth = createAuthMiddleware(() => ({ jwtSecret }));
const validToken = jwt.sign({ user_id: 'user-1' }, jwtSecret, { algorithm: 'HS256' });
assert.deepEqual(invoke(auth, { authorization: `Bearer ${validToken}` }), {
  nextCalled: true,
  statusCode: null,
});
assert.equal(
  invoke(auth, { authorization: `Bearer ${jwt.sign({ user_id: 'user-1' }, 'wrong')}` }).statusCode,
  401
);
assert.equal(
  invoke(auth, {
    authorization: `Bearer ${jwt.sign({ user_id: 'user-1' }, jwtSecret, { algorithm: 'HS384' })}`,
  }).statusCode,
  401
);
assert.equal(
  invoke(auth, {
    authorization: `Bearer ${jwt.sign({ user_id: 'user-1' }, jwtSecret, { expiresIn: -1 })}`,
  }).statusCode,
  401
);
assert.equal(invoke(auth, {}).statusCode, 401);

const unavailableJwt = createAuthMiddleware(() => {
  throw new SecurityConfigError('JWT_SECRET is required.');
});
assert.equal(invoke(unavailableJwt, { authorization: `Bearer ${validToken}` }).statusCode, 503);

const internal = createInternalAuth(() => ({ internalApiKey: 'test-internal-key' }));
assert.deepEqual(invoke(internal, { 'x-api-key': 'test-internal-key' }), {
  nextCalled: true,
  statusCode: null,
});
assert.equal(invoke(internal, { 'x-api-key': 'wrong' }).statusCode, 403);
assert.equal(invoke(internal, {}).statusCode, 403);

const unavailableInternal = createInternalAuth(() => {
  throw new SecurityConfigError('INTERNAL_API_KEY is required.');
});
assert.equal(invoke(unavailableInternal, { 'x-api-key': 'test-internal-key' }).statusCode, 503);

console.log('[backend-auth] 23 checks passed');
