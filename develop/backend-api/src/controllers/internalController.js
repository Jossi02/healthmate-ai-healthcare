const crypto = require('crypto');

const supabase = require('../config/db');
const logger = require('../utils/logger');
const { formatKstDate, normalizeIsoDate } = require('../utils/kst');
const {
  parseStoredArray,
  serializeArrayField,
  toOptionalNumber,
  toOptionalString,
} = require('../utils/profileFields');
const { isValidUuid } = require('../utils/ids');
const {
  buildProfileRowForUpsert,
  ensureUserHealthProfile,
  ensureUserHealthProfileRow,
} = require('../services/profileService');
const { loadExercisePlansWithItems } = require('../services/exercisePlanReadService');
const planMutationService = require('../services/planMutationService');

function normalizePlanType(value) {
  const text = String(value || '').trim().toLowerCase();
  if (text === 'diet' || text === 'meal') return 'diet';
  if (text === 'workout' || text === 'exercise') return 'workout';
  return null;
}

function ensureValidUserIdParam(res, userId) {
  if (isValidUuid(userId)) {
    return true;
  }
  res.status(400).json({ error: 'Invalid user_id format.' });
  return false;
}

function normalizeDeletePlanType(value) {
  const text = String(value || '').trim().toLowerCase();
  if (text === 'all' || text === 'both' || text === '전체' || text === '둘다') return 'all';
  return normalizePlanType(text);
}

function dedupeDates(items = []) {
  return [...new Set(items.map((item) => item.day))];
}

function normalizeTargetDates(value) {
  if (!Array.isArray(value)) return [];
  return [
    ...new Set(
      value
        .map((item) => normalizeIsoDate(item))
        .filter(Boolean)
    ),
  ];
}

function normalizeExerciseList(rawExerciseList, detailFallback) {
  if (Array.isArray(rawExerciseList) && rawExerciseList.length > 0) {
    return rawExerciseList
      .map((item) => ({
        exercise_name: toOptionalString(
          item.exercise_name || item.name || item.title || item.exercise
        ),
        sets: toOptionalNumber(item.sets || item.set || item.count),
        duration_minutes: toOptionalNumber(
          item.duration_minutes || item.duration || item.minutes
        ),
        calories: toOptionalNumber(item.calories) || 0,
      }))
      .filter((item) => item.exercise_name);
  }

  const detailText = toOptionalString(detailFallback);
  if (!detailText) {
    return [];
  }

  return [
    {
      exercise_name: detailText,
      sets: 3,
      duration_minutes: null,
      calories: 0,
    },
  ];
}

function normalizeIncomingPlanItems(planType, items) {
  if (!Array.isArray(items)) {
    return [];
  }

  return items
    .map((item) => {
      const day = normalizeIsoDate(item.day || item.date);
      const name = toOptionalString(item.name);
      const detail = toOptionalString(item.detail);

      if (!day || !name) {
        return null;
      }

      return {
        id: toOptionalString(item.id),
        day,
        name,
        detail,
        ex_list: planType === 'workout'
          ? normalizeExerciseList(item.ex_list, detail)
          : [],
      };
    })
    .filter(Boolean);
}

function parsePlanCheckId(itemId) {
  if (itemId.startsWith('exercise-item-')) {
    return {
      kind: 'exercise-item',
      numericId: Number(itemId.replace('exercise-item-', '')),
    };
  }

  if (itemId.startsWith('exercise-')) {
    return {
      kind: 'exercise',
      numericId: Number(itemId.replace('exercise-', '')),
    };
  }

  if (itemId.startsWith('meal-')) {
    return {
      kind: 'meal',
      numericId: Number(itemId.replace('meal-', '')),
    };
  }

  return null;
}

function calculateBmi(weight, height) {
  if (!weight || !height) return null;
  const heightInMeters = height / 100;
  if (!heightInMeters) return null;
  return Number((weight / (heightInMeters * heightInMeters)).toFixed(1));
}

