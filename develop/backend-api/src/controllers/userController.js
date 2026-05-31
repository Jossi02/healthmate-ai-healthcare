const supabase = require('../config/db');
const aiEventService = require('../services/aiEventService');
const {
  normalizeProfileRow,
  parseStoredArray,
  serializeArrayField,
  toOptionalNumber,
  toOptionalString,
} = require('../utils/profileFields');
const {
  buildProfileRowForUpsert,
  ensureUserHealthProfile,
  ensureUserHealthProfileRow,
} = require('../services/profileService');
const { loadExercisePlansWithItems } = require('../services/exercisePlanReadService');
const { deletePlanItemByOpaqueId } = require('../services/planMutationService');

const DEFAULT_WORKOUT_COLORS = [
  'from-sky-400 to-blue-500',
  'from-indigo-500 to-purple-600',
  'from-teal-400 to-emerald-500',
  'from-orange-400 to-red-500',
];

function buildProfilePayload(body = {}) {
  const payload = {
    user_id: body.user_id,
    mbti: toOptionalString(body.mbti),
    gender: toOptionalString(body.gender),
    age: toOptionalNumber(body.age),
    height: toOptionalNumber(body.height),
    weight: toOptionalNumber(body.weight),
    goal: toOptionalString(body.goal),
    activity_level: toOptionalString(body.activity_level || body.activityLevel),
    medical_history: body.medical_history !== undefined
      ? serializeArrayField(body.medical_history)
      : undefined,
    allergies: body.allergies !== undefined
      ? serializeArrayField(body.allergies)
      : undefined,
    diet_type: body.diet_type !== undefined
      ? toOptionalString(body.diet_type)
      : undefined,
    injury_history: body.injury_history !== undefined
      ? serializeArrayField(body.injury_history)
      : undefined,
  };

  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== undefined)
  );
}

function computeBmi(weight, height) {
  if (!weight || !height) return null;

  const heightInMeters = height / 100;
  if (!heightInMeters) return null;

  return Number((weight / (heightInMeters * heightInMeters)).toFixed(1));
}

function toMealDisplayType(mealType) {
  if (!mealType) return 'meal';
  return String(mealType);
}

function parseCalories(value) {
  if (value === undefined || value === null || value === '') return 0;
  const matched = String(value).match(/\d+/);
  return matched ? Number(matched[0]) : 0;
}

async function rebuildParentExerciseStatus(exerciseId) {
  const { data: siblings, error: siblingError } = await supabase
    .from('exercise_items')
    .select('is_completed')
    .eq('exercise_id', exerciseId);

  if (siblingError) {
    throw siblingError;
  }

  const total = siblings.length;
  const completedCount = siblings.filter((item) => item.is_completed).length;

  let nextStatus = 0;
  if (total > 0 && completedCount === total) nextStatus = 1;
  else if (completedCount > 0) nextStatus = 2;

  const { error: parentError } = await supabase
    .from('user_exercise_plans')
    .update({ status: nextStatus })
    .eq('exercise_id', exerciseId);

  if (parentError) {
    throw parentError;
  }

  return nextStatus;
}

function mapCalendarToGroupedResponse(exercises = [], meals = []) {
  const grouped = {};

  for (const exercise of exercises) {
    const dateKey = exercise.target_date;
    if (!grouped[dateKey]) {
      grouped[dateKey] = { exercises: [], meals: [] };
    }
    grouped[dateKey].exercises.push(exercise);
  }

  for (const meal of meals) {
    const dateKey = meal.target_date;
    if (!grouped[dateKey]) {
      grouped[dateKey] = { exercises: [], meals: [] };
    }
    grouped[dateKey].meals.push(meal);
  }

  return grouped;
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

async function getOwnedExercisePlan(userId, exerciseId) {
  const { data, error } = await supabase
    .from('user_exercise_plans')
    .select('exercise_id')
    .eq('user_id', userId)
    .eq('exercise_id', exerciseId)
    .maybeSingle();

  if (error) {
    throw error;
  }

  return data;
}

async function getOwnedExerciseItem(userId, itemId) {
  const { data: item, error } = await supabase
    .from('exercise_items')
    .select('item_id, exercise_id, is_completed')
    .eq('item_id', itemId)
    .maybeSingle();

  if (error) {
    throw error;
  }

  if (!item) {
    return null;
  }

  const ownedPlan = await getOwnedExercisePlan(userId, item.exercise_id);
  if (!ownedPlan) {
    return null;
  }

  return item;
}

// @route   GET /api/v1/users/profile
// @desc    Get user health profile
// @access  Private
exports.getProfile = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const profile = await ensureUserHealthProfile(supabase, userId);
    return res.json(profile || normalizeProfileRow({ user_id: userId }));
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to load profile.' });
  }
};

