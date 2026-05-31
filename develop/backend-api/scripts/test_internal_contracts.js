const assert = require('assert/strict');
const bcrypt = require('bcrypt');

const {
  buildProfileRowForUpsert,
  DEFAULT_SELECTED_AI_PERSONA,
  ensureUserHealthProfile,
} = require('../src/services/profileService');
const {
  isValidBcryptHash,
  verifyPassword,
} = require('../src/utils/passwordHash');
const { isValidUuid } = require('../src/utils/ids');
const {
  loadExercisePlansWithItems,
} = require('../src/services/exercisePlanReadService');
const {
  listChatThreads,
  loadChatMessages,
  persistChatTurn,
} = require('../src/services/chatThreadService');

const planMutationService = require('../src/services/planMutationService');

class FakeQuery {
  constructor(tableName, tables, state) {
    this.tableName = tableName;
    this.tables = tables;
    this.state = state;
    this.filters = [];
    this.orders = [];
    this.mode = 'select';
    this.payload = null;
  }

  select() {
    return this;
  }

  eq(column, value) {
    this.filters.push((row) => row[column] === value);
    return this;
  }

  in(column, values) {
    const valueSet = new Set(values);
    this.filters.push((row) => valueSet.has(row[column]));
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

  order(column, { ascending } = { ascending: true }) {
    this.orders.push({ column, ascending: ascending !== false });
    return this;
  }

  insert(payload) {
    this.mode = 'insert';
    this.payload = payload;
    return this;
  }

  upsert(payload) {
    this.mode = 'upsert';
    this.payload = payload;
    return this;
  }

  delete() {
    this.mode = 'delete';
    return this;
  }

  maybeSingle() {
    const rows = this._run();
    if (rows.length === 0) {
      return Promise.resolve({ data: null, error: null });
    }
    if (rows.length > 1) {
      return Promise.resolve({ data: null, error: new Error('Multiple rows') });
    }
    return Promise.resolve({ data: rows[0], error: null });
  }

  single() {
    const rows = this._run();
    if (rows.length !== 1) {
      return Promise.resolve({ data: null, error: new Error('Expected single row') });
    }
    return Promise.resolve({ data: rows[0], error: null });
  }

  then(resolve, reject) {
    try {
      resolve({ data: this._run(), error: null });
    } catch (error) {
      if (reject) reject(error);
    }
  }

  _run() {
    this._maybeThrow('before');

    if (this.mode === 'insert') {
      return this._insertRows(this.payload);
    }
    if (this.mode === 'upsert') {
      return this._upsertRows(this.payload);
    }
    if (this.mode === 'delete') {
      return this._deleteRows();
    }
    return this._selectRows();
  }

  _maybeThrow(phase) {
    const behavior = this.state.behaviors?.[this.tableName];
    if (!behavior) return;

    const key = `${phase}_${this.mode}`;
    if (!behavior[key]) return;

    const error = behavior[key];
    delete behavior[key];
    throw error;
  }

  _selectRows() {
    let rows = [...(this.tables[this.tableName] || [])];
    for (const filter of this.filters) {
      rows = rows.filter(filter);
    }
    for (const { column, ascending } of this.orders) {
      rows = rows.sort((left, right) => {
        if (left[column] === right[column]) return 0;
        const compare = left[column] > right[column] ? 1 : -1;
        return ascending ? compare : compare * -1;
      });
    }
    return rows.map((row) => ({ ...row }));
  }

  _insertRows(payload) {
    const rows = Array.isArray(payload) ? payload : [payload];
    const inserted = rows.map((row) => this._applyGeneratedKeys({ ...row }));
    this.tables[this.tableName] = [...(this.tables[this.tableName] || []), ...inserted];
    return inserted.map((row) => ({ ...row }));
  }

  _upsertRows(payload) {
    const rows = Array.isArray(payload) ? payload : [payload];
    const table = [...(this.tables[this.tableName] || [])];
    const upserted = [];

    for (const row of rows) {
      const nextRow = { ...row };
      const existingIndex = table.findIndex((item) => item.user_id === nextRow.user_id);
      if (existingIndex >= 0) {
        table[existingIndex] = { ...table[existingIndex], ...nextRow };
        upserted.push({ ...table[existingIndex] });
      } else {
        table.push(nextRow);
        upserted.push({ ...nextRow });
      }
    }

    this.tables[this.tableName] = table;
    return upserted;
  }

  _deleteRows() {
    const table = [...(this.tables[this.tableName] || [])];
    const kept = [];
    const deleted = [];

    for (const row of table) {
      const matches = this.filters.every((filter) => filter(row));
      if (matches) {
        deleted.push({ ...row });
      } else {
        kept.push(row);
      }
    }

    this.tables[this.tableName] = kept;
    return deleted;
  }

  _applyGeneratedKeys(row) {
    const generated = { ...row };
    const nextId = () => {
      const current = this.state.counters[this.tableName] || 1;
      this.state.counters[this.tableName] = current + 1;
      return current;
    };

    if (this.tableName === 'user_exercise_plans' && generated.exercise_id === undefined) {
      generated.exercise_id = nextId();
    }

    if (this.tableName === 'exercise_items' && generated.item_id === undefined) {
      generated.item_id = nextId();
    }

    if (this.tableName === 'user_meal_plans' && generated.meal_id === undefined) {
      generated.meal_id = nextId();
    }

    return generated;
  }
}

class FakeSupabase {
  constructor(tables, behaviors = {}) {
    this.tables = Object.fromEntries(
      Object.entries(tables).map(([name, rows]) => [name, rows.map((row) => ({ ...row }))])
    );
    this.state = {
      behaviors: Object.fromEntries(
        Object.entries(behaviors).map(([name, value]) => [name, { ...value }])
      ),
      counters: {},
    };

    for (const [tableName, rows] of Object.entries(this.tables)) {
      const keyName =
        tableName === 'user_exercise_plans'
          ? 'exercise_id'
          : tableName === 'exercise_items'
            ? 'item_id'
            : tableName === 'user_meal_plans'
              ? 'meal_id'
              : null;

      if (!keyName) continue;

      const nextValue =
        rows.reduce((maxValue, row) => Math.max(maxValue, Number(row[keyName] || 0)), 0) + 1;
      this.state.counters[tableName] = nextValue;
    }
  }

