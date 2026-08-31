const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const USER_A = '11111111-1111-4111-8111-111111111111';
const USER_B = '22222222-2222-4222-8222-222222222222';
const JWT_SECRET = 'tenant-regression-jwt-secret';
const INTERNAL_API_KEY = 'tenant-regression-internal-secret';

process.env.NODE_ENV = 'test';
process.env.LOG_LEVEL = 'error';
process.env.SUPABASE_URL = 'http://supabase.invalid';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'tenant-regression-service-role';
process.env.JWT_SECRET = JWT_SECRET;
process.env.INTERNAL_API_KEY = INTERNAL_API_KEY;
process.env.CORS_ALLOWED_ORIGINS = 'http://localhost:3000';
process.env.FASTAPI_URL = 'http://ai.invalid';

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

class FakeQuery {
  constructor(db, tableName) {
    this.db = db;
    this.tableName = tableName;
    this.filters = [];
    this.orders = [];
    this.limitCount = null;
    this.operation = 'select';
    this.payload = null;
    this.conflictColumns = [];
  }

  select() {
    return this;
  }

  eq(column, value) {
    this.filters.push((row) => row[column] === value);
    return this;
  }

  in(column, values) {
    const allowed = new Set(values);
    this.filters.push((row) => allowed.has(row[column]));
    return this;
  }

  gte(column, value) {
    this.filters.push((row) => row[column] >= value);
    return this;
  }

  lte(column, value) {
    this.filters.push((row) => row[column] <= value);
    return this;
  }

  order(column, { ascending = true } = {}) {
    this.orders.push({ column, ascending });
    return this;
  }

  limit(count) {
    this.limitCount = count;
    return this;
  }

  insert(payload) {
    this.operation = 'insert';
    this.payload = payload;
    return this;
  }

  upsert(payload, { onConflict = '' } = {}) {
    this.operation = 'upsert';
    this.payload = payload;
    this.conflictColumns = onConflict.split(',').map((item) => item.trim()).filter(Boolean);
    return this;
  }

  update(payload) {
    this.operation = 'update';
    this.payload = payload;
    return this;
  }

  delete() {
    this.operation = 'delete';
    return this;
  }

  maybeSingle() {
    const rows = this.run();
    if (rows.length === 0) return Promise.resolve({ data: null, error: null });
    if (rows.length > 1) return Promise.resolve({ data: null, error: new Error('Multiple rows') });
    return Promise.resolve({ data: rows[0], error: null });
  }

  single() {
    const rows = this.run();
    if (rows.length !== 1) {
      return Promise.resolve({ data: null, error: new Error('Expected one row') });
    }
    return Promise.resolve({ data: rows[0], error: null });
  }

  then(resolve, reject) {
    try {
      resolve({ data: this.run(), error: null });
    } catch (error) {
      if (reject) reject(error);
    }
  }

  run() {
    if (this.operation === 'insert') return this.insertRows(this.payload);
    if (this.operation === 'upsert') return this.upsertRows(this.payload);
    if (this.operation === 'update') return this.updateRows();
    if (this.operation === 'delete') return this.deleteRows();
    return this.selectRows();
  }

  matches(row) {
    return this.filters.every((filter) => filter(row));
  }

  selectRows() {
    const rows = (this.db.tables[this.tableName] || []).filter((row) => this.matches(row));
    rows.sort((left, right) => {
      for (const { column, ascending } of this.orders) {
        if (left[column] === right[column]) continue;
        const compared = left[column] > right[column] ? 1 : -1;
        return ascending ? compared : -compared;
      }
      return 0;
    });
    const limited = this.limitCount === null ? rows : rows.slice(0, this.limitCount);
    return clone(limited);
  }

  insertRows(payload) {
    const rows = (Array.isArray(payload) ? payload : [payload]).map((row) => this.withDefaults(row));
    this.db.tables[this.tableName] = [...(this.db.tables[this.tableName] || []), ...rows];
    return clone(rows);
  }