async function getOwnedExercisePlan(userId, exerciseId) {
  const { data, error } = await supabase
    .from('user_exercise_plans')
    .select('exercise_id')
    .eq('user_id', userId)
    .eq('exercise_id', exerciseId)
    .maybeSingle();

  if (error) throw error;
  return data;
}

async function getOwnedExerciseItem(userId, itemId) {
  const { data: item, error } = await supabase
    .from('exercise_items')
    .select('item_id, exercise_id')
    .eq('item_id', itemId)
    .maybeSingle();

  if (error) throw error;
  if (!item) return null;

  const ownedPlan = await getOwnedExercisePlan(userId, item.exercise_id);
  if (!ownedPlan) return null;

  return item;
}

async function rebuildParentExerciseStatus(exerciseId) {
  const { data: siblings, error } = await supabase
    .from('exercise_items')
    .select('is_completed')
    .eq('exercise_id', exerciseId);

  if (error) throw error;

  const total = siblings.length;
  const completedCount = siblings.filter((item) => item.is_completed).length;

  let nextStatus = 0;
  if (total > 0 && completedCount === total) nextStatus = 1;
  else if (completedCount > 0) nextStatus = 2;

  const { error: parentError } = await supabase
    .from('user_exercise_plans')
    .update({ status: nextStatus })
    .eq('exercise_id', exerciseId);

  if (parentError) throw parentError;
  return nextStatus;
}

async function createWorkoutPlans(userId, normalizedItems) {
  const createdItems = [];

  for (const item of normalizedItems) {
    const totalCalories = item.ex_list.reduce(
      (sum, exercise) => sum + Number(exercise.calories || 0),
      0
    );
    const { data: plan, error: planError } = await supabase
      .from('user_exercise_plans')
      .insert({
        user_id: userId,
        exercise_type: item.name,
        total_calories: totalCalories,
        status: 0,
        target_date: item.day,
      })
      .select('*')
      .single();

    if (planError) throw planError;

    let exerciseItems = [];
    if (item.ex_list.length > 0) {
      const insertRows = item.ex_list.map((exercise) => ({
        exercise_id: plan.exercise_id,
        exercise_name: exercise.exercise_name,
        calories: Number(exercise.calories || 0),
        target_sets: exercise.sets ?? null,
        duration_minutes: exercise.duration_minutes ?? null,
        is_completed: false,
      }));

      const { data: insertedItems, error: itemError } = await supabase
        .from('exercise_items')
        .insert(insertRows)
        .select('*');

      if (itemError) throw itemError;
      exerciseItems = insertedItems || [];
    }

    createdItems.push({
      ...item,
      plan,
      exercise_items: exerciseItems,
    });
  }

  return createdItems;
}

async function createDietPlans(userId, normalizedItems) {
  const createdItems = [];

  for (const item of normalizedItems) {
    const { data: meal, error } = await supabase
      .from('user_meal_plans')
      .insert({
        user_id: userId,
        food_name: item.detail || item.name,
        meal_type: item.name,
        target_date: item.day,
        is_completed: false,
        calories: 0,
      })
      .select('*')
      .single();

    if (error && !String(error.message || '').includes('calories')) {
      throw error;
    }

    if (error) {
      const retry = await supabase
        .from('user_meal_plans')
        .insert({
          user_id: userId,
          food_name: item.detail || item.name,
          meal_type: item.name,
          target_date: item.day,
          is_completed: false,
        })
        .select('*')
        .single();

      if (retry.error) throw retry.error;
      createdItems.push(retry.data);
      continue;
    }

    createdItems.push(meal);
  }

  return createdItems;
}

async function deleteWorkoutPlansForDates(userId, targetDates) {
  if (targetDates.length === 0) return;

  const { data: existingPlans, error } = await supabase
    .from('user_exercise_plans')
    .select('exercise_id')
    .eq('user_id', userId)
    .in('target_date', targetDates);

  if (error) throw error;

  const exerciseIds = (existingPlans || []).map((plan) => plan.exercise_id);
  if (exerciseIds.length > 0) {
    const { error: itemError } = await supabase
      .from('exercise_items')
      .delete()
      .in('exercise_id', exerciseIds);

    if (itemError) throw itemError;
  }

  const { error: deleteError } = await supabase
    .from('user_exercise_plans')
    .delete()
    .eq('user_id', userId)
    .in('target_date', targetDates);

  if (deleteError) throw deleteError;
}