  from(tableName) {
    return new FakeQuery(tableName, this.tables, this.state);
  }
}

async function testExistingProfileNormalization() {
  const supabase = new FakeSupabase({
    users: [{ user_id: 'user-1', nickname: 'Coco' }],
    user_health_profiles: [
      {
        user_id: 'user-1',
        selected_ai_persona: null,
        allergies: '["milk"]',
        injury_history: '[]',
        medical_history: '["knee"]',
      },
    ],
  });

  const profile = await ensureUserHealthProfile(supabase, 'user-1');
  assert.equal(profile.user_id, 'user-1');
  assert.equal(profile.nickname, 'Coco');
  assert.equal(profile.selected_ai_persona, DEFAULT_SELECTED_AI_PERSONA);
  assert.deepEqual(profile.allergies, ['milk']);
  assert.deepEqual(profile.medical_history, ['knee']);
}

async function testMissingProfileBootstrap() {
  const supabase = new FakeSupabase({
    users: [{ user_id: 'user-2', nickname: 'Nova' }],
    user_health_profiles: [],
  });

  const profile = await ensureUserHealthProfile(supabase, 'user-2');
  assert.equal(profile.user_id, 'user-2');
  assert.equal(profile.nickname, 'Nova');
  assert.equal(profile.selected_ai_persona, DEFAULT_SELECTED_AI_PERSONA);
  assert.deepEqual(profile.allergies, []);
  assert.equal(supabase.tables.user_health_profiles.length, 1);
}

async function testMissingUserReturnsNull() {
  const supabase = new FakeSupabase({
    users: [],
    user_health_profiles: [],
  });

  const profile = await ensureUserHealthProfile(supabase, 'ghost');
  assert.equal(profile, null);
}

async function testBuildProfileRowForUpsertDefaultsRequiredFields() {
  const row = buildProfileRowForUpsert(
    'user-3',
    {
      user_id: 'user-3',
      gender: 'female',
      age: 29,
      height: 170,
      weight: 60,
      bmi: 20.8,
      allergies: '["peanut"]',
      injury_history: '[]',
      medical_history: '[]',
    },
    {
      gender: null,
      age: null,
      weight: null,
      selected_ai_persona: null,
    }
  );

  assert.equal(row.gender, 'unknown');
  assert.equal(row.age, 0);
  assert.equal(row.weight, 0);
  assert.equal(row.height, 170);
  assert.equal(row.selected_ai_persona, DEFAULT_SELECTED_AI_PERSONA);
  assert.equal(row.allergies, '["peanut"]');
}

async function testLoadExercisePlansWithItems() {
  const supabase = new FakeSupabase({
    user_exercise_plans: [
      {
        exercise_id: 2,
        user_id: 'user-1',
        exercise_type: 'cardio',
        target_date: '2026-04-17',
        created_at: '2026-04-17T09:00:00Z',
        status: 0,
      },
      {
        exercise_id: 1,
        user_id: 'user-1',
        exercise_type: 'lower_body',
        target_date: '2026-04-16',
        created_at: '2026-04-16T09:00:00Z',
        status: 1,
      },
    ],
    exercise_items: [
      { item_id: 12, exercise_id: 1, exercise_name: 'lunges', is_completed: true },
      { item_id: 11, exercise_id: 1, exercise_name: 'squat', is_completed: true },
      { item_id: 21, exercise_id: 2, exercise_name: 'walk', is_completed: false },
    ],
  });

  const plans = await loadExercisePlansWithItems(supabase, {
    userId: 'user-1',
    startDate: '2026-04-16',
    endDate: '2026-04-17',
  });

  assert.equal(plans.length, 2);
  assert.equal(plans[0].exercise_id, 1);
  assert.deepEqual(
    plans[0].exercise_items.map((item) => item.exercise_name),
    ['squat', 'lunges']
  );
  assert.deepEqual(
    plans[1].exercise_items.map((item) => item.exercise_name),
    ['walk']
  );
}

async function testLoadExercisePlansEmpty() {
  const supabase = new FakeSupabase({
    user_exercise_plans: [],
    exercise_items: [],
  });

  const plans = await loadExercisePlansWithItems(supabase, {
    userId: 'user-1',
    targetDate: '2026-04-16',
  });

  assert.deepEqual(plans, []);
}

async function testCreateWorkoutPlansRollsBackOnChildInsertFailure() {
  const supabase = new FakeSupabase(
    {
      user_exercise_plans: [],
      exercise_items: [],
    },
    {
      exercise_items: {
        before_insert: new Error('exercise item insert failed'),
      },
    }
  );

  await assert.rejects(
    () =>
      planMutationService.createWorkoutPlans(supabase, 'user-1', [
        {
          day: '2026-04-16',
          name: 'strength',
          detail: 'squat',
          ex_list: [{ exercise_name: 'squat', sets: 3, calories: 50 }],
        },
      ]),
    /exercise item insert failed/
  );

  assert.deepEqual(supabase.tables.user_exercise_plans, []);
  assert.deepEqual(supabase.tables.exercise_items, []);
}

async function testReplaceWorkoutPlansKeepsOldPlanWhenNewCreateFails() {
  const supabase = new FakeSupabase(
    {
      user_exercise_plans: [
        {
          exercise_id: 10,
          user_id: 'user-1',
          exercise_type: 'legacy-plan',
          target_date: '2026-04-16',
          created_at: '2026-04-16T07:00:00Z',
          status: 0,
        },
      ],
      exercise_items: [
        {
          item_id: 99,
          exercise_id: 10,
          exercise_name: 'legacy-squat',
          is_completed: false,
        },
      ],
    },
    {
      exercise_items: {
        before_insert: new Error('replacement insert failed'),
      },
    }
  );

  await assert.rejects(
    () =>
      planMutationService.replaceWorkoutPlans(supabase, 'user-1', [
        {
          day: '2026-04-16',
          name: 'replacement-plan',
          detail: 'pushup',
          ex_list: [{ exercise_name: 'pushup', sets: 3, calories: 30 }],
        },
      ]),
    /replacement insert failed/
  );

  assert.equal(supabase.tables.user_exercise_plans.length, 1);
  assert.equal(supabase.tables.user_exercise_plans[0].exercise_id, 10);
  assert.equal(supabase.tables.exercise_items.length, 1);
  assert.equal(supabase.tables.exercise_items[0].exercise_id, 10);
}

async function testReplaceWorkoutPlansSwapsPlansOnSuccess() {
  const supabase = new FakeSupabase({
    user_exercise_plans: [
      {
        exercise_id: 10,
        user_id: 'user-1',
        exercise_type: 'legacy-plan',
        target_date: '2026-04-16',
        created_at: '2026-04-16T07:00:00Z',
        status: 0,
      },
    ],
    exercise_items: [
      {
        item_id: 99,
        exercise_id: 10,
        exercise_name: 'legacy-squat',
        is_completed: false,
      },
    ],
  });

  const created = await planMutationService.replaceWorkoutPlans(supabase, 'user-1', [
    {
      day: '2026-04-16',
      name: 'replacement-plan',
      detail: 'pushup',
      ex_list: [{ exercise_name: 'pushup', sets: 3, calories: 30 }],
    },
  ]);

  assert.equal(created.length, 1);
  assert.equal(supabase.tables.user_exercise_plans.length, 1);
  assert.notEqual(supabase.tables.user_exercise_plans[0].exercise_id, 10);
  assert.equal(supabase.tables.user_exercise_plans[0].exercise_type, 'replacement-plan');
  assert.equal(supabase.tables.exercise_items.length, 1);
  assert.equal(supabase.tables.exercise_items[0].exercise_name, 'pushup');
}

async function testDeleteWorkoutPlansForDatesRemovesChildren() {
  const supabase = new FakeSupabase({
    user_exercise_plans: [
      {
        exercise_id: 10,
        user_id: 'user-1',
        exercise_type: 'legacy-plan',
        target_date: '2026-04-16',
        created_at: '2026-04-16T07:00:00Z',
        status: 0,
      },
      {
        exercise_id: 11,
        user_id: 'user-1',
        exercise_type: 'keep-plan',
        target_date: '2026-04-17',
        created_at: '2026-04-17T07:00:00Z',
        status: 0,
      },
    ],
    exercise_items: [
      { item_id: 99, exercise_id: 10, exercise_name: 'legacy-squat' },
      { item_id: 100, exercise_id: 11, exercise_name: 'keep-walk' },
    ],
  });

  const deletedCount = await planMutationService.deleteWorkoutPlansForDates(
    supabase,
    'user-1',
    ['2026-04-16']
  );

  assert.equal(deletedCount, 1);
  assert.deepEqual(
    supabase.tables.user_exercise_plans.map((item) => item.exercise_id),
    [11]
  );
  assert.deepEqual(
    supabase.tables.exercise_items.map((item) => item.item_id),
    [100]
  );
}

async function testDeletePlanItemByOpaqueExerciseId() {
  const supabase = new FakeSupabase({
    user_exercise_plans: [
      {
        exercise_id: 10,
        user_id: 'user-1',
        exercise_type: 'legacy-plan',
        target_date: '2026-04-16',
        created_at: '2026-04-16T07:00:00Z',
        status: 0,
      },
    ],
    exercise_items: [
      { item_id: 99, exercise_id: 10, exercise_name: 'legacy-squat' },
    ],
  });

  const deleted = await planMutationService.deletePlanItemByOpaqueId(
    supabase,
    'user-1',
    'exercise-10'
  );

  assert.equal(deleted.kind, 'exercise');
  assert.deepEqual(supabase.tables.user_exercise_plans, []);
  assert.deepEqual(supabase.tables.exercise_items, []);
}

async function testPasswordHashValidationRejectsBadHashes() {
  assert.equal(isValidBcryptHash(null), false);
  assert.equal(isValidBcryptHash(''), false);
  assert.equal(isValidBcryptHash('not-a-bcrypt-hash'), false);
  assert.equal(await verifyPassword('secret', null), false);
  assert.equal(await verifyPassword('secret', 'not-a-bcrypt-hash'), false);
}

async function testUuidValidation() {
  assert.equal(isValidUuid('6f6d8c2d-0c1a-4a3f-9f7a-2a8d4b7b9c10'), true);
  assert.equal(isValidUuid('codex-probe-1234'), false);
  assert.equal(isValidUuid(''), false);
  assert.equal(isValidUuid(null), false);
}

async function testPasswordVerificationHandlesValidHashes() {
  const hash = await bcrypt.hash('secret', 10);
  assert.equal(isValidBcryptHash(hash), true);
  assert.equal(await verifyPassword('secret', hash), true);
  assert.equal(await verifyPassword('wrong', hash), false);
}

async function testPersistedChatThreadRoundTrip() {
  const supabase = new FakeSupabase({
    chat_threads: [],
    chat_messages: [],
  });

  await persistChatTurn(supabase, {
    userId: 'user-1',
    sessionId: 'thread-1',
    userMessage: '일주일 운동 플랜 짜줘',
    assistantMessage: '일주일 운동 플랜입니다.',
    intent: '계획',
    clientMessageId: 'assistant-msg-1',
    clientUserMessageId: 'user-msg-1',
  });

  const threads = await listChatThreads(supabase, 'user-1');
  const messages = await loadChatMessages(supabase, 'user-1', 'thread-1');

  assert.equal(threads.length, 1);
  assert.equal(threads[0].session_id, 'thread-1');
  assert.equal(threads[0].message_count, 2);
  assert.equal(messages.length, 2);
  assert.equal(messages[0].role, 'user');
  assert.equal(messages[1].role, 'assistant');
  assert.equal(messages[1].client_message_id, 'assistant-msg-1');
}

async function main() {
  await testExistingProfileNormalization();
  await testMissingProfileBootstrap();
  await testMissingUserReturnsNull();
  await testBuildProfileRowForUpsertDefaultsRequiredFields();
  await testLoadExercisePlansWithItems();
  await testLoadExercisePlansEmpty();
  await testCreateWorkoutPlansRollsBackOnChildInsertFailure();
  await testReplaceWorkoutPlansKeepsOldPlanWhenNewCreateFails();
  await testReplaceWorkoutPlansSwapsPlansOnSuccess();
  await testDeleteWorkoutPlansForDatesRemovesChildren();
  await testDeletePlanItemByOpaqueExerciseId();
  await testUuidValidation();
  await testPasswordHashValidationRejectsBadHashes();
  await testPasswordVerificationHandlesValidHashes();
  await testPersistedChatThreadRoundTrip();
  console.log('[internal-contracts] 15/15 passed');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