  upsertRows(payload) {
    const rows = Array.isArray(payload) ? payload : [payload];
    const table = this.db.tables[this.tableName] || [];
    const saved = [];

    for (const rawRow of rows) {
      const row = this.withDefaults(rawRow);
      const index = this.conflictColumns.length === 0
        ? -1
        : table.findIndex((candidate) =>
          this.conflictColumns.every((column) => candidate[column] === row[column]));
      if (index >= 0) {
        table[index] = { ...table[index], ...row };
        saved.push(table[index]);
      } else {
        table.push(row);
        saved.push(row);
      }
    }

    this.db.tables[this.tableName] = table;
    return clone(saved);
  }

  updateRows() {
    const updated = [];
    this.db.tables[this.tableName] = (this.db.tables[this.tableName] || []).map((row) => {
      if (!this.matches(row)) return row;
      const next = { ...row, ...clone(this.payload) };
      updated.push(next);
      return next;
    });
    return clone(updated);
  }

  deleteRows() {
    const kept = [];
    const deleted = [];
    for (const row of this.db.tables[this.tableName] || []) {
      (this.matches(row) ? deleted : kept).push(row);
    }
    this.db.tables[this.tableName] = kept;
    return clone(deleted);
  }

  withDefaults(rawRow) {
    const row = clone(rawRow);
    if (this.tableName === 'chat_feedback') {
      row.id ||= `feedback-${this.db.nextId++}`;
      row.created_at ||= '2026-08-31T00:00:00.000Z';
    }
    return row;
  }
}

class FakeSupabase {
  reset(tables) {
    this.tables = clone(tables);
    this.nextId = 1;
  }

  from(tableName) {
    return new FakeQuery(this, tableName);
  }
}

function seedTables() {
  return {
    users: [
      { user_id: USER_A, login_id: 'user-a', nickname: 'Alpha', email: 'a@example.invalid' },
      { user_id: USER_B, login_id: 'user-b', nickname: 'Beta', email: 'b@example.invalid' },
    ],
    user_health_profiles: [
      {
        user_id: USER_A,
        gender: 'female',
        age: 31,
        height: 165,
        weight: 60,
        bmi: 22,
        goal: 'fitness',
        activity_level: 'active',
        mbti: 'INTJ',
        allergies: '["none"]',
        injury_history: '[]',
        medical_history: '["none"]',
        selected_ai_persona: 'default',
      },
      {
        user_id: USER_B,
        gender: 'male',
        age: 35,
        height: 180,
        weight: 80,
        bmi: 24.7,
        goal: 'strength',
        activity_level: 'active',
        mbti: 'ENTJ',
        allergies: '["peanut"]',
        injury_history: '[]',
        medical_history: '["hypertension"]',
        selected_ai_persona: 'strict_trainer',
      },
    ],
    user_exercise_plans: [
      {
        exercise_id: 100,
        user_id: USER_A,
        exercise_type: 'A workout',
        target_date: '2026-08-31',
        total_calories: 100,
        status: 0,
        created_at: '2026-08-31T00:00:00.000Z',
      },
      {
        exercise_id: 200,
        user_id: USER_B,
        exercise_type: 'B workout',
        target_date: '2026-08-31',
        total_calories: 200,
        status: 0,
        created_at: '2026-08-31T00:00:00.000Z',
      },
    ],
    exercise_items: [
      { item_id: 101, exercise_id: 100, exercise_name: 'A squat', calories: 100, is_completed: false },
      { item_id: 201, exercise_id: 200, exercise_name: 'B press', calories: 200, is_completed: false },
    ],
    user_meal_plans: [
      {
        meal_id: 300,
        user_id: USER_A,
        meal_type: 'Lunch',
        food_name: 'A meal',
        target_date: '2026-08-31',
        calories: 400,
        is_completed: false,
        created_at: '2026-08-31T00:00:00.000Z',
      },
      {
        meal_id: 301,
        user_id: USER_B,
        meal_type: 'Lunch',
        food_name: 'B meal',
        target_date: '2026-08-31',
        calories: 500,
        is_completed: false,
        created_at: '2026-08-31T00:00:00.000Z',
      },
    ],
    chat_threads: [
      {
        user_id: USER_A,
        session_id: 'session-a',
        title: 'A thread',
        message_count: 2,
        created_at: '2026-08-31T00:00:00.000Z',
        updated_at: '2026-08-31T00:00:00.000Z',
        last_message_at: '2026-08-31T00:00:00.000Z',
      },
      {
        user_id: USER_B,
        session_id: 'shared-session',
        title: 'B private thread',
        message_count: 2,
        created_at: '2026-08-31T00:00:00.000Z',
        updated_at: '2026-08-31T00:00:00.000Z',
        last_message_at: '2026-08-31T00:00:00.000Z',
      },
    ],
    chat_messages: [
      {
        id: 'a-user-row',
        user_id: USER_A,
        session_id: 'session-a',
        role: 'user',
        content: 'A stored question',
        client_message_id: 'a-user-message',
        intent: null,
        created_at: '2026-08-31T00:00:00.000Z',
        role_order: 0,
      },
      {
        id: 'a-assistant-row',
        user_id: USER_A,
        session_id: 'session-a',
        role: 'assistant',
        content: 'A stored answer',
        client_message_id: 'a-assistant-message',
        intent: 'diet',
        created_at: '2026-08-31T00:00:00.000Z',
        role_order: 1,
      },
      {
        id: 'a-legacy-user-row',
        user_id: USER_A,
        session_id: 'session-a',
        role: 'user',
        content: 'A legacy stored question',
        client_message_id: null,
        intent: null,
        created_at: '2026-08-31T00:01:00.000Z',
        role_order: 0,
      },
      {
        id: 'a-legacy-assistant-row',
        user_id: USER_A,
        session_id: 'session-a',
        role: 'assistant',
        content: 'A legacy stored answer',
        client_message_id: null,
        intent: 'workout',
        created_at: '2026-08-31T00:01:00.000Z',
        role_order: 1,
      },
      {
        id: 'b-user-row',
        user_id: USER_B,
        session_id: 'shared-session',
        role: 'user',
        content: 'B private question',
        client_message_id: 'b-user-message',
        intent: null,
        created_at: '2026-08-31T00:00:00.000Z',
        role_order: 0,
      },
      {
        id: 'b-assistant-row',
        user_id: USER_B,
        session_id: 'shared-session',
        role: 'assistant',
        content: 'B private answer',
        client_message_id: 'b-assistant-message',
        intent: 'workout',
        created_at: '2026-08-31T00:00:00.000Z',
        role_order: 1,
      },
    ],
    chat_feedback: [
      {
        id: 'b-feedback',
        user_id: USER_B,
        session_id: 'shared-session',
        client_message_id: 'b-assistant-message',
        user_message: 'B private question',
        assistant_message: 'B private answer',
        rating: 'up',
      },
    ],
    ai_was_idempotency_keys: [],
  };
}