// @route   POST /api/v1/users/profile
// @desc    Upsert general profile fields
// @access  Private
exports.saveProfile = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const incomingProfile = buildProfilePayload({ ...req.body, user_id: userId });
    const nickname = toOptionalString(req.body.nickname);
    const existingProfile = await ensureUserHealthProfileRow(supabase, userId);
    if (!existingProfile) {
      return res.status(404).json({ error: 'User not found.' });
    }

    const profilePayload = buildProfileRowForUpsert(userId, existingProfile, incomingProfile);
    profilePayload.bmi = computeBmi(profilePayload.weight, profilePayload.height) ?? 0;

    const { data: profile, error } = await supabase
      .from('user_health_profiles')
      .upsert(profilePayload, { onConflict: 'user_id' })
      .select('*, users(nickname)')
      .single();

    if (error) throw error;

    if (nickname) {
      const { error: nicknameError } = await supabase
        .from('users')
        .update({ nickname })
        .eq('user_id', userId);

      if (nicknameError) throw nicknameError;
      profile.users = { ...(profile.users || {}), nickname };
    }

    const changedFields = Object.keys(incomingProfile).filter((field) => field !== 'user_id');
    if (nickname) {
      changedFields.push('nickname');
    }
    await aiEventService.notifyProfileUpdated(userId, [...new Set(changedFields)]);

    const normalized = normalizeProfileRow(profile);
    return res.json({
      message: 'Profile saved successfully.',
      profile: normalized,
      bmi: normalized.bmi ?? null,
    });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to save profile.' });
  }
};

// @route   PATCH /api/v1/users/:user_id/settings/persona
// @desc    Update selected_ai_persona via dedicated settings API
// @access  Private
exports.updatePersonaSetting = async (req, res) => {
  try {
    const requesterUserId = req.user.user_id;
    const { user_id: targetUserId } = req.params;
    const selectedAiPersona = toOptionalString(req.body.selected_ai_persona);

    if (requesterUserId !== targetUserId) {
      return res.status(403).json({ error: 'Forbidden.' });
    }

    if (!selectedAiPersona) {
      return res.status(400).json({ error: 'selected_ai_persona is required.' });
    }

    const existingProfile = await ensureUserHealthProfileRow(supabase, targetUserId);
    if (!existingProfile) {
      return res.status(404).json({ error: 'User not found.' });
    }

    const profilePayload = buildProfileRowForUpsert(targetUserId, existingProfile, {
      selected_ai_persona: selectedAiPersona,
    });

    const { data: profile, error } = await supabase
      .from('user_health_profiles')
      .upsert(profilePayload, { onConflict: 'user_id' })
      .select('user_id, selected_ai_persona')
      .single();

    if (error) throw error;

    await aiEventService.notifyProfileUpdated(targetUserId, ['selected_ai_persona']);

    return res.json({
      message: 'Persona setting saved successfully.',
      setting: profile,
    });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to update persona setting.' });
  }
};

// @route   GET /api/v1/users/calendar
// @desc    Get exercise and meal plans grouped by date
// @access  Private
exports.getCalendar = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const startDate = req.query.start_date;
    const endDate = req.query.end_date;

    if (!startDate || !endDate) {
      return res.status(400).json({ error: 'start_date and end_date are required.' });
    }

    const exercises = await loadExercisePlansWithItems(supabase, {
      userId,
      startDate,
      endDate,
    });

    const { data: meals, error: mealError } = await supabase
      .from('user_meal_plans')
      .select('*')
      .eq('user_id', userId)
      .gte('target_date', startDate)
      .lte('target_date', endDate)
      .order('target_date', { ascending: true })
      .order('created_at', { ascending: true });

    if (mealError) throw mealError;

    return res.json(mapCalendarToGroupedResponse(exercises || [], meals || []));
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to load calendar data.' });
  }
};