async function deleteDietPlansForDates(userId, targetDates) {
  if (targetDates.length === 0) return;

  const { error } = await supabase
    .from('user_meal_plans')
    .delete()
    .eq('user_id', userId)
    .in('target_date', targetDates);

  if (error) throw error;
}

async function loadExistingConflictDates(userId, planType, targetDates) {
  if (targetDates.length === 0) return [];

  if (planType === 'workout') {
    const { data, error } = await supabase
      .from('user_exercise_plans')
      .select('target_date')
      .eq('user_id', userId)
      .in('target_date', targetDates);

    if (error) throw error;
    return [...new Set((data || []).map((row) => row.target_date))];
  }

  const { data, error } = await supabase
    .from('user_meal_plans')
    .select('target_date')
    .eq('user_id', userId)
    .in('target_date', targetDates);

  if (error) throw error;
  return [...new Set((data || []).map((row) => row.target_date))];
}

function normalizeIdempotencyKey(value) {
  if (Array.isArray(value)) {
    return normalizeIdempotencyKey(value[0]);
  }
  const text = String(value || '').trim();
  return text || null;
}

function readIdempotencyKey(req) {
  return normalizeIdempotencyKey(
    req.headers?.['idempotency-key']
      || req.headers?.['x-idempotency-key']
      || req.body?._idempotency_key
      || req.body?.idempotency_key
  );
}

function stripIdempotencyFields(value) {
  if (Array.isArray(value)) {
    return value.map(stripIdempotencyFields);
  }
  if (!value || typeof value !== 'object') {
    return value;
  }
  return Object.keys(value)
    .sort()
    .reduce((acc, key) => {
      if (key === '_idempotency_key' || key === 'idempotency_key') {
        return acc;
      }
      acc[key] = stripIdempotencyFields(value[key]);
      return acc;
    }, {});
}

function hashIdempotencyRequest(req, userId, operation) {
  const normalized = {
    operation,
    user_id: userId,
    body: stripIdempotencyFields(req.body || {}),
  };
  return crypto
    .createHash('sha256')
    .update(JSON.stringify(normalized))
    .digest('hex');
}

function isMissingIdempotencyTableError(error) {
  return error?.code === '42P01'
    || String(error?.message || '').includes('ai_was_idempotency_keys');
}

function isDuplicateIdempotencyError(error) {
  return error?.code === '23505'
    || String(error?.message || '').toLowerCase().includes('duplicate');
}

const MEMORY_IDEMPOTENCY_TTL_MS = 30 * 60 * 1000;
const memoryIdempotencyStore = new Map();

function cleanupMemoryIdempotencyStore() {
  const now = Date.now();
  for (const [key, value] of memoryIdempotencyStore.entries()) {
    if (now - Number(value.updatedAt || value.createdAt || 0) > MEMORY_IDEMPOTENCY_TTL_MS) {
      memoryIdempotencyStore.delete(key);
    }
  }
}

function isStaleIdempotencyProcessing(row) {
  const updatedAt = Date.parse(row?.updated_at || row?.created_at || '');
  if (!Number.isFinite(updatedAt)) {
    return false;
  }
  return Date.now() - updatedAt > 10 * 60 * 1000;
}

function isStaleMemoryProcessing(row) {
  return Date.now() - Number(row?.updatedAt || row?.createdAt || 0) > 10 * 60 * 1000;
}