const fakeSupabase = new FakeSupabase();
fakeSupabase.reset(seedTables());

const dbModulePath = require.resolve('../src/config/db');
require.cache[dbModulePath] = {
  id: dbModulePath,
  filename: dbModulePath,
  loaded: true,
  exports: fakeSupabase,
};

const outboundRequests = [];
const axiosModule = require('axios');
const mockPost = async (url, payload) => {
  outboundRequests.push({ url, payload: clone(payload) });
  if (url.includes('/home/recommendations')) {
    return { data: { received_user_id: payload.user_id } };
  }
  if (url.includes('/internal/events/profile-updated')) {
    return { data: { status: 'ok' } };
  }
  throw new Error(`Unexpected outbound request: ${url}`);
};
axiosModule.post = mockPost;
if (axiosModule.default) axiosModule.default.post = mockPost;

const app = express();
app.use(express.json());
app.use('/api/v1/auth', require('../src/routes/auth'));
app.use('/api/v1/users', require('../src/routes/users'));
app.use('/api/v1/home', require('../src/routes/home'));
app.use('/api/v1/chat', require('../src/routes/chat'));
app.use('/api', require('../src/routes/internal'));

const userAToken = jwt.sign(
  { user_id: USER_A, login_id: 'user-a' },
  JWT_SECRET,
  { algorithm: 'HS256', expiresIn: '1h' }
);

function findRow(tableName, predicate) {
  return fakeSupabase.tables[tableName].find(predicate);
}

async function parseResponse(response) {
  const text = await response.text();
  return {
    status: response.status,
    body: text ? JSON.parse(text) : null,
  };
}

