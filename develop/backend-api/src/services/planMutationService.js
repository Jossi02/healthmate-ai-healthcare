function dedupeDates(items = []) {
  return [...new Set(items.map((item) => item.day))];
}

const DIET_EVIDENCE_MARKERS = [
  '근거',
  '이유',
  '추천 이유',
  '선정 이유',
  '설명',
  '고려',
  '주의',
  '참고',
  '알레르기',
  '질환',
  '목표',
  'reason',
  'evidence',
  'rationale',
  'because',
  'note',
  'guideline',
  'profile',
  'allergy',
  'disease',
  'constraint',
];

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function stripDietEvidenceTail(value) {
  let next = String(value || '').replace(/\s+/g, ' ').trim();
  for (const marker of DIET_EVIDENCE_MARKERS) {
    const pattern = new RegExp(
      `\\s*(?:[/|;,.]\\s*)?${escapeRegExp(marker)}\\s*(?:[:\\-]|\\s).*`,
      'i'
    );
    next = next.replace(pattern, '');
  }
  return next.trim();
}

function isDietEvidencePiece(value) {
  const normalized = String(value || '').toLowerCase();
  return DIET_EVIDENCE_MARKERS.some((marker) => normalized.includes(marker.toLowerCase()));
}

function cleanDietPlanValue(value) {
  const raw = String(value || '').replace(/\s+/g, ' ').trim();
  if (!raw) return '';
  const withoutEvidenceTail = stripDietEvidenceTail(raw);
  const pieces = withoutEvidenceTail
    .split(/\s*(?:\/|\||;)\s*/)
    .map((piece) => stripDietEvidenceTail(piece).trim())
    .filter(Boolean)
    .filter((piece) => !isDietEvidencePiece(piece));
  return (pieces.length ? pieces.join(', ') : withoutEvidenceTail).trim();
}

function parseCalories(value) {
  if (value === null || value === undefined || value === '') return 0;
  const match = String(value).match(/-?\d+(?:\.\d+)?/);
  if (!match) return 0;
  const parsed = Number(match[0]);
  if (!Number.isFinite(parsed) || parsed < 0) return 0;
  return Math.round(parsed);
}

const MEAL_SLOT_PATTERN = /(아침|점심|저녁|breakfast|lunch|dinner)\s*[:：-]\s*/gi;
const MEAL_SLOT_PREFIX_PATTERN = /^\s*(아침|점심|저녁|breakfast|lunch|dinner)\s*[:：-]\s*/i;

function canonicalMealType(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized.includes('breakfast') || normalized.includes('아침')) return 'Breakfast';
  if (normalized.includes('lunch') || normalized.includes('점심')) return 'Lunch';
  if (normalized.includes('dinner') || normalized.includes('저녁')) return 'Dinner';
  return null;
}

function stripMealTypePrefix(value) {
  return String(value || '').replace(MEAL_SLOT_PREFIX_PATTERN, '').trim();
}

function trimMealSegment(value) {
  return String(value || '').replace(/^[\s,，;|/]+|[\s,，;|/]+$/g, '').trim();
}

function splitCompoundDietText(value) {
  const raw = cleanDietPlanValue(value);
  if (!raw) return [];

  const matches = [...raw.matchAll(MEAL_SLOT_PATTERN)];
  if (matches.length === 0) return [];

  return matches
    .map((match, index) => {
      const mealType = canonicalMealType(match[1]);
      const start = (match.index || 0) + match[0].length;
      const end = matches[index + 1]?.index ?? raw.length;
      const foodName = cleanDietPlanValue(trimMealSegment(raw.slice(start, end)));
      return mealType && foodName ? { mealType, foodName } : null;
    })
    .filter(Boolean);
}

function expandDietPlanItems(normalizedItems = []) {
  return normalizedItems.flatMap((item) => {
    const detailSegments = splitCompoundDietText(item.detail);
    const nameSegments = splitCompoundDietText(item.name);
    const segments = detailSegments.length >= 2 ? detailSegments : nameSegments.length >= 2 ? nameSegments : [];

    if (segments.length === 0) {
      return [item];
    }

    return segments.map((segment) => ({
      ...item,
      name: segment.mealType,
      detail: segment.foodName,
    }));
  });
}

