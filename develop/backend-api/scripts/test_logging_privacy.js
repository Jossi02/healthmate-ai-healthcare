const assert = require('assert');
const http = require('http');
const { Writable } = require('stream');

process.env.NODE_ENV = 'test';
process.env.LOG_LEVEL = 'http';
process.env.JWT_SECRET = 'logging-privacy-test-jwt-secret';
process.env.INTERNAL_API_KEY = 'logging-privacy-test-internal-key';
process.env.SUPABASE_URL = 'https://example.supabase.test';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'service-role-secret';

const logger = require('../src/utils/logger');
const { transports } = require('winston');
const chatController = require('../src/controllers/chatController');
const homeController = require('../src/controllers/homeController');
const { errorHandler } = require('../src/middleware/errorHandler');
const axios = require('axios');

const chunks = [];
const fileTransports = logger.transports.filter((transport) => transport.filename);
logger.clear();
logger.add(new transports.Stream({
  stream: new Writable({
    write(chunk, _encoding, callback) {
      chunks.push(String(chunk));
      callback();
    },
  }),
}));

function assertNoSecrets(value) {
  const text = String(value);
  for (const secret of [
    'bearer-secret',
    'cookie-secret',
    'api-secret',
    'access-secret',
    'refresh-secret',
    'internal-secret',
    'hash-secret',
    'jwt-secret',
    'service-role-secret',
    'password-secret',
    'query-secret',
    'upstream-secret',
    'private-health-payload',
  ]) {
    assert.ok(!text.includes(secret), `secret leaked: ${secret}`);
  }
}

async function request(server, path, headers = {}) {
  return new Promise((resolve, reject) => {
    const request = http.get({ port: server.address().port, path, headers }, (response) => {
      response.resume();
      response.on('end', () => resolve(response));
    });
    request.on('error', reject);
  });
}

function mockResponse(resolve) {
  return {
    statusCode: 200,
    body: null,
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(body) {
      this.body = body;
      if (resolve) resolve(this);
      return this;
    },
  };
}

async function invoke(controller, req) {
  return new Promise((resolve, reject) => {
    const response = mockResponse(resolve);
    Promise.resolve(controller(req, response)).catch(reject);
  });
}