// @route   PUT /api/v1/users/plans/check
// @desc    Mark one plan item as completed using opaque item_id
// @access  Private
exports.checkTodayPlanItem = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const parsed = parsePlanCheckId(req.body.item_id || '');

    if (!parsed || !Number.isFinite(parsed.numericId)) {
      return res.status(400).json({ error: 'Invalid item_id format.' });
    }

    if (parsed.kind === 'exercise-item') {
      const ownedItem = await getOwnedExerciseItem(userId, parsed.numericId);
      if (!ownedItem) {
        return res.status(404).json({ error: 'Exercise item not found.' });
      }

      const { data: updatedItem, error } = await supabase
        .from('exercise_items')
        .update({ is_completed: true })
        .eq('item_id', parsed.numericId)
        .select('item_id, exercise_id')
        .single();

      if (error) throw error;

      const parentStatus = await rebuildParentExerciseStatus(updatedItem.exercise_id);
      return res.json({
        status: 'success',
        item_id: req.body.item_id,
        checked: true,
        parent_status: parentStatus,
      });
    }

    if (parsed.kind === 'exercise') {
      const ownedPlan = await getOwnedExercisePlan(userId, parsed.numericId);
      if (!ownedPlan) {
        return res.status(404).json({ error: 'Exercise plan not found.' });
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
        return res.status(404).json({ error: 'Exercise plan not found.' });
      }

      const { error: childrenError } = await supabase
        .from('exercise_items')
        .update({ is_completed: true })
        .eq('exercise_id', parsed.numericId);

      if (childrenError) throw childrenError;

      return res.json({
        status: 'success',
        item_id: req.body.item_id,
        checked: true,
        parent_status: 1,
      });
    }

    if (parsed.kind === 'meal') {
      const { data: updatedMeal, error } = await supabase
        .from('user_meal_plans')
        .update({ is_completed: true })
        .eq('meal_id', parsed.numericId)
        .eq('user_id', userId)
        .select('meal_id')
        .maybeSingle();

      if (error) throw error;
      if (!updatedMeal) {
        return res.status(404).json({ error: 'Meal plan not found.' });
      }

      return res.json({
        status: 'success',
        item_id: req.body.item_id,
        checked: true,
      });
    }

    return res.status(400).json({ error: 'Unsupported item type.' });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to check plan item.' });
  }
};

// @route   DELETE /api/v1/users/plans/:item_id
// @desc    Delete one workout or diet plan item owned by the current user
// @access  Private
exports.deletePlanItem = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const itemId = toOptionalString(req.params.item_id);
    if (!itemId) {
      return res.status(400).json({ error: 'item_id is required.' });
    }

    const deleted = await deletePlanItemByOpaqueId(supabase, userId, itemId);
    if (!deleted) {
      return res.status(404).json({ error: 'Plan item not found.' });
    }

    return res.json({
      status: 'success',
      item_id: itemId,
      deleted,
    });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to delete plan item.' });
  }
};

// @route   PUT /api/v1/users/exercises/items/:item_id
// @desc    Update exercise item completion
// @access  Private
exports.updateExerciseItem = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const itemId = Number(req.params.item_id);
    const isCompleted = Boolean(req.body.is_completed);

    if (!Number.isFinite(itemId)) {
      return res.status(400).json({ error: 'Invalid exercise item id.' });
    }

    const ownedItem = await getOwnedExerciseItem(userId, itemId);
    if (!ownedItem) {
      return res.status(404).json({ error: 'Exercise item not found.' });
    }

    const { data: updatedItem, error } = await supabase
      .from('exercise_items')
      .update({ is_completed: isCompleted })
      .eq('item_id', itemId)
      .select('*')
      .single();

    if (error) throw error;

    const parentStatus = await rebuildParentExerciseStatus(updatedItem.exercise_id);
    return res.json({
      message: 'Exercise item updated successfully.',
      item: updatedItem,
      parent_status: parentStatus,
    });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to update exercise item.' });
  }
};

// @route   PUT /api/v1/users/meals/:id
// @desc    Update meal completion
// @access  Private
exports.updateMealStatus = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const mealId = Number(req.params.id);
    const isCompleted = Boolean(req.body.is_completed);

    if (!Number.isFinite(mealId)) {
      return res.status(400).json({ error: 'Invalid meal id.' });
    }

    const { data: updatedMeal, error } = await supabase
      .from('user_meal_plans')
      .update({ is_completed: isCompleted })
      .eq('meal_id', mealId)
      .eq('user_id', userId)
      .select('*')
      .maybeSingle();

    if (error) throw error;
    if (!updatedMeal) {
      return res.status(404).json({ error: 'Meal plan not found.' });
    }

    return res.json({
      message: 'Meal status updated successfully.',
      meal: updatedMeal,
    });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to update meal status.' });
  }
};