function beginMemoryIdempotency(req, res, userId, operation, idempotencyKey, requestHash) {
  cleanupMemoryIdempotencyStore();
  const existing = memoryIdempotencyStore.get(idempotencyKey);

  if (existing) {
    if (
      String(existing.userId || '').toLowerCase() !== String(userId || '').toLowerCase()
      || existing.operation !== operation
    ) {
      res.status(409).json({ error: 'Idempotency key was already used for another operation.' });
      return { enabled: true, handled: true, store: 'memory' };
    }
    if (existing.requestHash && existing.requestHash !== requestHash) {
      res.status(409).json({ error: 'Idempotency key was already used with different payload.' });
      return { enabled: true, handled: true, store: 'memory' };
    }
    if (existing.status === 'completed' && existing.responseBody) {
      res.status(existing.statusCode || 200).json(existing.responseBody);
      return { enabled: true, handled: true, store: 'memory' };
    }
    if (existing.status === 'processing' && !isStaleMemoryProcessing(existing)) {
      res.status(409).json({ error: 'Duplicate request is already processing.' });
      return { enabled: true, handled: true, store: 'memory' };
    }
  }

  const now = Date.now();
  memoryIdempotencyStore.set(idempotencyKey, {
    userId,
    operation,
    requestHash,
    status: 'processing',
    createdAt: existing?.createdAt || now,
    updatedAt: now,
  });

  return {
    enabled: true,
    store: 'memory',
    idempotencyKey,
    operation,
    userId,
    requestHash,
  };
}

async function beginIdempotency(req, res, userId, operation) {
  const idempotencyKey = readIdempotencyKey(req);
  if (!idempotencyKey) {
    return { enabled: false };
  }

  const requestHash = hashIdempotencyRequest(req, userId, operation);
  const now = new Date().toISOString();
  const { data: existing, error: readError } = await supabase
    .from('ai_was_idempotency_keys')
    .select('*')
    .eq('idempotency_key', idempotencyKey)
    .maybeSingle();

  if (readError) {
    if (isMissingIdempotencyTableError(readError)) {
      logger.warn('Idempotency table is missing; using in-memory idempotency fallback.');
      return beginMemoryIdempotency(req, res, userId, operation, idempotencyKey, requestHash);
    }
    throw readError;
  }

  if (existing) {
    if (
      String(existing.user_id || '').toLowerCase() !== String(userId || '').toLowerCase()
      || existing.operation !== operation
    ) {
      res.status(409).json({ error: 'Idempotency key was already used for another operation.' });
      return { enabled: true, handled: true };
    }
    if (existing.request_hash && existing.request_hash !== requestHash) {
      res.status(409).json({ error: 'Idempotency key was already used with different payload.' });
      return { enabled: true, handled: true };
    }
    if (existing.status === 'completed' && existing.response_body) {
      res.status(existing.status_code || 200).json(existing.response_body);
      return { enabled: true, handled: true };
    }
    if (existing.status === 'processing' && !isStaleIdempotencyProcessing(existing)) {
      res.status(409).json({ error: 'Duplicate request is already processing.' });
      return { enabled: true, handled: true };
    }

    const { error: updateError } = await supabase
      .from('ai_was_idempotency_keys')
      .update({
        status: 'processing',
        request_hash: requestHash,
        status_code: null,
        response_body: null,
        last_error: null,
        updated_at: now,
        completed_at: null,
      })
      .eq('idempotency_key', idempotencyKey);

    if (updateError) {
      if (isMissingIdempotencyTableError(updateError)) {
        logger.warn('Idempotency table is missing; using in-memory idempotency fallback.');
        return beginMemoryIdempotency(req, res, userId, operation, idempotencyKey, requestHash);
      }
      throw updateError;
    }

    return {
      enabled: true,
      store: 'database',
      idempotencyKey,
      operation,
      userId,
      requestHash,
    };
  }

  const { error: insertError } = await supabase
    .from('ai_was_idempotency_keys')
    .insert({
      idempotency_key: idempotencyKey,
      user_id: userId,
      operation,
      request_hash: requestHash,
      status: 'processing',
      created_at: now,
      updated_at: now,
    });

  if (insertError) {
    if (isMissingIdempotencyTableError(insertError)) {
      logger.warn('Idempotency table is missing; using in-memory idempotency fallback.');
      return beginMemoryIdempotency(req, res, userId, operation, idempotencyKey, requestHash);
    }
    if (isDuplicateIdempotencyError(insertError)) {
      res.status(409).json({ error: 'Duplicate request is already processing.' });
      return { enabled: true, handled: true };
    }
    throw insertError;
  }

  return {
    enabled: true,
    store: 'database',
    idempotencyKey,
    operation,
    userId,
    requestHash,
  };
}