async function request(baseUrl, path, { method = 'GET', body, internal = false } = {}) {
  const headers = internal
    ? { 'x-api-key': INTERNAL_API_KEY }
    : { Authorization: `Bearer ${userAToken}` };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  return parseResponse(await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  }));
}

const scenarios = [];
function scenario(name, run) {
  scenarios.push({ name, run });
}

scenario('JWT identity controls auth and profile reads/writes', async (baseUrl) => {
  let response = await request(baseUrl, `/api/v1/auth/me?user_id=${USER_B}`);
  assert.equal(response.status, 200);
  assert.equal(response.body.user_id, USER_A);

  response = await request(baseUrl, `/api/v1/users/profile?user_id=${USER_B}`);
  assert.equal(response.status, 200);
  assert.equal(response.body.user_id, USER_A);

  response = await request(baseUrl, '/api/v1/users/profile', {
    method: 'POST',
    body: {
      user_id: USER_B,
      nickname: 'Alpha updated',
      weight: 62,
    },
  });
  assert.equal(response.status, 200);
  assert.equal(findRow('user_health_profiles', (row) => row.user_id === USER_A).weight, 62);
  assert.equal(findRow('user_health_profiles', (row) => row.user_id === USER_B).weight, 80);
  assert.equal(findRow('users', (row) => row.user_id === USER_A).nickname, 'Alpha updated');
  assert.equal(findRow('users', (row) => row.user_id === USER_B).nickname, 'Beta');
});

scenario('persona settings reject a different user path', async (baseUrl) => {
  const response = await request(baseUrl, `/api/v1/users/${USER_B}/settings/persona`, {
    method: 'PATCH',
    body: { selected_ai_persona: 'default' },
  });
  assert.equal(response.status, 403);
  assert.equal(
    findRow('user_health_profiles', (row) => row.user_id === USER_B).selected_ai_persona,
    'strict_trainer'
  );
});

scenario('calendar and home recommendation identity come from JWT', async (baseUrl) => {
  let response = await request(
    baseUrl,
    `/api/v1/users/calendar?start_date=2026-08-31&end_date=2026-08-31&user_id=${USER_B}`
  );
  assert.equal(response.status, 200);
  assert.deepEqual(response.body['2026-08-31'].exercises.map((item) => item.exercise_id), [100]);
  assert.deepEqual(response.body['2026-08-31'].meals.map((item) => item.meal_id), [300]);

  response = await request(baseUrl, '/api/v1/home/recommendations', {
    method: 'POST',
    body: { user_id: USER_B, type: 'all' },
  });
  assert.equal(response.status, 200);
  assert.equal(response.body.received_user_id, USER_A);
  const homeRequest = outboundRequests.find((item) => item.url.includes('/home/recommendations'));
  assert.equal(homeRequest.payload.user_id, USER_A);
});

scenario('workout and meal child mutations require parent ownership', async (baseUrl) => {
  let response = await request(baseUrl, '/api/v1/users/exercises/items/201', {
    method: 'PUT',
    body: { is_completed: true },
  });
  assert.equal(response.status, 404);

  response = await request(baseUrl, '/api/v1/users/plans/check', {
    method: 'PUT',
    body: { item_id: 'exercise-item-201' },
  });
  assert.equal(response.status, 404);

  response = await request(baseUrl, '/api/v1/users/meals/301', {
    method: 'PUT',
    body: { is_completed: true },
  });
  assert.equal(response.status, 404);
  assert.equal(findRow('exercise_items', (row) => row.item_id === 201).is_completed, false);
  assert.equal(findRow('user_meal_plans', (row) => row.meal_id === 301).is_completed, false);
});

scenario('single and date plan deletion preserve another tenant', async (baseUrl) => {
  let response = await request(baseUrl, '/api/v1/users/plans/exercise-200', { method: 'DELETE' });
  assert.equal(response.status, 404);

  response = await request(baseUrl, '/api/v1/users/plans', {
    method: 'DELETE',
    body: {
      plan_type: 'all',
      target_dates: ['2026-08-31'],
    },
  });
  assert.equal(response.status, 200);
  assert.equal(findRow('user_exercise_plans', (row) => row.exercise_id === 100), undefined);
  assert.equal(findRow('user_meal_plans', (row) => row.meal_id === 300), undefined);
  assert.ok(findRow('user_exercise_plans', (row) => row.exercise_id === 200));
  assert.ok(findRow('exercise_items', (row) => row.item_id === 201));
  assert.ok(findRow('user_meal_plans', (row) => row.meal_id === 301));
});