function buildDietInsertPayload(userId, item, includeCalories = true) {
  const cleanedName = cleanDietPlanValue(item.name);
  const cleanedDetail = cleanDietPlanValue(item.detail);
  const inferredMealType = canonicalMealType(cleanedName);
  const foodName = cleanedDetail || cleanDietPlanValue(stripMealTypePrefix(cleanedName)) || 'Meal';
  const mealType = inferredMealType || cleanedName || 'meal';
  const payload = {
    user_id: userId,
    food_name: foodName,
    meal_type: mealType,
    target_date: item.day,
    is_completed: false,
  };
  if (includeCalories) {
    payload.calories = parseCalories(item.calories ?? item.kcal ?? item.total_calories);
  }
  return payload;
}

async function deleteWorkoutPlansByIds(supabase, userId, exerciseIds = []) {
  if (exerciseIds.length === 0) return;

  const { error: itemError } = await supabase
    .from('exercise_items')
    .delete()
    .in('exercise_id', exerciseIds);

  if (itemError) throw itemError;

  const { error: planError } = await supabase
    .from('user_exercise_plans')
    .delete()
    .eq('user_id', userId)
    .in('exercise_id', exerciseIds);

  if (planError) throw planError;
}

async function deleteDietPlansByIds(supabase, userId, mealIds = []) {
  if (mealIds.length === 0) return;

  const { error } = await supabase
    .from('user_meal_plans')
    .delete()
    .eq('user_id', userId)
    .in('meal_id', mealIds);

  if (error) throw error;
}

async function deleteWorkoutPlansForDates(supabase, userId, targetDates = []) {
  if (targetDates.length === 0) return 0;

  const exerciseIds = await loadExistingWorkoutPlanIdsForDates(supabase, userId, targetDates);
  await deleteWorkoutPlansByIds(supabase, userId, exerciseIds);
  return exerciseIds.length;
}

async function deleteDietPlansForDates(supabase, userId, targetDates = []) {
  if (targetDates.length === 0) return 0;

  const mealIds = await loadExistingDietPlanIdsForDates(supabase, userId, targetDates);
  await deleteDietPlansByIds(supabase, userId, mealIds);
  return mealIds.length;
}

async function deleteWorkoutPlansForUser(supabase, userId) {
  const exerciseIds = await loadExistingWorkoutPlanIdsForUser(supabase, userId);
  await deleteWorkoutPlansByIds(supabase, userId, exerciseIds);
  return exerciseIds.length;
}

async function deleteDietPlansForUser(supabase, userId) {
  const mealIds = await loadExistingDietPlanIdsForUser(supabase, userId);
  await deleteDietPlansByIds(supabase, userId, mealIds);
  return mealIds.length;
}