async function finishIdempotency(context, statusCode, responseBody) {
  if (!context?.enabled || !context.idempotencyKey) {
    return;
  }
  if (context.store === 'memory') {
    const existing = memoryIdempotencyStore.get(context.idempotencyKey) || {};
    memoryIdempotencyStore.set(context.idempotencyKey, {
      ...existing,
      status: 'completed',
      statusCode,
      responseBody,
      updatedAt: Date.now(),
      completedAt: Date.now(),
    });
    return;
  }
  const now = new Date().toISOString();
  const { error } = await supabase
    .from('ai_was_idempotency_keys')
    .update({
      status: 'completed',
      status_code: statusCode,
      response_body: responseBody,
      last_error: null,
      updated_at: now,
      completed_at: now,
    })
    .eq('idempotency_key', context.idempotencyKey);

  if (error) {
    if (isMissingIdempotencyTableError(error)) {
      logger.warn('Idempotency table is missing; completed response was not stored.');
      return;
    }
    logger.warn(`Failed to store idempotency result: ${error.message}`);
  }
}

async function failIdempotency(context, error) {
  if (!context?.enabled || !context.idempotencyKey) {
    return;
  }
  if (context.store === 'memory') {
    const existing = memoryIdempotencyStore.get(context.idempotencyKey) || {};
    memoryIdempotencyStore.set(context.idempotencyKey, {
      ...existing,
      status: 'failed',
      lastError: String(error?.message || error),
      updatedAt: Date.now(),
    });
    return;
  }
  const { error: updateError } = await supabase
    .from('ai_was_idempotency_keys')
    .update({
      status: 'failed',
      last_error: String(error?.message || error),
      updated_at: new Date().toISOString(),
    })
    .eq('idempotency_key', context.idempotencyKey);

  if (updateError && !isMissingIdempotencyTableError(updateError)) {
    logger.warn(`Failed to mark idempotency failure: ${updateError.message}`);
  }
}

async function sendWithIdempotency(res, context, statusCode, body) {
  await finishIdempotency(context, statusCode, body);
  return res.status(statusCode).json(body);
}

// GET /api/user/profile/:user_id
exports.getProfile = async (req, res) => {
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const profile = await ensureUserHealthProfile(supabase, userId);
    if (!profile) {
      return res.status(404).json({ error: 'Profile not found.' });
    }

    return res.json({
      user_id: profile.user_id,
      weight: profile.weight ?? null,
      height: profile.height ?? null,
      age: profile.age ?? null,
      gender: profile.gender ?? null,
      diet_type: profile.diet_type ?? null,
      allergies: parseStoredArray(profile.allergies),
      injury_history: parseStoredArray(profile.injury_history),
      goal: profile.goal ?? null,
      activity_level: profile.activity_level ?? null,
      selected_ai_persona: profile.selected_ai_persona ?? null,
      bmi: profile.bmi ?? null,
      medical_history: parseStoredArray(profile.medical_history),
      mbti: profile.mbti ?? null,
    });
  } catch (error) {
    logger.error(`Internal getProfile error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to load profile.' });
  }
};

// GET /api/plan/today/:user_id
exports.getTodayPlan = async (req, res) => {
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const today = formatKstDate();

    const exercises = await loadExercisePlansWithItems(supabase, {
      userId,
      targetDate: today,
    });

    const { data: meals, error: mealError } = await supabase
      .from('user_meal_plans')
      .select('*')
      .eq('user_id', userId)
      .eq('target_date', today)
      .order('created_at', { ascending: true });

    if (mealError) throw mealError;

    const planItems = [];

    for (const exercise of exercises || []) {
      const exerciseItems = exercise.exercise_items || [];
      if (exerciseItems.length > 0) {
        for (const item of exerciseItems) {
          planItems.push({
            id: `exercise-item-${item.item_id}`,
            type: 'exercise',
            name: exercise.exercise_type || 'exercise',
            detail: item.exercise_name,
            day: exercise.target_date,
            completed: Boolean(item.is_completed),
          });
        }
      } else {
        planItems.push({
          id: `exercise-${exercise.exercise_id}`,
          type: 'exercise',
          name: exercise.exercise_type || 'exercise',
          detail: `total ${exercise.total_calories || 0} kcal`,
          day: exercise.target_date,
          completed: exercise.status === 1,
        });
      }
    }

    for (const meal of meals || []) {
      planItems.push({
        id: `meal-${meal.meal_id}`,
        type: 'meal',
        name: meal.meal_type || 'meal',
        detail: meal.food_name,
        day: meal.target_date,
        completed: Boolean(meal.is_completed),
      });
    }

    return res.json(planItems);
  } catch (error) {
    logger.error(`Internal getTodayPlan error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to load today plan.' });
  }
};