scenario('chat thread read and delete are scoped by user and session', async (baseUrl) => {
  let response = await request(baseUrl, '/api/v1/chat/threads');
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.threads.map((thread) => thread.session_id), ['session-a']);

  response = await request(baseUrl, '/api/v1/chat/threads/shared-session');
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.messages, []);

  response = await request(baseUrl, '/api/v1/chat/threads/shared-session', { method: 'DELETE' });
  assert.equal(response.status, 404);
  assert.ok(findRow('chat_threads', (row) => row.user_id === USER_B));
  assert.ok(findRow('chat_messages', (row) => row.id === 'b-assistant-row'));
  assert.ok(findRow('chat_feedback', (row) => row.id === 'b-feedback'));
});

scenario('feedback requires an owned persisted assistant message', async (baseUrl) => {
  let response = await request(baseUrl, '/api/v1/chat/feedback', {
    method: 'POST',
    body: {
      client_message_id: 'b-assistant-message',
      session_id: 'shared-session',
      user_message: 'spoofed B question',
      assistant_message: 'spoofed B answer',
      rating: 'up',
    },
  });
  assert.equal(response.status, 404);
  assert.equal(fakeSupabase.tables.chat_feedback.length, 1);

  response = await request(baseUrl, '/api/v1/chat/feedback', {
    method: 'POST',
    body: {
      client_message_id: 'a-assistant-message',
      session_id: 'session-a',
      user_message: 'spoofed A question',
      assistant_message: 'spoofed A answer',
      intent: 'spoofed-intent',
      rating: 'up',
    },
  });
  assert.equal(response.status, 200);
  const saved = findRow(
    'chat_feedback',
    (row) => row.user_id === USER_A && row.client_message_id === 'a-assistant-message'
  );
  assert.equal(saved.user_message, 'A stored question');
  assert.equal(saved.assistant_message, 'A stored answer');
  assert.equal(saved.intent, 'diet');

  response = await request(baseUrl, '/api/v1/chat/feedback', {
    method: 'POST',
    body: {
      client_message_id: 'a-legacy-assistant-row',
      session_id: 'session-a',
      rating: 'up',
    },
  });
  assert.equal(response.status, 200);
  const legacySaved = findRow(
    'chat_feedback',
    (row) => row.user_id === USER_A && row.client_message_id === 'a-legacy-assistant-row'
  );
  assert.equal(legacySaved.user_message, 'A legacy stored question');
  assert.equal(legacySaved.assistant_message, 'A legacy stored answer');
  assert.equal(legacySaved.intent, 'workout');
});

scenario('internal API reads and child checks honor the path user', async (baseUrl) => {
  let response = await request(baseUrl, `/api/user/profile/${USER_A}`, { internal: true });
  assert.equal(response.status, 200);
  assert.equal(response.body.user_id, USER_A);

  response = await request(baseUrl, `/api/plan/today/${USER_A}`, { internal: true });
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.map((item) => item.id).sort(), ['exercise-item-101', 'meal-300']);

  response = await request(baseUrl, `/api/plan/check/${USER_A}`, {
    method: 'PUT',
    internal: true,
    body: { item_id: 'exercise-item-201' },
  });
  assert.equal(response.status, 404);
  assert.equal(findRow('exercise_items', (row) => row.item_id === 201).is_completed, false);
});

async function main() {
  const server = http.createServer(app);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  let passed = 0;

  try {
    for (const { name, run } of scenarios) {
      fakeSupabase.reset(seedTables());
      outboundRequests.length = 0;
      await run(baseUrl);
      passed += 1;
      console.log(`[tenant-boundaries] ok ${passed} - ${name}`);
    }
    console.log(`[tenant-boundaries] ${passed}/${scenarios.length} scenarios passed`);
  } finally {
    await new Promise((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())));
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