async function deleteExerciseItemById(supabase, userId, itemId) {
  const safeItemId = Number(itemId);
  if (!Number.isFinite(safeItemId)) return null;

  const { data: item, error: itemLoadError } = await supabase
    .from('exercise_items')
    .select('item_id, exercise_id, calories')
    .eq('item_id', safeItemId)
    .maybeSingle();

  if (itemLoadError) throw itemLoadError;
  if (!item) return null;

  const { data: parentPlan, error: planLoadError } = await supabase
    .from('user_exercise_plans')
    .select('exercise_id, total_calories')
    .eq('user_id', userId)
    .eq('exercise_id', item.exercise_id)
    .maybeSingle();

  if (planLoadError) throw planLoadError;
  if (!parentPlan) return null;

  const { error: deleteError } = await supabase
    .from('exercise_items')
    .delete()
    .eq('item_id', safeItemId);

  if (deleteError) throw deleteError;

  const { data: remainingItems, error: remainingError } = await supabase
    .from('exercise_items')
    .select('item_id, calories, is_completed')
    .eq('exercise_id', parentPlan.exercise_id);

  if (remainingError) throw remainingError;

  if (!remainingItems || remainingItems.length === 0) {
    await deleteWorkoutPlansByIds(supabase, userId, [parentPlan.exercise_id]);
    return {
      kind: 'exercise-item',
      deleted_item_id: safeItemId,
      deleted_parent: true,
    };
  }

  const totalCalories = remainingItems.reduce(
    (sum, row) => sum + Number(row.calories || 0),
    0
  );
  const completedCount = remainingItems.filter((row) => row.is_completed).length;
  const nextStatus = completedCount === remainingItems.length
    ? 1
    : completedCount > 0
      ? 2
      : 0;

  const { error: updateError } = await supabase
    .from('user_exercise_plans')
    .update({
      total_calories: totalCalories,
      status: nextStatus,
    })
    .eq('user_id', userId)
    .eq('exercise_id', parentPlan.exercise_id);

  if (updateError) throw updateError;

  return {
    kind: 'exercise-item',
    deleted_item_id: safeItemId,
    deleted_parent: false,
    parent_status: nextStatus,
  };
}

async function deletePlanItemByOpaqueId(supabase, userId, itemId = '') {
  const rawItemId = String(itemId || '').trim();
  if (rawItemId.startsWith('exercise-item-')) {
    return deleteExerciseItemById(
      supabase,
      userId,
      Number(rawItemId.replace('exercise-item-', ''))
    );
  }

  if (rawItemId.startsWith('exercise-')) {
    const exerciseId = Number(rawItemId.replace('exercise-', ''));
    if (!Number.isFinite(exerciseId)) return null;

    const { data: plan, error } = await supabase
      .from('user_exercise_plans')
      .select('exercise_id')
      .eq('user_id', userId)
      .eq('exercise_id', exerciseId)
      .maybeSingle();

    if (error) throw error;
    if (!plan) return null;

    await deleteWorkoutPlansByIds(supabase, userId, [exerciseId]);
    return {
      kind: 'exercise',
      deleted_item_id: exerciseId,
      deleted_parent: true,
    };
  }

  if (rawItemId.startsWith('meal-')) {
    const mealId = Number(rawItemId.match(/^meal-(\d+)/)?.[1]);
    if (!Number.isFinite(mealId)) return null;

    const { data: meal, error } = await supabase
      .from('user_meal_plans')
      .select('meal_id')
      .eq('user_id', userId)
      .eq('meal_id', mealId)
      .maybeSingle();

    if (error) throw error;
    if (!meal) return null;

    await deleteDietPlansByIds(supabase, userId, [mealId]);
    return {
      kind: 'meal',
      deleted_item_id: mealId,
      deleted_parent: true,
    };
  }

  return null;
}