// PUT /api/user/profile/:user_id
exports.updateProfile = async (req, res) => {
  let idempotency = null;
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const allowedUpdates = {
      weight: toOptionalNumber(req.body.weight),
      height: toOptionalNumber(req.body.height),
      age: toOptionalNumber(req.body.age),
      gender: toOptionalString(req.body.gender),
      diet_type: req.body.diet_type !== undefined ? toOptionalString(req.body.diet_type) : undefined,
      allergies: req.body.allergies !== undefined ? serializeArrayField(req.body.allergies) : undefined,
      injury_history: req.body.injury_history !== undefined ? serializeArrayField(req.body.injury_history) : undefined,
      goal: toOptionalString(req.body.goal),
      activity_level: toOptionalString(req.body.activity_level),
      medical_history: req.body.medical_history !== undefined
        ? serializeArrayField(req.body.medical_history)
        : undefined,
      mbti: toOptionalString(req.body.mbti),
    };

    const filteredUpdate = Object.fromEntries(
      Object.entries(allowedUpdates).filter(([, value]) => value !== undefined)
    );

    if (Object.keys(filteredUpdate).length === 0) {
      return res.status(400).json({ error: 'No valid profile fields provided.' });
    }

    idempotency = await beginIdempotency(req, res, userId, 'profile_update');
    if (idempotency.handled) return;

    const existingProfile = await ensureUserHealthProfileRow(supabase, userId);
    if (!existingProfile) {
      return sendWithIdempotency(res, idempotency, 404, { error: 'User not found.' });
    }

    const profilePayload = buildProfileRowForUpsert(userId, existingProfile, filteredUpdate);
    profilePayload.bmi = calculateBmi(profilePayload.weight, profilePayload.height) ?? 0;

    const { data: updatedProfile, error } = await supabase
      .from('user_health_profiles')
      .upsert(profilePayload, { onConflict: 'user_id' })
      .select('*')
      .single();

    if (error) throw error;

    return sendWithIdempotency(res, idempotency, 200, {
      status: 'success',
      updated_fields: Object.keys(filteredUpdate),
      profile: updatedProfile,
    });
  } catch (error) {
    await failIdempotency(idempotency, error);
    logger.error(`Internal updateProfile error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to update profile.' });
  }
};

// POST /api/plan/create/:user_id
exports.createPlan = async (req, res) => {
  let idempotency = null;
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const planType = normalizePlanType(req.body.plan_type);

    if (!planType) {
      return res.status(400).json({ error: 'plan_type is required.' });
    }

    const normalizedItems = normalizeIncomingPlanItems(planType, req.body.items);

    if (normalizedItems.length === 0) {
      return res.status(400).json({ error: 'plan_type and valid items are required.' });
    }

    idempotency = await beginIdempotency(req, res, userId, 'plan_write');
    if (idempotency.handled) return;

    const targetDates = dedupeDates(normalizedItems);
    const conflictDates = await planMutationService.loadExistingConflictDates(
      supabase,
      userId,
      planType,
      targetDates
    );

    if (conflictDates.length > 0) {
      return sendWithIdempotency(res, idempotency, 409, {
        error: 'Existing plans already exist for one or more requested dates.',
        conflict_dates: conflictDates,
        suggested_action: 'update',
      });
    }

    const created = planType === 'workout'
      ? await planMutationService.createWorkoutPlans(supabase, userId, normalizedItems)
      : await planMutationService.createDietPlans(supabase, userId, normalizedItems);

    return sendWithIdempotency(res, idempotency, 201, {
      status: 'success',
      plan_type: planType,
      created_count: created.length,
      items: created,
    });
  } catch (error) {
    await failIdempotency(idempotency, error);
    logger.error(`Internal createPlan error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to create plan.' });
  }
};