// @route   POST /api/v1/users/exercises/recommend-add
// @desc    Add recommended exercise item to a date
// @access  Private
exports.addRecommendedExercise = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const targetDate = toOptionalString(req.body.target_date);
    const exerciseType = toOptionalString(req.body.exercise_type) || 'recommended';
    const exerciseName = toOptionalString(req.body.exercise_name);
    const calories = parseCalories(req.body.calories);
    const targetSets = toOptionalNumber(req.body.target_sets);
    const durationMinutes = toOptionalNumber(req.body.duration_minutes);

    if (!targetDate || !exerciseName) {
      return res.status(400).json({ error: 'target_date and exercise_name are required.' });
    }

    const { data: parentPlan, error: parentError } = await supabase
      .from('user_exercise_plans')
      .select('*')
      .eq('user_id', userId)
      .eq('target_date', targetDate)
      .eq('exercise_type', exerciseType)
      .maybeSingle();

    if (parentError) throw parentError;

    let plan = parentPlan;
    if (!plan) {
      const { data: createdPlan, error } = await supabase
        .from('user_exercise_plans')
        .insert({
          user_id: userId,
          exercise_type: exerciseType,
          total_calories: calories,
          status: 0,
          target_date: targetDate,
        })
        .select('*')
        .single();

      if (error) throw error;
      plan = createdPlan;
    } else {
      const { data: existingItem, error: existingItemError } = await supabase
        .from('exercise_items')
        .select('*')
        .eq('exercise_id', plan.exercise_id)
        .eq('exercise_name', exerciseName)
        .maybeSingle();

      if (existingItemError) throw existingItemError;
      if (existingItem) {
        return res.json({
          message: 'Recommended exercise already exists.',
          already_exists: true,
          parent_plan: plan,
          item: existingItem,
        });
      }

      const { data: updatedPlan, error } = await supabase
        .from('user_exercise_plans')
        .update({
          total_calories: Number(plan.total_calories || 0) + calories,
        })
        .eq('exercise_id', plan.exercise_id)
        .select('*')
        .single();

      if (error) throw error;
      plan = updatedPlan;
    }

    const { data: item, error: itemError } = await supabase
      .from('exercise_items')
      .insert({
        exercise_id: plan.exercise_id,
        exercise_name: exerciseName,
        calories,
        target_sets: targetSets,
        duration_minutes: durationMinutes,
        is_completed: false,
      })
      .select('*')
      .single();

    if (itemError) throw itemError;

    return res.json({
      message: 'Recommended exercise added successfully.',
      parent_plan: plan,
      item,
    });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to add recommended exercise.' });
  }
};

// @route   PUT /api/v1/users/meals/recommend-replace
// @desc    Replace a meal recommendation for a date/meal slot
// @access  Private
exports.replaceRecommendedMeal = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const targetDate = toOptionalString(req.body.target_date);
    const mealType = toOptionalString(req.body.meal_type);
    const foodName = toOptionalString(req.body.food_name);
    const calories = parseCalories(req.body.calories);

    if (!targetDate || !mealType || !foodName) {
      return res.status(400).json({ error: 'target_date, meal_type and food_name are required.' });
    }

    const { data: existingMeal, error: existingError } = await supabase
      .from('user_meal_plans')
      .select('meal_id')
      .eq('user_id', userId)
      .eq('target_date', targetDate)
      .eq('meal_type', mealType)
      .maybeSingle();

    if (existingError) throw existingError;

    let meal;
    if (existingMeal) {
      const { data: updatedMeal, error } = await supabase
        .from('user_meal_plans')
        .update({
          food_name: foodName,
          calories,
        })
        .eq('meal_id', existingMeal.meal_id)
        .select('*')
        .single();

      if (error) throw error;
      meal = updatedMeal;
    } else {
      const { data: createdMeal, error } = await supabase
        .from('user_meal_plans')
        .insert({
          user_id: userId,
          target_date: targetDate,
          meal_type: mealType,
          food_name: foodName,
          calories,
          is_completed: false,
        })
        .select('*')
        .single();

      if (error) throw error;
      meal = createdMeal;
    }

    return res.json({
      message: 'Recommended meal replaced successfully.',
      meal: {
        ...meal,
        meal_type: toMealDisplayType(meal.meal_type),
      },
    });
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Failed to replace recommended meal.' });
  }
};

exports.__private__ = {
  buildProfilePayload,
  computeBmi,
  mapCalendarToGroupedResponse,
  parsePlanCheckId,
  parseStoredArray,
  serializeArrayField,
  DEFAULT_WORKOUT_COLORS,
};