async function loadExistingConflictDates(supabase, userId, planType, targetDates = []) {
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

async function loadExistingWorkoutPlanIdsForDates(supabase, userId, targetDates = []) {
  if (targetDates.length === 0) return [];

  const { data, error } = await supabase
    .from('user_exercise_plans')
    .select('exercise_id')
    .eq('user_id', userId)
    .in('target_date', targetDates);

  if (error) throw error;
  return (data || []).map((plan) => plan.exercise_id).filter(Boolean);
}

async function loadExistingDietPlanIdsForDates(supabase, userId, targetDates = []) {
  if (targetDates.length === 0) return [];

  const { data, error } = await supabase
    .from('user_meal_plans')
    .select('meal_id')
    .eq('user_id', userId)
    .in('target_date', targetDates);

  if (error) throw error;
  return (data || []).map((meal) => meal.meal_id).filter(Boolean);
}

async function loadExistingWorkoutPlanIdsForUser(supabase, userId) {
  const { data, error } = await supabase
    .from('user_exercise_plans')
    .select('exercise_id')
    .eq('user_id', userId);

  if (error) throw error;
  return (data || []).map((plan) => plan.exercise_id).filter(Boolean);
}

async function loadExistingDietPlanIdsForUser(supabase, userId) {
  const { data, error } = await supabase
    .from('user_meal_plans')
    .select('meal_id')
    .eq('user_id', userId);

  if (error) throw error;
  return (data || []).map((meal) => meal.meal_id).filter(Boolean);
}

async function createWorkoutPlans(supabase, userId, normalizedItems = []) {
  const createdItems = [];
  const createdExerciseIds = [];

  try {
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
      createdExerciseIds.push(plan.exercise_id);

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
  } catch (error) {
    if (createdExerciseIds.length > 0) {
      try {
        await deleteWorkoutPlansByIds(supabase, userId, createdExerciseIds);
      } catch (rollbackError) {
        error.rollbackError = rollbackError;
      }
    }
    throw error;
  }
}

async function createDietPlans(supabase, userId, normalizedItems = []) {
  const createdItems = [];
  const createdMealIds = [];
  const insertItems = expandDietPlanItems(normalizedItems);

  try {
    for (const item of insertItems) {
      const { data: meal, error } = await supabase
        .from('user_meal_plans')
        .insert(buildDietInsertPayload(userId, item, true))
        .select('*')
        .single();

      if (error && !String(error.message || '').includes('calories')) {
        throw error;
      }

      if (error) {
        const retry = await supabase
          .from('user_meal_plans')
          .insert(buildDietInsertPayload(userId, item, false))
          .select('*')
          .single();

        if (retry.error) throw retry.error;
        createdMealIds.push(retry.data.meal_id);
        createdItems.push(retry.data);
        continue;
      }

      createdMealIds.push(meal.meal_id);
      createdItems.push(meal);
    }

    return createdItems;
  } catch (error) {
    if (createdMealIds.length > 0) {
      try {
        await deleteDietPlansByIds(supabase, userId, createdMealIds);
      } catch (rollbackError) {
        error.rollbackError = rollbackError;
      }
    }
    throw error;
  }
}

async function replaceWorkoutPlans(supabase, userId, normalizedItems = []) {
  const targetDates = dedupeDates(normalizedItems);
  const existingExerciseIds = await loadExistingWorkoutPlanIdsForDates(supabase, userId, targetDates);
  const createdItems = await createWorkoutPlans(supabase, userId, normalizedItems);

  try {
    await deleteWorkoutPlansByIds(supabase, userId, existingExerciseIds);
  } catch (error) {
    const createdExerciseIds = createdItems
      .map((item) => item.plan?.exercise_id)
      .filter(Boolean);

    try {
      await deleteWorkoutPlansByIds(supabase, userId, createdExerciseIds);
    } catch (rollbackError) {
      error.rollbackError = rollbackError;
    }
    throw error;
  }

  return createdItems;
}

async function replaceDietPlans(supabase, userId, normalizedItems = []) {
  const insertItems = expandDietPlanItems(normalizedItems);
  const targetDates = dedupeDates(insertItems);
  const existingMealIds = await loadExistingDietPlanIdsForDates(supabase, userId, targetDates);
  const createdItems = await createDietPlans(supabase, userId, insertItems);

  try {
    await deleteDietPlansByIds(supabase, userId, existingMealIds);
  } catch (error) {
    const createdMealIds = createdItems
      .map((item) => item.meal_id)
      .filter(Boolean);

    try {
      await deleteDietPlansByIds(supabase, userId, createdMealIds);
    } catch (rollbackError) {
      error.rollbackError = rollbackError;
    }
    throw error;
  }

  return createdItems;
}

module.exports = {
  createDietPlans,
  createWorkoutPlans,
  deleteDietPlansByIds,
  deleteDietPlansForDates,
  deleteDietPlansForUser,
  deletePlanItemByOpaqueId,
  deleteWorkoutPlansForDates,
  deleteWorkoutPlansForUser,
  deleteWorkoutPlansByIds,
  loadExistingConflictDates,
  replaceDietPlans,
  replaceWorkoutPlans,
};