async function main() {
  const nested = {
    headers: {
      authorization: 'Bearer bearer-secret',
      cookie: 'sid=cookie-secret',
      'x-api-key': 'api-secret',
    },
    accessToken: 'access-secret',
    refresh_token: 'refresh-secret',
    internalApiKey: 'internal-secret',
    password_hash: 'hash-secret',
    jwt: 'jwt-secret',
    service_role_key: 'service-role-secret',
    nested: [{ password: 'password-secret' }],
  };
  nested.self = nested;

  assert.equal(logger.redactSensitive(nested).headers.authorization, '[REDACTED]');
  for (const key of ['accessToken', 'refresh_token', 'internalApiKey', 'password_hash', 'jwt', 'service_role_key']) {
    assert.equal(logger.redactSensitive(nested)[key], '[REDACTED]');
  }
  assert.equal(logger.redactSensitive(nested).nested[0].password, '[REDACTED]');
  assert.equal(logger.redactSensitive(nested).self, '[Circular]');

  const error = new Error('Authorization: Bearer bearer-secret password=password-secret');
  error.config = { headers: { cookie: 'cookie-secret', 'x-api-key': 'api-secret' } };
  error.response = { status: 503, data: { diagnosis: 'private-health-payload' } };
  error.service_role_key = 'service-role-secret';
  const serializedError = logger.serializeLogValue(error);
  assertNoSecrets(serializedError);
  assert.ok(!serializedError.includes('config'));
  assert.ok(!serializedError.includes('response'));
  assertNoSecrets(logger.serializeLogValue([
    'Authorization: Bearer bearer-secret',
    'token=query-secret',
    'accessToken=access-secret',
    'refresh_token=refresh-secret',
    'internalApiKey=internal-secret',
    'passwordHash=hash-secret',
    'jwt=jwt-secret',
    'serviceRoleKey=service-role-secret',
  ].join(' ')));
  assertNoSecrets(logger.serializeLogValue(new Error('private-health-payload')));

  assert.equal(fileTransports.length, 2);
  for (const transport of fileTransports) {
    assert.equal(transport.maxsize, 5 * 1024 * 1024);
    assert.equal(transport.maxFiles, 5);
    assert.equal(transport.tailable, true);
  }

  logger.info(nested);
  logger.error('credential-bearing error', error);
  logger.error('health-bearing error', new Error('private-health-payload'));
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(chunks.join('').includes('"upstreamStatus":503'));

  const app = require('../src/app');
  const server = app.listen(0);
  try {
    await request(server, '/api/health?token=query-secret', {
      Authorization: 'Bearer bearer-secret',
      Cookie: 'sid=cookie-secret',
    });
    await new Promise((resolve) => setImmediate(resolve));
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }

  const accessLog = chunks.find((chunk) => chunk.includes('GET /api/health'));
  assert.ok(accessLog, 'Morgan access log missing');
  assert.match(accessLog, /GET \/api\/health \d+ [\d.]+ ms/);
  assert.ok(!accessLog.includes('?'), 'Morgan access log contains query string');
  assert.ok(!accessLog.includes('Authorization'), 'Morgan access log contains auth data');
  assertNoSecrets(chunks.join(''));

  const originalPost = axios.post;
  try {
    axios.post = async () => {
      const upstreamError = new Error('upstream password=password-secret');
      upstreamError.response = {
        status: 503,
        data: {
          message: 'provider query-secret',
          password: 'password-secret',
          diagnosis: 'private-health-payload',
        },
      };
      throw upstreamError;
    };

    const requestContext = { user: { user_id: 'user-1' }, body: { user_message: 'hello' } };
    const upstreamLogStart = chunks.length;
    const chatResponse = await invoke(chatController.sendMessage, requestContext);
    assert.equal(chatResponse.statusCode, 502);
    assert.deepEqual(chatResponse.body, { error: 'Failed to process chat request.' });

    const homeResponse = await invoke(homeController.getWorkoutRecommendations, requestContext);
    assert.equal(homeResponse.statusCode, 502);
    assert.deepEqual(homeResponse.body, { error: 'Failed to load home recommendations.' });
    await new Promise((resolve) => setImmediate(resolve));
    const upstreamLogs = chunks.slice(upstreamLogStart).join('');
    assertNoSecrets(upstreamLogs);
    assert.ok(!upstreamLogs.includes('private-health-payload'));
  } finally {
    axios.post = originalPost;
  }

  let errorBody;
  let statusCode;
  errorHandler(
    new Error('database password=password-secret'),
    { method: 'GET', path: '/private' },
    {
      status(code) {
        statusCode = code;
        return this;
      },
      json(body) {
        errorBody = body;
        return this;
      },
    }
  );
  assert.equal(statusCode, 500);
  assert.deepEqual(errorBody, {
    success: false,
    statusCode: 500,
    message: '서버 내부 오류가 발생했습니다.',
  });

  const badRequest = new Error('private-health-payload token=query-secret');
  badRequest.statusCode = 400;
  const badRequestResponse = mockResponse();
  errorHandler(badRequest, { method: 'POST', path: '/private' }, badRequestResponse);
  assert.equal(badRequestResponse.statusCode, 400);
  assert.deepEqual(badRequestResponse.body, {
    success: false,
    statusCode: 400,
    message: 'Request failed.',
  });
  await new Promise((resolve) => setImmediate(resolve));
  assertNoSecrets(chunks.join(''));

  logger.close();
  console.log('[logging-privacy] passed');
}

main().catch((error) => {
  logger.close();
  console.error(error);
  process.exitCode = 1;
});
