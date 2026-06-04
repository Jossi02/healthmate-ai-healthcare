export type WorkoutSlot = "upper_body" | "lower_body" | "cardio" | "stretching";
export type DietSlot = "breakfast" | "lunch" | "dinner";
export type RecommendationScope = "all" | "workout" | "diet";

export type WorkoutRecommendation = {
  exercise_name: string;
  summary: string;
  sets?: number | null;
  duration_minutes?: number | null;
  calories: number;
};

export type DietRecommendation = {
  food_name: string;
  summary: string;
  calories: number;
};

export type HomeRecommendations = {
  date: string;
  scope: RecommendationScope;
  workout: Record<WorkoutSlot, WorkoutRecommendation | null>;
  diet: Record<DietSlot, DietRecommendation | null>;
  quality_flags?: Record<string, unknown>;
};

export type RecommendationAddedState = {
  workout: Record<WorkoutSlot, boolean>;
  diet: Record<DietSlot, boolean>;
};

export type RecommendationHistoryState = {
  workout: Record<WorkoutSlot, string[]>;
  diet: Record<DietSlot, string[]>;
};

export type HomeRecommendationCache = {
  recommendations: HomeRecommendations;
  added: RecommendationAddedState;
  history: RecommendationHistoryState;
};

export const WORKOUT_SLOTS: WorkoutSlot[] = [
  "upper_body",
  "lower_body",
  "cardio",
  "stretching",
];

export const DIET_SLOTS: DietSlot[] = ["breakfast", "lunch", "dinner"];

export function createEmptyAddedState(): RecommendationAddedState {
  return {
    workout: {
      upper_body: false,
      lower_body: false,
      cardio: false,
      stretching: false,
    },
    diet: {
      breakfast: false,
      lunch: false,
      dinner: false,
    },
  };
}

export function createEmptyRecommendationHistory(): RecommendationHistoryState {
  return {
    workout: {
      upper_body: [],
      lower_body: [],
      cardio: [],
      stretching: [],
    },
    diet: {
      breakfast: [],
      lunch: [],
      dinner: [],
    },
  };
}

export function createEmptyRecommendations(date: string): HomeRecommendations {
  return {
    date,
    scope: "all",
    workout: {
      upper_body: null,
      lower_body: null,
      cardio: null,
      stretching: null,
    },
    diet: {
      breakfast: null,
      lunch: null,
      dinner: null,
    },
    quality_flags: {},
  };
}

export function getHomeRecommendationCacheKey(userId: string, date: string) {
  return `healthAppHomeRecommendations:${userId}:${date}`;
}

export function mergeRecommendations(
  current: HomeRecommendations,
  incoming: HomeRecommendations,
  scope: RecommendationScope
): HomeRecommendations {
  const next =
    scope === "workout"
      ? {
          ...current,
          date: incoming.date,
          workout: incoming.workout,
          quality_flags: incoming.quality_flags ?? current.quality_flags,
        }
      : scope === "diet"
        ? {
            ...current,
            date: incoming.date,
            diet: incoming.diet,
            quality_flags: incoming.quality_flags ?? current.quality_flags,
          }
        : incoming;

  return {
    ...next,
    scope: resolveRecommendationScope(next),
  };
}

export function hasAnyWorkoutRecommendations(recommendations: HomeRecommendations) {
  return WORKOUT_SLOTS.some((slot) => recommendations.workout[slot] !== null);
}

export function hasAnyDietRecommendations(recommendations: HomeRecommendations) {
  return DIET_SLOTS.some((slot) => recommendations.diet[slot] !== null);
}

export function resolveRecommendationScope(
  recommendations: HomeRecommendations
): RecommendationScope {
  const hasWorkout = hasAnyWorkoutRecommendations(recommendations);
  const hasDiet = hasAnyDietRecommendations(recommendations);

  if (hasWorkout && hasDiet) {
    return "all";
  }

  if (hasWorkout) {
    return "workout";
  }

  if (hasDiet) {
    return "diet";
  }

  return "all";
}

export function isRecommendationScopeComplete(
  recommendations: HomeRecommendations,
  scope: RecommendationScope
) {
  if (scope === "all" || scope === "workout") {
    const hasMissingWorkout = WORKOUT_SLOTS.some(
      (slot) => recommendations.workout[slot] === null
    );
    if (hasMissingWorkout) {
      return false;
    }
  }

  if (scope === "all" || scope === "diet") {
    const hasMissingDiet = DIET_SLOTS.some(
      (slot) => recommendations.diet[slot] === null
    );
    if (hasMissingDiet) {
      return false;
    }
  }

  return true;
}

export function resetAddedStateForScope(
  current: RecommendationAddedState,
  scope: RecommendationScope
): RecommendationAddedState {
  if (scope === "workout") {
    return {
      ...current,
      workout: createEmptyAddedState().workout,
    };
  }

  if (scope === "diet") {
    return {
      ...current,
      diet: createEmptyAddedState().diet,
    };
  }

  return createEmptyAddedState();
}

function normalizeRecommendationName(name: string | undefined | null) {
  return String(name || "")
    .trim()
    .toLowerCase();
}

export function appendRecommendationHistory(
  current: RecommendationHistoryState,
  incoming: HomeRecommendations,
  scope: RecommendationScope,
  maxPerSlot = 3
): RecommendationHistoryState {
  const next: RecommendationHistoryState = {
    workout: {
      upper_body: [...current.workout.upper_body],
      lower_body: [...current.workout.lower_body],
      cardio: [...current.workout.cardio],
      stretching: [...current.workout.stretching],
    },
    diet: {
      breakfast: [...current.diet.breakfast],
      lunch: [...current.diet.lunch],
      dinner: [...current.diet.dinner],
    },
  };

  if (scope === "all" || scope === "workout") {
    for (const slot of WORKOUT_SLOTS) {
      const item = incoming.workout[slot];
      const normalizedName = normalizeRecommendationName(item?.exercise_name);
      if (!normalizedName) continue;
      const history = next.workout[slot].filter((entry) => entry !== normalizedName);
      history.push(normalizedName);
      next.workout[slot] = history.slice(-maxPerSlot);
    }
  }

  if (scope === "all" || scope === "diet") {
    for (const slot of DIET_SLOTS) {
      const item = incoming.diet[slot];
      const normalizedName = normalizeRecommendationName(item?.food_name);
      if (!normalizedName) continue;
      const history = next.diet[slot].filter((entry) => entry !== normalizedName);
      history.push(normalizedName);
      next.diet[slot] = history.slice(-maxPerSlot);
    }
  }

  return next;
}

export function hasRecommendationHistoryCollision(
  history: RecommendationHistoryState,
  incoming: HomeRecommendations,
  scope: RecommendationScope
) {
  if (scope === "all" || scope === "workout") {
    for (const slot of WORKOUT_SLOTS) {
      const normalizedName = normalizeRecommendationName(incoming.workout[slot]?.exercise_name);
      if (normalizedName && history.workout[slot].includes(normalizedName)) {
        return true;
      }
    }
  }

  if (scope === "all" || scope === "diet") {
    for (const slot of DIET_SLOTS) {
      const normalizedName = normalizeRecommendationName(incoming.diet[slot]?.food_name);
      if (normalizedName && history.diet[slot].includes(normalizedName)) {
        return true;
      }
    }
  }

  return false;
}