// PUT /api/plan/update/:user_id
exports.updatePlan = async (req, res) => {
  let idempotency = null;
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const planType = normalizePlanType(req.body.plan_type);

    if (!planType) {
      return res.status(400).json({ error: 'plan_type is required.' });
    }

    const normalizedItems = normalizeIncomingPlanItems(planType, req.body.items);

    if (normalizedItems.length === 0) {
      return res.status(400).json({ error: 'plan_type and valid items are required.' });
    }

    const targetDates = dedupeDates(normalizedItems);

    idempotency = await beginIdempotency(req, res, userId, 'plan_write');
    if (idempotency.handled) return;

    const updated = planType === 'workout'
      ? await planMutationService.replaceWorkoutPlans(supabase, userId, normalizedItems)
      : await planMutationService.replaceDietPlans(supabase, userId, normalizedItems);

    return sendWithIdempotency(res, idempotency, 200, {
      status: 'success',
      plan_type: planType,
      replaced_dates: targetDates,
      updated_count: updated.length,
    });
  } catch (error) {
    await failIdempotency(idempotency, error);
    logger.error(`Internal updatePlan error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to update plan.' });
  }
};

// DELETE /api/plan/delete/:user_id
exports.deletePlan = async (req, res) => {
  let idempotency = null;
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const planType = normalizeDeletePlanType(req.body.plan_type);
    const targetDates = normalizeTargetDates(req.body.target_dates);

    if (!planType) {
      return res.status(400).json({ error: 'plan_type is required.' });
    }

    if (targetDates.length === 0) {
      return res.status(400).json({ error: 'target_dates is required.' });
    }

    let deletedWorkoutCount = 0;
    let deletedDietCount = 0;

    idempotency = await beginIdempotency(req, res, userId, 'plan_delete');
    if (idempotency.handled) return;

    if (planType === 'workout' || planType === 'all') {
      deletedWorkoutCount = await planMutationService.deleteWorkoutPlansForDates(
        supabase,
        userId,
        targetDates
      );
    }

    if (planType === 'diet' || planType === 'all') {
      deletedDietCount = await planMutationService.deleteDietPlansForDates(
        supabase,
        userId,
        targetDates
      );
    }

    return sendWithIdempotency(res, idempotency, 200, {
      status: 'success',
      plan_type: planType,
      target_dates: targetDates,
      deleted_workout_count: deletedWorkoutCount,
      deleted_diet_count: deletedDietCount,
      deleted_count: deletedWorkoutCount + deletedDietCount,
    });
  } catch (error) {
    await failIdempotency(idempotency, error);
    logger.error(`Internal deletePlan error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to delete plan.' });
  }
};

// PUT /api/plan/check/:user_id
exports.checkPlan = async (req, res) => {
  let idempotency = null;
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const { item_id: itemId } = req.body;
    const parsed = parsePlanCheckId(itemId || '');

    if (!parsed || !Number.isFinite(parsed.numericId)) {
      return res.status(400).json({ error: 'Invalid item_id format.' });
    }

    idempotency = await beginIdempotency(req, res, userId, 'plan_check');
    if (idempotency.handled) return;

    if (parsed.kind === 'exercise-item') {
      const ownedItem = await getOwnedExerciseItem(userId, parsed.numericId);
      if (!ownedItem) {
        return sendWithIdempotency(res, idempotency, 404, { error: 'Exercise item not found.' });
      }

      const { data: updatedItem, error } = await supabase
        .from('exercise_items')
        .update({ is_completed: true })
        .eq('item_id', parsed.numericId)
        .select('item_id, exercise_id')
        .single();

      if (error) throw error;

      const parentStatus = await rebuildParentExerciseStatus(updatedItem.exercise_id);
      return sendWithIdempotency(res, idempotency, 200, {
        status: 'success',
        item_id: itemId,
        checked: true,
        parent_status: parentStatus,
      });
    }

    if (parsed.kind === 'exercise') {
      const ownedPlan = await getOwnedExercisePlan(userId, parsed.numericId);
      if (!ownedPlan) {
        return sendWithIdempotency(res, idempotency, 404, { error: 'Exercise plan not found.' });
      }

      const { data: updatedPlan, error: parentError } = await supabase
        .from('user_exercise_plans')
        .update({ status: 1 })
        .eq('exercise_id', parsed.numericId)
        .eq('user_id', userId)
        .select('exercise_id')
        .maybeSingle();

      if (parentError) throw parentError;
      if (!updatedPlan) {
        return sendWithIdempotency(res, idempotency, 404, { error: 'Exercise plan not found.' });
      }

      const { error: itemError } = await supabase
        .from('exercise_items')
        .update({ is_completed: true })
        .eq('exercise_id', parsed.numericId);

      if (itemError) throw itemError;

      return sendWithIdempotency(res, idempotency, 200, {
        status: 'success',
        item_id: itemId,
        checked: true,
        parent_status: 1,
      });
    }

    const { data: updatedMeal, error: mealError } = await supabase
      .from('user_meal_plans')
      .update({ is_completed: true })
      .eq('meal_id', parsed.numericId)
      .eq('user_id', userId)
      .select('meal_id')
      .maybeSingle();

    if (mealError) throw mealError;
    if (!updatedMeal) {
      return sendWithIdempotency(res, idempotency, 404, { error: 'Meal plan not found.' });
    }

    return sendWithIdempotency(res, idempotency, 200, {
      status: 'success',
      item_id: itemId,
      checked: true,
    });
  } catch (error) {
    await failIdempotency(idempotency, error);
    logger.error(`Internal checkPlan error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to check plan item.' });
  }
};

// GET /api/workout-plan/full/:user_id
exports.getFullWorkoutPlan = async (req, res) => {
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const exercises = await loadExercisePlansWithItems(supabase, { userId });

    return res.json({
      plan_type: 'workout',
      items: (exercises || []).map((exercise) => ({
        id: `exercise-${exercise.exercise_id}`,
        name: exercise.exercise_type,
        detail: (exercise.exercise_items || []).map((item) => item.exercise_name).join(', ') || null,
        day: exercise.target_date,
        completed: exercise.status === 1,
        ex_list: (exercise.exercise_items || []).map((item) => ({
          exercise_name: item.exercise_name,
          sets: item.target_sets ?? null,
          duration_minutes: item.duration_minutes ?? null,
          calories: item.calories ?? 0,
        })),
      })),
    });
  } catch (error) {
    logger.error(`Internal getFullWorkoutPlan error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to load workout plans.' });
  }
};

// GET /api/diet-plan/full/:user_id
exports.getFullDietPlan = async (req, res) => {
  try {
    const { user_id: userId } = req.params;
    if (!ensureValidUserIdParam(res, userId)) return;

    const { data: meals, error } = await supabase
      .from('user_meal_plans')
      .select('*')
      .eq('user_id', userId)
      .order('target_date', { ascending: true })
      .order('created_at', { ascending: true });

    if (error) throw error;

    return res.json({
      plan_type: 'diet',
      items: (meals || []).map((meal) => ({
        id: `meal-${meal.meal_id}`,
        name: meal.meal_type,
        detail: meal.food_name,
        day: meal.target_date,
        completed: Boolean(meal.is_completed),
      })),
    });
  } catch (error) {
    logger.error(`Internal getFullDietPlan error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to load diet plans.' });
  }
};
