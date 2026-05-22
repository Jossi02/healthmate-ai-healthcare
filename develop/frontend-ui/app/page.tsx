"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Calendar, CheckCircle2, CheckSquare, Plus, Trash2, X, Droplets, Utensils, Activity, RefreshCw, Heart, Info, Dumbbell, PersonStanding } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { usePlan } from './context/PlanContext';
import { formatKstDate, formatKstDisplayDate } from '@/lib/date';
import {
  createEmptyAddedState,
  createEmptyRecommendationHistory,
  createEmptyRecommendations,
  DIET_SLOTS,
  getHomeRecommendationCacheKey,
  hasRecommendationHistoryCollision,
  isRecommendationScopeComplete,
  mergeRecommendations,
  resetAddedStateForScope,
  type DietRecommendation,
  type DietSlot,
  type HomeRecommendations,
  type RecommendationAddedState,
  type RecommendationHistoryState,
  type RecommendationScope,
  type WorkoutRecommendation,
  type WorkoutSlot,
  appendRecommendationHistory,
} from '@/lib/homeRecommendations';
import { compactText } from '@/lib/planDisplay';

export type TodoItem = {
  id: number;
  text: string;
  completed: boolean;
  type?: 'workout' | 'diet' | 'general';
  mealType?: 'breakfast' | 'lunch' | 'dinner';
  calories?: number;
  img?: string;
};

type LegacyWorkoutCard = WorkoutRecommendation & {
  id: string;
  name: string;
  desc: string;
  duration: string;
  slot: WorkoutSlot;
};

type LegacyDietCard = DietRecommendation & {
  id: string;
  name: string;
  desc: string;
  slot: DietSlot;
};

type LegacyRecommendations = {
  workout: {
    strength: {
      upper: LegacyWorkoutCard | null;
      lower: LegacyWorkoutCard | null;
    };
    cardio: LegacyWorkoutCard | null;
    stretching: LegacyWorkoutCard | null;
  };
  diet: Record<DietSlot, LegacyDietCard | null>;
};

type WorkoutPopupState = {
  isOpen: boolean;
  target: LegacyWorkoutCard | null;
};

type DietPopupState = {
  isOpen: boolean;
  target: LegacyDietCard | null;
  mealType: DietSlot | null;
};

type RequestedRecommendationScope = Exclude<RecommendationScope, 'all'>;
type HomeRecommendationHighlightId = `workout:${WorkoutSlot}` | `diet:${DietSlot}`;
type PlanSyncToastState = {
  isOpen: boolean;
  title: string;
  message: string;
};

const getWorkoutRecommendationHighlightId = (
  slot: WorkoutSlot
): HomeRecommendationHighlightId => `workout:${slot}`;

const getDietRecommendationHighlightId = (
  mealType: DietSlot
): HomeRecommendationHighlightId => `diet:${mealType}`;

const isRecommendationHighlightInScope = (
  id: HomeRecommendationHighlightId,
  scope: RecommendationScope
) => {
  if (scope === 'all') return true;
  return id.startsWith(`${scope}:`);
};

const getFoodEmoji = (name: string) => {
  if (name.includes('샐러드') || name.includes('야채')) return '🥗';
  if (name.includes('스테이크') || name.includes('고기') || name.includes('장조림')) return '🍖';
  if (name.includes('쉐이크') || name.includes('우유')) return '🥛';
  if (name.includes('밥') || name.includes('국') || name.includes('찌개') || name.includes('된장')) return '🍚';
  if (name.includes('파스타') || name.includes('면') || name.includes('소바') || name.includes('우동')) return '🍝';
  if (name.includes('샌드위치') || name.includes('빵') || name.includes('오트밀')) return '🥪';
  if (name.includes('계란') || name.includes('에그') || name.includes('낫토')) return '🍳';
  if (name.includes('연어') || name.includes('초밥')) return '🍣';
  if (name.includes('요거트') || name.includes('보울')) return '🥣';
  if (name.includes('닭')) return '🍗';
  return '🥗';
};

const getApiBaseUrl = () => {
  const raw = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || '';
  return raw.endsWith('/') ? raw.slice(0, -1) : raw;
};

const buildApiUrl = (path: string) => {
  const baseUrl = getApiBaseUrl();
  return baseUrl ? `${baseUrl}${path}` : path;
};

const getHomeRecommendationApiPath = (scope: RequestedRecommendationScope) => {
  if (scope === 'workout') {
    return '/api/v1/home/recommendations/workout';
  }

  return '/api/v1/home/recommendations/diet';
};

const getAuthHeaders = () => {
  const token = typeof window !== 'undefined' ? localStorage.getItem('healthAppToken') : null;

  return {
    'Content-Type': 'application/json',
    'ngrok-skip-browser-warning': 'true',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
};

const getWorkoutSlotLabel = (slot: WorkoutSlot) => {
  switch (slot) {
    case 'upper_body':
      return '상체 운동';
    case 'lower_body':
      return '하체 운동';
    case 'cardio':
      return '유산소';
    case 'stretching':
      return '스트레칭';
    default:
      return '운동';
  }
};

const getWorkoutSlotColor = (slot: WorkoutSlot) => {
  switch (slot) {
    case 'upper_body':
      return 'from-blue-500 to-indigo-600';
    case 'lower_body':
      return 'from-indigo-500 to-purple-600';
    case 'cardio':
      return 'from-rose-500 to-pink-600';
    case 'stretching':
      return 'from-emerald-500 to-teal-600';
    default:
      return 'from-blue-500 to-indigo-600';
  }
};

const formatWorkoutPrescription = (item: WorkoutRecommendation | null) => {
  if (!item) return '추천';
  if (item.duration_minutes) return `${item.duration_minutes}분`;
  if (item.sets) return `${item.sets}세트`;
  return '추천';
};

const matchesMealSlot = (savedType: string | undefined, mealType: DietSlot) => {
  const normalized = String(savedType || '').trim().toLowerCase();
  if (mealType === 'breakfast') {
    return normalized === '아침' || normalized === 'breakfast';
  }
  if (mealType === 'lunch') {
    return normalized === '점심' || normalized === 'lunch';
  }
  return normalized === '저녁' || normalized === 'dinner';
};

const createLegacyRecommendations = (): LegacyRecommendations => ({
  workout: {
    strength: {
      upper: { id: 's1', name: '푸시업', desc: '가슴/삼두근 강화', duration: '15분', exercise_name: '푸시업', summary: '가슴/삼두근 강화', calories: 150, slot: 'upper_body' },
      lower: { id: 's2', name: '스쿼트', desc: '하체/코어 단련', duration: '20분', exercise_name: '스쿼트', summary: '하체/코어 단련', calories: 180, slot: 'lower_body' },
    },
    cardio: { id: 'c1', name: '가벼운 조깅', desc: '심폐지구력 향상', duration: '30분', exercise_name: '가벼운 조깅', summary: '심폐지구력 향상', calories: 220, slot: 'cardio', duration_minutes: 30 },
    stretching: { id: 'st1', name: '전신 이완 요가', desc: '근육 긴장 완화', duration: '10분', exercise_name: '전신 이완 요가', summary: '근육 긴장 완화', calories: 80, slot: 'stretching' },
  },
  diet: {
    breakfast: { id: 'd1', name: '오트밀과 과일', desc: '식이섬유 가득', calories: 350, food_name: '오트밀과 과일', summary: '식이섬유 가득', slot: 'breakfast' },
    lunch: { id: 'd2', name: '닭가슴살 샐러드', desc: '단백질 보충', calories: 450, food_name: '닭가슴살 샐러드', summary: '단백질 보충', slot: 'lunch' },
    dinner: { id: 'd3', name: '연어 스테이크', desc: '건강한 지방', calories: 500, food_name: '연어 스테이크', summary: '건강한 지방', slot: 'dinner' },
  },
});

const mapRecommendationsToLegacyCards = (
  recommendations: HomeRecommendations
): LegacyRecommendations => ({
  workout: {
    strength: {
      upper: recommendations.workout.upper_body
        ? {
            ...recommendations.workout.upper_body,
            id: 'upper_body',
            name: recommendations.workout.upper_body.exercise_name,
            desc: recommendations.workout.upper_body.summary,
            duration: formatWorkoutPrescription(recommendations.workout.upper_body),
            slot: 'upper_body',
          }
        : null,
      lower: recommendations.workout.lower_body
        ? {
            ...recommendations.workout.lower_body,
            id: 'lower_body',
            name: recommendations.workout.lower_body.exercise_name,
            desc: recommendations.workout.lower_body.summary,
            duration: formatWorkoutPrescription(recommendations.workout.lower_body),
            slot: 'lower_body',
          }
        : null,
    },
    cardio: recommendations.workout.cardio
      ? {
          ...recommendations.workout.cardio,
          id: 'cardio',
          name: recommendations.workout.cardio.exercise_name,
          desc: recommendations.workout.cardio.summary,
          duration: formatWorkoutPrescription(recommendations.workout.cardio),
          slot: 'cardio',
        }
      : null,
    stretching: recommendations.workout.stretching
      ? {
          ...recommendations.workout.stretching,
          id: 'stretching',
          name: recommendations.workout.stretching.exercise_name,
          desc: recommendations.workout.stretching.summary,
          duration: formatWorkoutPrescription(recommendations.workout.stretching),
          slot: 'stretching',
        }
      : null,
  },
  diet: {
    breakfast: recommendations.diet.breakfast
      ? {
          ...recommendations.diet.breakfast,
          id: 'breakfast',
          name: recommendations.diet.breakfast.food_name,
          desc: recommendations.diet.breakfast.summary,
          slot: 'breakfast',
        }
      : null,
    lunch: recommendations.diet.lunch
      ? {
          ...recommendations.diet.lunch,
          id: 'lunch',
          name: recommendations.diet.lunch.food_name,
          desc: recommendations.diet.lunch.summary,
          slot: 'lunch',
        }
      : null,
    dinner: recommendations.diet.dinner
      ? {
          ...recommendations.diet.dinner,
          id: 'dinner',
          name: recommendations.diet.dinner.food_name,
          desc: recommendations.diet.dinner.summary,
          slot: 'dinner',
        }
      : null,
  },
});

export default function Home() {
  const router = useRouter();
  const { addWorkout, replaceDiet, getPlanByDate, userData, isUserLoading } = usePlan();
  const [isClient, setIsClient] = useState(false);
  const [isPlannerOpen, setIsPlannerOpen] = useState(false);
  const [currentDate, setCurrentDate] = useState('');

  // Calorie & Nutrient Modals state
  const [isCalorieModalOpen, setIsCalorieModalOpen] = useState(false);
  const [isNutrientModalOpen, setIsNutrientModalOpen] = useState(false);
  const [goalInput, setGoalInput] = useState({
    calories: '',
    carbs: '',
    protein: '',
    fat: '',
  });
  const [goalErrorMsg, setGoalErrorMsg] = useState('');
  const [isGoalSaving, setIsGoalSaving] = useState(false);

  // Targets (displayed in UI)
  const [targets, setTargets] = useState({
    calories: 2000,
    carbs: 250,
    protein: 80,
    fat: 50
  });

  // Current Intakes (mock for UI initially, gets updated by AI)
  const [resetConfirmModal, setResetConfirmModal] = useState<{ isOpen: boolean, target: 'calories' | 'macros' | null }>({ isOpen: false, target: null });
  const [intakes, setIntakes] = useState({
    calories: 0,
    carbs: 0,
    protein: 0,
    fat: 0
  });

  // Calculated Fallback
  const [recommendedCalories, setRecommendedCalories] = useState(2000);

  const closeAllModals = () => {
    setIsPlannerOpen(false);
    setIsCalorieModalOpen(false);
    setIsNutrientModalOpen(false);
    setIsDietModalOpen(false);
    setWorkoutPopup({ isOpen: false, target: null });
    setDietPopup({ isOpen: false, target: null, mealType: null });
  };

  const handleCalorieSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setGoalErrorMsg('');

    const cal = Number(goalInput.calories);
    if (isNaN(cal) || cal <= 0 || cal >= 10000) {
      return setGoalErrorMsg('올바른 범위를 입력해주세요 (1~9999)');
    }

    setIsGoalSaving(true);
    try {
      const nextTargets = { ...targets, calories: cal };
      setTargets(nextTargets);
      persistNutritionSnapshot(nextTargets, intakes);
      setIsCalorieModalOpen(false);
    } catch (err) {
      console.error(err);
      setGoalErrorMsg('저장에 실패했습니다.');
    } finally {
      setIsGoalSaving(false);
    }
  };

  const handleNutrientSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setGoalErrorMsg('');

    const car = Number(goalInput.carbs);
    const pro = Number(goalInput.protein);
    const fa = Number(goalInput.fat);

    if (isNaN(car) || car <= 0 || car >= 10000 ||
      isNaN(pro) || pro <= 0 || pro >= 10000 ||
      isNaN(fa) || fa <= 0 || fa >= 10000) {
      return setGoalErrorMsg('올바른 범위를 입력해주세요 (1~9999)');
    }

    setIsGoalSaving(true);
    try {
      const nextTargets = {
        ...targets,
        carbs: car,
        protein: pro,
        fat: fa
      };
      setTargets(nextTargets);
      persistNutritionSnapshot(nextTargets, intakes);
      setIsNutrientModalOpen(false);
    } catch (err) {
      console.error(err);
      setGoalErrorMsg('저장에 실패했습니다.');
    } finally {
      setIsGoalSaving(false);
    }
  };

  // Diet Modal state
  const [isDietModalOpen, setIsDietModalOpen] = useState(false);
  const [manualInput, setManualInput] = useState({ calories: '', carbs: '', protein: '', fat: '' });
  const [dietAnalysis, setDietAnalysis] = useState<{ calories: number, carbs: number, protein: number, fat: number, message: string } | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  // Water tracker state (glasses of water)
  const [waterGlasses, setWaterGlasses] = useState(2);
  const maxWaterGlasses = 8;
  const [showWaterGoalAnim, setShowWaterGoalAnim] = useState(false);

  const [todos, setTodos] = useState<TodoItem[]>([
    { id: 1, text: '아침 스트레칭 10분', completed: false, type: 'workout' },
    { id: 2, text: '비타민 챙겨먹기', completed: false, type: 'general' },
    { id: 3, text: '기존 아침 식단 (사과 등)', completed: false, type: 'diet', mealType: 'breakfast', calories: 150 }
  ]);
  const [newTodo, setNewTodo] = useState('');

  const [aiRecommendations, setAiRecommendations] = useState<LegacyRecommendations>(
    createLegacyRecommendations
  );

  // 팝업 모달 상태
  const [homeRecommendationsRaw, setHomeRecommendationsRaw] = useState<HomeRecommendations>(
    () => createEmptyRecommendations(formatKstDate())
  );
  const [recommendationAdded, setRecommendationAdded] = useState<RecommendationAddedState>(
    () => createEmptyAddedState()
  );
  const [recommendationHistory, setRecommendationHistory] = useState<RecommendationHistoryState>(
    () => createEmptyRecommendationHistory()
  );
  const [isRecommendationLoading, setIsRecommendationLoading] = useState(false);
  const [recommendationRefresh, setRecommendationRefresh] = useState({
    workout: false,
    diet: false,
  });
  const [highlightedRecommendationIds, setHighlightedRecommendationIds] =
    useState<HomeRecommendationHighlightId[]>([]);
  const [planSyncToast, setPlanSyncToast] = useState<PlanSyncToastState>({
    isOpen: false,
    title: '',
    message: '',
  });

  const [workoutPopup, setWorkoutPopup] = useState<WorkoutPopupState>({ isOpen: false, target: null });
  const [dietPopup, setDietPopup] = useState<DietPopupState>({ isOpen: false, target: null, mealType: null });
  const [alertPopup, setAlertPopup] = useState<{ isOpen: boolean, message: string }>({ isOpen: false, message: '' });
  const homeRecommendationsRawRef = useRef(homeRecommendationsRaw);
  const recommendationAddedRef = useRef(recommendationAdded);
  const recommendationHistoryRef = useRef(recommendationHistory);
  const isMountedRef = useRef(false);
  const isLeavingHomeRef = useRef(false);
  const recommendationAbortControllersRef = useRef<Set<AbortController>>(new Set());
  const planSyncToastTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const persistNutritionSnapshot = (
    nextTargets = targets,
    nextIntakes = intakes,
    date = formatKstDate()
  ) => {
    if (typeof window === 'undefined') return;

    localStorage.setItem('healthAppNutrition', JSON.stringify({
      date,
      targetKcal: nextTargets.calories,
      consumedKcal: nextIntakes.calories,
      targetMacros: nextTargets,
      consumedMacros: {
        carbs: nextIntakes.carbs,
        protein: nextIntakes.protein,
        fat: nextIntakes.fat
      }
    }));
  };

  const persistHomeRecommendationCache = useCallback((
    recommendations: HomeRecommendations,
    added: RecommendationAddedState,
    history: RecommendationHistoryState
  ) => {
    if (typeof window === 'undefined' || !userData?.user_id) return;

    localStorage.setItem(
      getHomeRecommendationCacheKey(userData.user_id, recommendations.date),
      JSON.stringify({
        recommendations,
        added,
        history,
      })
    );
  }, [userData?.user_id]);

  const registerRecommendationAbortController = useCallback((controller: AbortController) => {
    recommendationAbortControllersRef.current.add(controller);

    return () => {
      recommendationAbortControllersRef.current.delete(controller);
    };
  }, []);

  const markRecommendationHighlighted = useCallback(
    (id: HomeRecommendationHighlightId) => {
      setHighlightedRecommendationIds((previous) =>
        previous.includes(id) ? previous : [...previous, id]
      );
    },
    []
  );

  const dismissRecommendationHighlight = useCallback(
    (id: HomeRecommendationHighlightId) => {
      setHighlightedRecommendationIds((previous) =>
        previous.includes(id)
          ? previous.filter((highlightId) => highlightId !== id)
          : previous
      );
    },
    []
  );

  const clearRecommendationHighlightsForScope = useCallback(
    (scope: RecommendationScope) => {
      setHighlightedRecommendationIds((previous) =>
        previous.filter((id) => !isRecommendationHighlightInScope(id, scope))
      );
    },
    []
  );

  const hidePlanSyncToast = useCallback(() => {
    if (planSyncToastTimeoutRef.current) {
      clearTimeout(planSyncToastTimeoutRef.current);
      planSyncToastTimeoutRef.current = null;
    }

    setPlanSyncToast((previous) => ({ ...previous, isOpen: false }));
  }, []);

  const showPlanSyncToast = useCallback(
    (kind: 'workout' | 'diet', itemName: string) => {
      if (planSyncToastTimeoutRef.current) {
        clearTimeout(planSyncToastTimeoutRef.current);
      }

      setPlanSyncToast({
        isOpen: true,
        title:
          kind === 'workout'
            ? '운동이 오늘 플랜에 추가됐어요'
            : '식단이 오늘 플랜에 반영됐어요',
        message: itemName,
      });

      planSyncToastTimeoutRef.current = setTimeout(() => {
        setPlanSyncToast((previous) => ({ ...previous, isOpen: false }));
        planSyncToastTimeoutRef.current = null;
      }, 6500);
    },
    []
  );

  const openPlanFromToast = useCallback(() => {
    hidePlanSyncToast();
    router.push('/recommend');
  }, [hidePlanSyncToast, router]);

  const hasNavigatedAwayFromHome = useCallback(() => {
    if (typeof window === 'undefined') {
      return false;
    }

    return window.location.pathname !== '/';
  }, []);

  const isRecommendationRequestCancelled = useCallback((error: unknown, signal?: AbortSignal) => {
    if (
      !isMountedRef.current ||
      isLeavingHomeRef.current ||
      hasNavigatedAwayFromHome() ||
      signal?.aborted
    ) {
      return true;
    }

    return error instanceof DOMException && error.name === 'AbortError';
  }, [hasNavigatedAwayFromHome]);

  useEffect(() => {
    isMountedRef.current = true;
    isLeavingHomeRef.current = false;
    const recommendationAbortControllers =
      recommendationAbortControllersRef.current;

    const markLeavingHome = () => {
      isLeavingHomeRef.current = true;
    };

    window.addEventListener('beforeunload', markLeavingHome);
    window.addEventListener('pagehide', markLeavingHome);

    return () => {
      markLeavingHome();
      isMountedRef.current = false;
      window.removeEventListener('beforeunload', markLeavingHome);
      window.removeEventListener('pagehide', markLeavingHome);
      recommendationAbortControllers.forEach((controller) => controller.abort());
      recommendationAbortControllers.clear();
      if (planSyncToastTimeoutRef.current) {
        clearTimeout(planSyncToastTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    homeRecommendationsRawRef.current = homeRecommendationsRaw;
  }, [homeRecommendationsRaw]);

  useEffect(() => {
    recommendationAddedRef.current = recommendationAdded;
  }, [recommendationAdded]);

  useEffect(() => {
    recommendationHistoryRef.current = recommendationHistory;
  }, [recommendationHistory]);

  const applyIncomingRecommendations = useCallback((
    incoming: HomeRecommendations,
    scope: RecommendationScope
  ) => {
    const currentRecommendations =
      homeRecommendationsRawRef.current.date === incoming.date
        ? homeRecommendationsRawRef.current
        : createEmptyRecommendations(incoming.date);
    const nextRaw = mergeRecommendations(currentRecommendations, incoming, scope);
    const nextAdded = resetAddedStateForScope(recommendationAddedRef.current, scope);
    const nextHistory = appendRecommendationHistory(
      recommendationHistoryRef.current,
      incoming,
      scope
    );

    setHomeRecommendationsRaw(nextRaw);
    setAiRecommendations(mapRecommendationsToLegacyCards(nextRaw));
    setRecommendationAdded(nextAdded);
    setRecommendationHistory(nextHistory);
    clearRecommendationHighlightsForScope(scope);
    persistHomeRecommendationCache(nextRaw, nextAdded, nextHistory);
  }, [clearRecommendationHighlightsForScope, persistHomeRecommendationCache]);

  const fetchRecommendationScope = useCallback(async (
    scope: RequestedRecommendationScope,
    options?: {
      suppressSectionLoading?: boolean;
      suppressAlert?: boolean;
      signal?: AbortSignal;
    }
  ) => {
    if (!userData?.user_id) return false;

    const suppressSectionLoading = options?.suppressSectionLoading ?? false;
    const suppressAlert = options?.suppressAlert ?? false;
    const requestController = options?.signal ? null : new AbortController();
    const requestSignal = options?.signal ?? requestController?.signal;
    const unregisterAbortController = requestController
      ? registerRecommendationAbortController(requestController)
      : null;

    if (!suppressSectionLoading) {
      setRecommendationRefresh((prev) => ({ ...prev, [scope]: true }));
    }

    try {
      const currentHistory = recommendationHistoryRef.current;
      let incoming: HomeRecommendations | null = null;

      for (let attempt = 0; attempt < 3; attempt += 1) {
        const response = await fetch(buildApiUrl(getHomeRecommendationApiPath(scope)), {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify({
            recent_recommendations: currentHistory,
          }),
          signal: requestSignal,
        });

        if (!response.ok) {
          throw new Error('Failed to load home recommendations.');
        }

        const candidate = await response.json() as HomeRecommendations;
        incoming = candidate;

        if (!hasRecommendationHistoryCollision(currentHistory, candidate, scope)) {
          break;
        }
      }

      if (!incoming) {
        throw new Error('Failed to load home recommendations.');
      }

      if (
        requestSignal?.aborted ||
        !isMountedRef.current ||
        isLeavingHomeRef.current ||
        hasNavigatedAwayFromHome()
      ) {
        return false;
      }

      applyIncomingRecommendations(incoming, scope);
      return true;
    } catch (error) {
      if (isRecommendationRequestCancelled(error, requestSignal)) {
        return false;
      }

      console.error('Failed to fetch home recommendations', error);
      if (!suppressAlert) {
        setAlertPopup({
          isOpen: true,
          message: 'AI 異붿쿇??遺덈윭?ㅼ? 紐삵뻽?듬땲?? ?좎떆 ???ㅼ떆 ?쒕룄??二쇱꽭??',
        });
      }
      return false;
    } finally {
      unregisterAbortController?.();

      if (
        !suppressSectionLoading &&
        isMountedRef.current &&
        !isLeavingHomeRef.current &&
        !hasNavigatedAwayFromHome()
      ) {
        setRecommendationRefresh((prev) => ({ ...prev, [scope]: false }));
      }
    }
  }, [
    applyIncomingRecommendations,
    hasNavigatedAwayFromHome,
    isRecommendationRequestCancelled,
    registerRecommendationAbortController,
    userData?.user_id,
  ]);

  const fetchHomeRecommendations = useCallback(async (scope: RecommendationScope) => {
    if (!userData?.user_id) return;

    if (scope !== 'all') {
      await fetchRecommendationScope(scope);
      return;
    }

    setIsRecommendationLoading(true);
    const requestController = new AbortController();
    const unregisterAbortController = registerRecommendationAbortController(requestController);

    try {
      const workoutLoaded = await fetchRecommendationScope('workout', {
        suppressSectionLoading: true,
        suppressAlert: true,
        signal: requestController.signal,
      });
      const dietLoaded = await fetchRecommendationScope('diet', {
        suppressSectionLoading: true,
        suppressAlert: true,
        signal: requestController.signal,
      });

      if (
        requestController.signal.aborted ||
        !isMountedRef.current ||
        isLeavingHomeRef.current ||
        hasNavigatedAwayFromHome()
      ) {
        return;
      }

      if (!workoutLoaded || !dietLoaded) {
        setAlertPopup({
          isOpen: true,
          message: 'AI 異붿쿇??遺덈윭?ㅼ? 紐삵뻽?듬땲?? ?좎떆 ???ㅼ떆 ?쒕룄??二쇱꽭??',
        });
      }
    } catch (error) {
      if (isRecommendationRequestCancelled(error, requestController.signal)) {
        return;
      }

      console.error('Failed to fetch home recommendations', error);
      setAlertPopup({
        isOpen: true,
        message: 'AI 추천을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.',
      });
    } finally {
      unregisterAbortController();

      if (
        isMountedRef.current &&
        !isLeavingHomeRef.current &&
        !hasNavigatedAwayFromHome()
      ) {
        setIsRecommendationLoading(false);
      }
    }
  }, [
    fetchRecommendationScope,
    hasNavigatedAwayFromHome,
    isRecommendationRequestCancelled,
    registerRecommendationAbortController,
    userData?.user_id,
  ]);

  const openWorkoutRecommendationPopup = (slot: WorkoutSlot, workout: LegacyWorkoutCard) => {
    closeAllModals();
    setWorkoutPopup({
      isOpen: true,
      target: {
        ...workout,
        slot,
      },
    });
  };

  const openDietRecommendationPopup = (diet: LegacyDietCard, mealType: DietSlot) => {
    closeAllModals();
    setDietPopup({
      isOpen: true,
      target: diet,
      mealType,
    });
  };

  const confirmWorkoutRecommendationAdd = async () => {
    if (!workoutPopup.target) {
      setWorkoutPopup({ isOpen: false, target: null });
      return;
    }

    const target = workoutPopup.target;
    const todayStr = formatKstDate();
    const todayPlan = getPlanByDate(todayStr);
    const isDuplicate = todayPlan?.exercises.some(
      (ex) => ex.title === target.name
    );

    if (isDuplicate) {
      setWorkoutPopup({ isOpen: false, target: null });
      setTimeout(() => setAlertPopup({ isOpen: true, message: '이미 캘린더에 추가된 운동입니다!' }), 100);
      return;
    }

    const slot = (target.slot || 'upper_body') as WorkoutSlot;
    const didAdd = await addWorkout(todayStr, {
      title: target.name,
      time: target.duration || '추천',
      level: getWorkoutSlotLabel(slot),
      calories: `${target.calories || 0} kcal`,
      color: getWorkoutSlotColor(slot),
      type: slot,
      targetSets: slot === 'cardio' ? null : target.sets ?? 3,
      durationMinutes: slot === 'cardio'
        ? target.duration_minutes ?? 20
        : null,
    });

    if (didAdd) {
      let nextAdded = recommendationAdded;
      setRecommendationAdded((prev) => {
        nextAdded = {
          ...prev,
          workout: {
            ...prev.workout,
            [slot]: true,
          },
        };
        return nextAdded;
      });
      persistHomeRecommendationCache(
        homeRecommendationsRawRef.current,
        nextAdded,
        recommendationHistoryRef.current
      );
      markRecommendationHighlighted(getWorkoutRecommendationHighlightId(slot));
      showPlanSyncToast('workout', target.name);
    }

    setWorkoutPopup({ isOpen: false, target: null });
  };

  const confirmDietRecommendationReplace = async () => {
    if (!dietPopup.target || !dietPopup.mealType) {
      setDietPopup({ isOpen: false, target: null, mealType: null });
      return;
    }

    const todayStr = formatKstDate();
    const mealType = dietPopup.mealType;
    const todayPlan = getPlanByDate(todayStr);
    const existingDiet = todayPlan?.diets.find((d) =>
      matchesMealSlot(d.type, mealType)
    );

    if (existingDiet && existingDiet.name === dietPopup.target.name) {
      setDietPopup({ isOpen: false, target: null, mealType: null });
      setTimeout(() => setAlertPopup({ isOpen: true, message: '이미 캘린더에 추가된 음식입니다!' }), 100);
      return;
    }

    const didReplace = await replaceDiet(todayStr, mealType, {
      name: dietPopup.target.name,
      desc: dietPopup.target.desc,
      kcal: `${dietPopup.target.calories || 0} kcal`,
    });

    if (didReplace) {
      let nextAdded = recommendationAdded;
      setRecommendationAdded((prev) => {
        nextAdded = {
          ...prev,
          diet: {
            ...prev.diet,
            [mealType]: true,
          },
        };
        return nextAdded;
      });
      persistHomeRecommendationCache(
        homeRecommendationsRawRef.current,
        nextAdded,
        recommendationHistoryRef.current
      );
      markRecommendationHighlighted(getDietRecommendationHighlightId(mealType));
      showPlanSyncToast('diet', dietPopup.target.name);
    }

    setDietPopup({ isOpen: false, target: null, mealType: null });
  };

  const addTodo = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTodo.trim()) return;
    setTodos([...todos, { id: Date.now(), text: newTodo, completed: false }]);
    setNewTodo('');
  };

  const toggleTodo = (id: number) => {
    setTodos(todos.map(todo => todo.id === id ? { ...todo, completed: !todo.completed } : todo));
  };

  const deleteTodo = (id: number) => {
    setTodos(todos.filter(todo => todo.id !== id));
  };

  const confirmResetIntake = () => {
    if (!resetConfirmModal.target) return;

    setIntakes(prev => {
      const newIntakes = { ...prev };
      if (resetConfirmModal.target === 'calories') {
        newIntakes.calories = 0;
      } else if (resetConfirmModal.target === 'macros') {
        newIntakes.carbs = 0;
        newIntakes.protein = 0;
        newIntakes.fat = 0;
      }

      persistNutritionSnapshot(targets, newIntakes);

      return newIntakes;
    });

    setResetConfirmModal({ isOpen: false, target: null });
  };

  const handleDietSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualInput.calories && !manualInput.carbs && !manualInput.protein && !manualInput.fat) return;

    const inputCal = Number(manualInput.calories) || 0;
    const inputCarbs = Number(manualInput.carbs) || 0;
    const inputProtein = Number(manualInput.protein) || 0;
    const inputFat = Number(manualInput.fat) || 0;

    if (inputCal > 10000 || inputCarbs > 1000 || inputProtein > 1000 || inputFat > 1000) {
      setAlertPopup({
        isOpen: true,
        message: "입력 가능한 범위를 초과했습니다\n(최대 10,000 kcal 또는 각 1,000g)"
      });
      return;
    }

    setIsAnalyzing(true);
    setDietAnalysis(null);

    // Simulate save/analysis delay
    setTimeout(() => {
      setIntakes(prev => {
        const newIntakes = {
          calories: prev.calories + inputCal,
          carbs: prev.carbs + inputCarbs,
          protein: prev.protein + inputProtein,
          fat: prev.fat + inputFat
        };

        persistNutritionSnapshot(targets, newIntakes);

        return newIntakes;
      });

      setDietAnalysis({
        calories: inputCal,
        carbs: inputCarbs,
        protein: inputProtein,
        fat: inputFat,
        message: '직접 입력한 영양소가 추가되었습니다.'
      });
      setIsAnalyzing(false);
      setManualInput({ calories: '', carbs: '', protein: '', fat: '' });
    }, 600);
  };

  const addWater = (e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    if (waterGlasses < maxWaterGlasses) {
      const newVal = waterGlasses + 1;
      setWaterGlasses(newVal);
      if (newVal === maxWaterGlasses) {
        setShowWaterGoalAnim(true);
        setTimeout(() => setShowWaterGoalAnim(false), 3000);
      }
    }
  };

  const removeWater = (e: React.MouseEvent) => {
    if (e) e.stopPropagation();
    if (waterGlasses > 0) setWaterGlasses(prev => prev - 1);
  };

  useEffect(() => {
    setIsClient(true);
    const token = localStorage.getItem('healthAppToken');
    const stored = localStorage.getItem('healthAppUser');
    
    if (!token || !stored) {
      router.push('/login'); 
    } else {
      // Load persisted nutrition data for today
      const savedNutrition = localStorage.getItem('healthAppNutrition');
      if (savedNutrition) {
        const parsed = JSON.parse(savedNutrition);
        const todayStr = formatKstDate();
        if (parsed.date === todayStr) {
          setIntakes({
            calories: parsed.consumedKcal || 0,
            carbs: parsed.consumedMacros?.carbs || 0,
            protein: parsed.consumedMacros?.protein || 0,
            fat: parsed.consumedMacros?.fat || 0
          });

          if (parsed.targetMacros || parsed.targetKcal) {
            const savedTargets = {
              calories: parsed.targetKcal || 0,
              carbs: parsed.targetMacros?.carbs || 0,
              protein: parsed.targetMacros?.protein || 0,
              fat: parsed.targetMacros?.fat || 0
            };

            setTargets(savedTargets);
            setGoalInput({
              calories: String(savedTargets.calories || ''),
              carbs: String(savedTargets.carbs || ''),
              protein: String(savedTargets.protein || ''),
              fat: String(savedTargets.fat || '')
            });
          }
        }
      }
    }
  }, [router]);

  useEffect(() => {
    if (userData && userData.weight && userData.height && userData.age && userData.gender) {
      const w = parseFloat(String(userData.weight));
      const h = parseFloat(String(userData.height)); // in cm
      const age = parseInt(String(userData.age), 10);
      const genderDelta = (userData.gender === '남성' || userData.gender === 'male') ? 5 : -161;

      // Mifflin-St Jeor Equation
      const bmr = 10 * w + 6.25 * h - 5 * age + genderDelta;
      const recCals = Math.round(bmr * 1.375); // Light activity multiplier

      setRecommendedCalories(recCals);

      const initialTargets = {
        calories: recCals,
        carbs: Math.round((recCals * 0.5) / 4),
        protein: Math.round((recCals * 0.3) / 4),
        fat: Math.round((recCals * 0.2) / 9)
      };

      const savedNutritionRaw = localStorage.getItem('healthAppNutrition');
      const todayStr = formatKstDate();

      if (savedNutritionRaw) {
        try {
          const parsed = JSON.parse(savedNutritionRaw);
          if (parsed.date === todayStr && (parsed.targetMacros || parsed.targetKcal)) {
            const savedTargets = {
              calories: parsed.targetKcal || initialTargets.calories,
              carbs: parsed.targetMacros?.carbs || initialTargets.carbs,
              protein: parsed.targetMacros?.protein || initialTargets.protein,
              fat: parsed.targetMacros?.fat || initialTargets.fat
            };

            setTargets(savedTargets);
            setGoalInput({
              calories: String(savedTargets.calories),
              carbs: String(savedTargets.carbs),
              protein: String(savedTargets.protein),
              fat: String(savedTargets.fat)
            });
            return;
          }
        } catch (error) {
          console.error('Failed to parse saved nutrition state', error);
        }
      }

      setTargets(initialTargets);
      setGoalInput({
        calories: String(initialTargets.calories),
        carbs: String(initialTargets.carbs),
        protein: String(initialTargets.protein),
        fat: String(initialTargets.fat)
      });
    }
  }, [userData]);

  useEffect(() => {
    setCurrentDate(formatKstDisplayDate(new Date()));
  }, []);

  useEffect(() => {
    if (!isClient || isUserLoading || !userData?.user_id) return;

    const todayStr = formatKstDate();
    const cacheKey = getHomeRecommendationCacheKey(userData.user_id, todayStr);
    const cached = localStorage.getItem(cacheKey);

    if (cached) {
      try {
        const parsed = JSON.parse(cached) as {
          recommendations?: HomeRecommendations;
          added?: RecommendationAddedState;
          history?: RecommendationHistoryState;
        };

        if (
          parsed.recommendations?.date === todayStr &&
          isRecommendationScopeComplete(parsed.recommendations, 'all')
        ) {
          const nextRecommendations = parsed.recommendations;
          const nextAdded = parsed.added || createEmptyAddedState();
          const nextHistory = parsed.history || createEmptyRecommendationHistory();
          setHomeRecommendationsRaw(nextRecommendations);
          setAiRecommendations(mapRecommendationsToLegacyCards(nextRecommendations));
          setRecommendationAdded(nextAdded);
          setRecommendationHistory(nextHistory);
          return;
        }
      } catch (error) {
        console.error('Failed to parse recommendation cache', error);
      }
    }

    const emptyRecommendations = createEmptyRecommendations(todayStr);
    setHomeRecommendationsRaw(emptyRecommendations);
    setAiRecommendations(mapRecommendationsToLegacyCards(emptyRecommendations));
    setRecommendationAdded(createEmptyAddedState());
    setRecommendationHistory(createEmptyRecommendationHistory());
    void fetchHomeRecommendations('all');
  }, [fetchHomeRecommendations, isClient, isUserLoading, userData?.user_id]);

  const todayRecommendationPlan = getPlanByDate(formatKstDate());

  const isWorkoutRecommendationAdded = (
    slot: WorkoutSlot,
    workout: LegacyWorkoutCard | null
  ) => {
    if (!workout) return false;
    if (recommendationAdded.workout[slot]) return true;

    return Boolean(
      todayRecommendationPlan?.exercises.some((exercise) => {
        const savedTitle = exercise.title.trim().toLowerCase();
        const recommendationTitle = workout.name.trim().toLowerCase();
        return savedTitle === recommendationTitle;
      })
    );
  };

  const isDietRecommendationAdded = (
    mealType: DietSlot,
    diet: LegacyDietCard | null
  ) => {
    if (!diet) return false;
    if (recommendationAdded.diet[mealType]) return true;

    return Boolean(
      todayRecommendationPlan?.diets.some((savedDiet) => {
        const savedName = savedDiet.name.trim().toLowerCase();
        const recommendationName = diet.name.trim().toLowerCase();
        return (
          matchesMealSlot(savedDiet.type, mealType) &&
          savedName === recommendationName
        );
      })
    );
  };

  const upperWorkoutHighlightId = getWorkoutRecommendationHighlightId('upper_body');
  const lowerWorkoutHighlightId = getWorkoutRecommendationHighlightId('lower_body');
  const cardioWorkoutHighlightId = getWorkoutRecommendationHighlightId('cardio');
  const stretchingWorkoutHighlightId = getWorkoutRecommendationHighlightId('stretching');
  const isUpperWorkoutHighlighted = highlightedRecommendationIds.includes(upperWorkoutHighlightId);
  const isLowerWorkoutHighlighted = highlightedRecommendationIds.includes(lowerWorkoutHighlightId);
  const isCardioWorkoutHighlighted = highlightedRecommendationIds.includes(cardioWorkoutHighlightId);
  const isStretchingWorkoutHighlighted = highlightedRecommendationIds.includes(stretchingWorkoutHighlightId);

return (
  <div className="min-h-screen bg-[#f8fafc] text-gray-900 font-sans p-6 pb-32 md:p-8 lg:p-12 md:pb-36 lg:pb-40">
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <header className="flex justify-between items-start pt-4">
        <div className="flex flex-col space-y-2">
          <p className="text-sm font-semibold text-gray-500 tracking-wide">{currentDate}</p>
          {isClient && !isUserLoading && userData ? (
            <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.5 }}>
              <h1 className="text-3xl font-extrabold text-gray-900 tracking-tight">
                <span className="text-[#2563eb]">{userData?.nickname || userData?.name || '사용자'}</span> 님,
              </h1>
              <p className="text-gray-600 text-base font-medium mt-1">오늘은 <span className="text-[#2563eb] font-bold">{userData?.goal || '건강'}</span>을 목표로 달려봐요!</p>
            </motion.div>
          ) : (
            <div className="flex flex-col justify-center h-[76px] space-y-2 animate-pulse">
              <div className="h-8 bg-gray-200/50 rounded-md w-48"></div>
              <div className="h-4 bg-gray-200/50 rounded-md w-64"></div>
            </div>
          )}
        </div>
        <button
          onClick={() => { closeAllModals(); setIsPlannerOpen(true); }}
          className="px-3 py-3 bg-white rounded-2xl shadow-[0_8px_30px_rgb(0,0,0,0.06)] border border-gray-100/80 text-[#2563eb] hover:bg-blue-50 hover:-translate-y-1 hover:shadow-[0_12px_40px_rgb(37,99,235,0.12)] transition-all duration-300 flex flex-col items-center justify-center min-w-[76px]"
        >
          <Calendar className="w-6 h-6 mb-1 drop-shadow-sm" />
          <span className="text-[10.5px] font-bold">오늘의 할 일</span>
        </button>
      </header>

      {/* Dashboard Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
        {/* Intake Calories Card */}
        <div
          onClick={() => { closeAllModals(); setIsCalorieModalOpen(true); }}
          className="bg-white p-6 rounded-3xl shadow-[0_8px_30px_rgb(0,0,0,0.04)] border border-gray-100/60 flex flex-col justify-between hover:shadow-[0_12px_40px_rgb(249,115,22,0.08)] hover:-translate-y-1 transition-all duration-300 cursor-pointer group"
        >
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center space-x-3">
              <div className="w-11 h-11 rounded-2xl bg-orange-50/80 flex items-center justify-center text-orange-500 shadow-inner group-hover:scale-110 transition-transform">
                <Utensils className="w-5 h-5" />
              </div>
              <h2 className="text-base font-bold text-gray-800 tracking-wide">섭취 칼로리</h2>
            </div>
          </div>
          <div>
            <div className="flex items-baseline space-x-1 truncate max-w-[150px]">
              <p className="text-3xl font-extrabold text-gray-900 tracking-tight truncate min-w-0">{intakes.calories.toLocaleString()}</p>
              <span className="text-sm font-semibold text-gray-400 truncate min-w-0">/ {targets.calories.toLocaleString()} kcal</span>
            </div>
            <div className="flex items-center space-x-1 mt-2.5">
              <div className="w-full bg-gray-100 rounded-full h-1.5 flex-1 max-w-[120px] overflow-hidden">
                <motion.div initial={{ width: 0 }} animate={{ width: `${Math.min((intakes.calories / targets.calories) * 100, 100)}%` }} transition={{ duration: 1, ease: 'easeOut' }} className="bg-orange-500 h-1.5 rounded-full"></motion.div>
              </div>
              <p className="text-sm text-gray-500 font-semibold ml-2">{Math.round(Math.min((intakes.calories / targets.calories) * 100, 100))}%</p>
            </div>
          </div>
        </div>

        {/* Nutrients Card */}
        <div
          onClick={() => { closeAllModals(); setIsNutrientModalOpen(true); }}
          className="bg-white p-6 rounded-3xl shadow-[0_8px_30px_rgb(0,0,0,0.04)] border border-gray-100/60 flex flex-col justify-between hover:shadow-[0_12px_40px_rgb(16,185,129,0.08)] hover:-translate-y-1 transition-all duration-300 md:col-span-2 lg:col-span-2 cursor-pointer"
        >
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <div className="w-11 h-11 rounded-2xl bg-emerald-50/80 flex items-center justify-center text-emerald-500 shadow-inner">
                <Activity className="w-5 h-5" />
              </div>
              <h2 className="text-base font-bold text-gray-800 tracking-wide">오늘의 영양소 (탄/단/지)</h2>
            </div>
          </div>
          <div className="flex gap-4 items-end justify-between h-full pt-2">
            <div className="flex-1 space-y-1.5 min-w-0">
              <div className="flex justify-between text-[11px] md:text-xs font-bold text-gray-500 whitespace-nowrap">
                <span>탄수화물</span><span className="truncate ml-1">{intakes.carbs.toLocaleString()}/{targets.carbs.toLocaleString()}g</span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                <motion.div initial={{ width: 0 }} animate={{ width: `${Math.min((intakes.carbs / targets.carbs) * 100, 100)}%` }} transition={{ duration: 1 }} className="bg-amber-400 h-2 rounded-full"></motion.div>
              </div>
            </div>
            <div className="flex-1 space-y-1.5 min-w-0">
              <div className="flex justify-between text-[11px] md:text-xs font-bold text-gray-500 whitespace-nowrap">
                <span>단백질</span><span className="truncate ml-1">{intakes.protein.toLocaleString()}/{targets.protein.toLocaleString()}g</span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                <motion.div initial={{ width: 0 }} animate={{ width: `${Math.min((intakes.protein / targets.protein) * 100, 100)}%` }} transition={{ duration: 1, delay: 0.1 }} className="bg-blue-400 h-2 rounded-full"></motion.div>
              </div>
            </div>
            <div className="flex-1 space-y-1.5 min-w-0">
              <div className="flex justify-between text-[11px] md:text-xs font-bold text-gray-500 whitespace-nowrap">
                <span>지방</span><span className="truncate ml-1">{intakes.fat.toLocaleString()}/{targets.fat.toLocaleString()}g</span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                <motion.div initial={{ width: 0 }} animate={{ width: `${Math.min((intakes.fat / targets.fat) * 100, 100)}%` }} transition={{ duration: 1, delay: 0.2 }} className="bg-rose-400 h-2 rounded-full"></motion.div>
              </div>
            </div>
          </div>
        </div>

        {/* Hydration Tracker Card (NEW) */}
        <div
          onClick={() => { }}
          className="col-span-1 md:col-span-2 lg:col-span-1 bg-white p-6 rounded-3xl shadow-[0_8px_30px_rgb(0,0,0,0.04)] border border-gray-100/60 flex flex-col justify-between group hover:shadow-[0_12px_40px_rgb(14,165,233,0.12)] hover:-translate-y-1 transition-all duration-300 relative overflow-hidden"
        >
          {/* Animated background water fill level */}
          <motion.div
            className="absolute bottom-0 left-0 right-0 bg-sky-50/50 z-0"
            animate={{ height: `${(waterGlasses / maxWaterGlasses) * 100}%` }}
            transition={{ type: "spring", stiffness: 60, damping: 15 }}
          />

          {/* Goal Achieved Animation */}
          <AnimatePresence>
            {showWaterGoalAnim && (
              <>
                <motion.div
                  initial={{ y: 20, opacity: 0, scale: 0.5 }}
                  animate={{ y: -40, opacity: 1, scale: 1.2 }}
                  exit={{ opacity: 0, scale: 0.8 }}
                  transition={{ duration: 0.6, ease: "easeOut" }}
                  className="absolute top-4 left-1/2 -translate-x-1/2 z-20 flex space-x-1"
                >
                  <Droplets className="w-5 h-5 text-blue-500 drop-shadow-sm" />
                  <Droplets className="w-6 h-6 text-sky-400 -mt-2 drop-shadow-sm" />
                  <Droplets className="w-5 h-5 text-blue-500 drop-shadow-sm" />
                </motion.div>
                <motion.div
                  initial={{ y: 30, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ delay: 0.2, duration: 0.5 }}
                  className="absolute top-[80px] left-1/2 -translate-x-1/2 z-20 bg-blue-500 text-white text-[11px] font-bold px-3 py-1.5 rounded-full whitespace-nowrap shadow-md"
                >
                  오늘의 목표 달성! 🎉
                </motion.div>
              </>
            )}
          </AnimatePresence>

          <div className="flex items-center justify-between mb-4 z-10 relative">
            <div className="flex items-center space-x-3">
              <div className="w-11 h-11 rounded-2xl bg-sky-100/80 flex items-center justify-center text-sky-500 shadow-inner group-hover:scale-110 transition-transform">
                <Droplets className="w-5 h-5" />
              </div>
              <h2 className="text-base font-bold text-gray-800 tracking-wide">수분 섭취</h2>
            </div>
          </div>

          <div className="z-10 relative mt-2">
            <div className="flex items-center justify-between">
              <div className="flex items-baseline space-x-1">
                <motion.p
                  key={waterGlasses}
                  initial={{ scale: 1.5, opacity: 0, color: '#38bdf8' }}
                  animate={{ scale: 1, opacity: 1, color: '#111827' }}
                  className="text-3xl font-extrabold tracking-tight"
                >
                  {waterGlasses}
                </motion.p>
                <span className="text-sm font-semibold text-gray-400">/ {maxWaterGlasses} 잔</span>
              </div>
              <div className="flex items-center space-x-2">
                <button onClick={removeWater} className="w-8 h-8 rounded-full bg-sky-100 flex items-center justify-center text-sky-500 hover:bg-sky-500 hover:text-white transition-colors cursor-pointer disabled:opacity-50" disabled={waterGlasses === 0}>
                  <span className="text-xl font-bold leading-none mb-0.5">-</span>
                </button>
                <button onClick={addWater} className="w-8 h-8 rounded-full bg-sky-100 flex items-center justify-center text-sky-500 hover:bg-sky-500 hover:text-white transition-colors cursor-pointer disabled:opacity-50" disabled={waterGlasses === maxWaterGlasses}>
                  <Plus className="w-4 h-4 text-inherit" />
                </button>
              </div>
            </div>
            <p className="text-xs text-sky-600 font-semibold mt-3 text-center opacity-0 group-hover:opacity-100 transition-opacity">
              오늘도 충분한 수분을 섭취하세요!
            </p>
          </div>
        </div>
      </div>

      {/* Quick Actions (NEW) */}
      <div className="pt-2 pb-4">
        <p className="text-sm font-bold text-gray-500 mb-4 px-1">빠른 실행</p>
        <div className="grid grid-cols-2 gap-4">
          <button
            onClick={() => router.push('/recommend')}
            className="group bg-gradient-to-r from-[#2563eb] to-blue-500 p-5 rounded-3xl shadow-[0_8px_30px_rgb(37,99,235,0.25)] hover:shadow-[0_12px_40px_rgb(37,99,235,0.35)] hover:-translate-y-1 transition-all duration-300 flex items-center justify-between text-left overflow-hidden relative"
          >
            <div className="absolute top-0 right-0 w-32 h-32 bg-white/10 rounded-full blur-2xl -mr-10 -mt-10 group-hover:scale-110 transition-transform"></div>
            <div className="relative z-10">
              <h3 className="text-white font-bold text-lg mb-1">오늘 운동 추천</h3>
              <p className="text-blue-100 text-xs font-medium">AI가 분석한 맞춤형 플랜</p>
            </div>
            <div className="w-10 h-10 bg-white/20 backdrop-blur-md rounded-2xl flex items-center justify-center text-white relative z-10">
              <Activity className="w-5 h-5" />
            </div>
          </button>
          <button
            onClick={() => { closeAllModals(); setIsDietModalOpen(true); }}
            className="group bg-white p-5 rounded-3xl shadow-[0_8px_30px_rgb(0,0,0,0.06)] border border-gray-100/80 hover:shadow-[0_12px_40px_rgb(0,0,0,0.1)] hover:-translate-y-1 transition-all duration-300 flex items-center justify-between text-left"
          >
            <div>
              <h3 className="text-gray-900 font-bold text-lg mb-1">간편 영양 기록</h3>
              <p className="text-gray-400 text-xs font-medium">칼로리 관리의 시작</p>
            </div>
            <div className="w-10 h-10 bg-gray-50 rounded-2xl flex items-center justify-center text-gray-500 group-hover:bg-orange-50 group-hover:text-orange-500 transition-colors">
              <Utensils className="w-5 h-5" />
            </div>
          </button>
        </div>
      </div>

      {/* AI Recommended Section */}
      <section className="mt-4 mb-10 space-y-6">
        {/* Workout Recommendation Grid */}
        <div className="bg-white p-6 md:p-8 rounded-3xl shadow-[0_8px_30px_rgb(0,0,0,0.04)] border border-gray-100/60">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-lg font-bold text-gray-900 tracking-wide flex items-center">
              <span className="bg-gradient-to-r from-[#2563eb] to-indigo-500 text-transparent bg-clip-text mr-2">AI 운동 추천</span>
            </h2>
            <button
              onClick={() => void fetchHomeRecommendations('workout')}
              disabled={recommendationRefresh.workout || isRecommendationLoading}
              className="text-gray-400 hover:text-[#2563eb] hover:bg-blue-50 transition-all p-2 rounded-full cursor-pointer group focus:outline-none disabled:opacity-50"
            >
              <RefreshCw className={`w-5 h-5 transition-transform duration-500 ${(recommendationRefresh.workout || isRecommendationLoading) ? 'animate-spin' : 'group-hover:rotate-180'}`} />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {/* Strength Upper */}
            <div
              onMouseEnter={() => dismissRecommendationHighlight(upperWorkoutHighlightId)}
              onPointerDown={() => dismissRecommendationHighlight(upperWorkoutHighlightId)}
              className={`rounded-xl p-5 border shadow-sm hover:shadow-[0_8px_24px_rgba(37,99,235,0.1)] transition-all flex flex-col relative group ${isUpperWorkoutHighlighted ? 'bg-emerald-50/70 border-emerald-200 ring-2 ring-emerald-200' : 'bg-white border-gray-100'}`}
            >
              {isUpperWorkoutHighlighted && (
                <span className="absolute right-4 top-14 rounded-full bg-emerald-500 px-2.5 py-1 text-[10px] font-bold text-white shadow-sm">
                  방금 반영됨
                </span>
              )}
              <div className="flex justify-between items-start mb-4">
                <span className="px-2.5 py-1 bg-blue-50 border border-blue-100 text-blue-600 rounded-full text-xs font-bold shadow-sm">근력 (상체)</span>
                {aiRecommendations.workout?.strength?.upper && (
                  <button aria-label="상체 운동 추천 추가" onClick={() => openWorkoutRecommendationPopup('upper_body', aiRecommendations.workout.strength.upper!)} disabled={isWorkoutRecommendationAdded('upper_body', aiRecommendations.workout.strength.upper)} className="p-1.5 bg-blue-500 shadow-md text-white hover:bg-blue-600 rounded-full transition-colors z-10 disabled:bg-gray-300 disabled:hover:bg-gray-300">
                    <Plus className="w-4 h-4" />
                  </button>
                )}
              </div>
              {aiRecommendations.workout?.strength?.upper ? (
                <div className="flex-1 flex flex-col">
                  <div className="w-14 h-14 bg-gradient-to-br from-blue-100 to-blue-200 rounded-full mb-4 flex items-center justify-center text-blue-600 shadow-inner">
                    <Dumbbell className="w-7 h-7" />
                  </div>
                  <h4 className="font-bold text-gray-900 text-[15px] mb-1.5">{aiRecommendations.workout.strength.upper.name}</h4>
                  <p className="text-xs text-gray-500 leading-relaxed">{compactText(aiRecommendations.workout.strength.upper.desc, 42)}</p>
                  <p className="mt-2 text-[11px] font-bold text-blue-500">{aiRecommendations.workout.strength.upper.duration}</p>
                  {isWorkoutRecommendationAdded('upper_body', aiRecommendations.workout.strength.upper) && <p className="mt-1 text-[11px] font-bold text-emerald-500">추가됨</p>}
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-xs text-gray-400">추천 항목 없음</div>
              )}
            </div>

            {/* Strength Lower */}
            <div
              onMouseEnter={() => dismissRecommendationHighlight(lowerWorkoutHighlightId)}
              onPointerDown={() => dismissRecommendationHighlight(lowerWorkoutHighlightId)}
              className={`rounded-xl p-5 border shadow-sm hover:shadow-[0_8px_24px_rgba(37,99,235,0.1)] transition-all flex flex-col relative group ${isLowerWorkoutHighlighted ? 'bg-emerald-50/70 border-emerald-200 ring-2 ring-emerald-200' : 'bg-white border-gray-100'}`}
            >
              {isLowerWorkoutHighlighted && (
                <span className="absolute right-4 top-14 rounded-full bg-emerald-500 px-2.5 py-1 text-[10px] font-bold text-white shadow-sm">
                  방금 반영됨
                </span>
              )}
              <div className="flex justify-between items-start mb-4">
                <span className="px-2.5 py-1 bg-indigo-50 border border-indigo-100 text-indigo-600 rounded-full text-xs font-bold shadow-sm">근력 (하체)</span>
                {aiRecommendations.workout?.strength?.lower && (
                  <button aria-label="하체 운동 추천 추가" onClick={() => openWorkoutRecommendationPopup('lower_body', aiRecommendations.workout.strength.lower!)} disabled={isWorkoutRecommendationAdded('lower_body', aiRecommendations.workout.strength.lower)} className="p-1.5 bg-indigo-500 shadow-md text-white hover:bg-indigo-600 rounded-full transition-colors z-10 disabled:bg-gray-300 disabled:hover:bg-gray-300">
                    <Plus className="w-4 h-4" />
                  </button>
                )}
              </div>
              {aiRecommendations.workout?.strength?.lower ? (
                <div className="flex-1 flex flex-col">
                  <div className="w-14 h-14 bg-gradient-to-br from-indigo-100 to-indigo-200 rounded-full mb-4 flex items-center justify-center text-indigo-600 shadow-inner">
                    <PersonStanding className="w-7 h-7" />
                  </div>
                  <h4 className="font-bold text-gray-900 text-[15px] mb-1.5">{aiRecommendations.workout.strength.lower.name}</h4>
                  <p className="text-xs text-gray-500 leading-relaxed">{compactText(aiRecommendations.workout.strength.lower.desc, 42)}</p>
                  <p className="mt-2 text-[11px] font-bold text-indigo-500">{aiRecommendations.workout.strength.lower.duration}</p>
                  {isWorkoutRecommendationAdded('lower_body', aiRecommendations.workout.strength.lower) && <p className="mt-1 text-[11px] font-bold text-emerald-500">추가됨</p>}
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-xs text-gray-400">추천 항목 없음</div>
              )}
            </div>

            {/* Cardio */}
            <div
              onMouseEnter={() => dismissRecommendationHighlight(cardioWorkoutHighlightId)}
              onPointerDown={() => dismissRecommendationHighlight(cardioWorkoutHighlightId)}
              className={`rounded-xl p-5 border shadow-sm hover:shadow-[0_8px_24px_rgba(244,63,94,0.15)] transition-all flex flex-col relative group ${isCardioWorkoutHighlighted ? 'bg-emerald-50/70 border-emerald-200 ring-2 ring-emerald-200' : 'bg-white border-gray-100'}`}
            >
              {isCardioWorkoutHighlighted && (
                <span className="absolute right-4 top-14 rounded-full bg-emerald-500 px-2.5 py-1 text-[10px] font-bold text-white shadow-sm">
                  방금 반영됨
                </span>
              )}
              <div className="flex justify-between items-start mb-4">
                <span className="px-2.5 py-1 bg-rose-50 border border-rose-100 text-rose-500 rounded-full text-xs font-bold shadow-sm">유산소</span>
                {aiRecommendations.workout?.cardio && (
                  <button aria-label="유산소 운동 추천 추가" onClick={() => openWorkoutRecommendationPopup('cardio', aiRecommendations.workout.cardio!)} disabled={isWorkoutRecommendationAdded('cardio', aiRecommendations.workout.cardio)} className="p-1.5 bg-rose-500 shadow-md text-white hover:bg-rose-600 rounded-full transition-colors z-10 disabled:bg-gray-300 disabled:hover:bg-gray-300">
                    <Plus className="w-4 h-4" />
                  </button>
                )}
              </div>
              {aiRecommendations.workout?.cardio ? (
                <div className="flex-1 flex flex-col">
                  <div className="w-14 h-14 bg-gradient-to-br from-rose-100 to-rose-200 rounded-full mb-4 flex items-center justify-center text-rose-500 shadow-inner">
                    <Heart className="w-7 h-7" />
                  </div>
                  <h4 className="font-bold text-gray-900 text-[15px] mb-1.5">{aiRecommendations.workout.cardio.name}</h4>
                  <p className="text-xs text-gray-500 leading-relaxed">{compactText(aiRecommendations.workout.cardio.desc, 42)}</p>
                  <p className="mt-2 text-[11px] font-bold text-rose-500">{aiRecommendations.workout.cardio.duration}</p>
                  {isWorkoutRecommendationAdded('cardio', aiRecommendations.workout.cardio) && <p className="mt-1 text-[11px] font-bold text-emerald-500">추가됨</p>}
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-xs text-gray-400">추천 항목 없음</div>
              )}
            </div>

            {/* Stretching */}
            <div
              onMouseEnter={() => dismissRecommendationHighlight(stretchingWorkoutHighlightId)}
              onPointerDown={() => dismissRecommendationHighlight(stretchingWorkoutHighlightId)}
              className={`rounded-xl p-5 border shadow-sm hover:shadow-[0_8px_24px_rgba(16,185,129,0.15)] transition-all flex flex-col relative group ${isStretchingWorkoutHighlighted ? 'bg-emerald-50/70 border-emerald-200 ring-2 ring-emerald-200' : 'bg-white border-gray-100'}`}
            >
              {isStretchingWorkoutHighlighted && (
                <span className="absolute right-4 top-14 rounded-full bg-emerald-500 px-2.5 py-1 text-[10px] font-bold text-white shadow-sm">
                  방금 반영됨
                </span>
              )}
              <div className="flex justify-between items-start mb-4">
                <span className="px-2.5 py-1 bg-emerald-50 border border-emerald-100 text-emerald-500 rounded-full text-xs font-bold shadow-sm">스트레칭</span>
                {aiRecommendations.workout?.stretching && (
                  <button aria-label="스트레칭 추천 추가" onClick={() => openWorkoutRecommendationPopup('stretching', aiRecommendations.workout.stretching!)} disabled={isWorkoutRecommendationAdded('stretching', aiRecommendations.workout.stretching)} className="p-1.5 bg-emerald-500 shadow-md text-white hover:bg-emerald-600 rounded-full transition-colors z-10 disabled:bg-gray-300 disabled:hover:bg-gray-300">
                    <Plus className="w-4 h-4" />
                  </button>
                )}
              </div>
              {aiRecommendations.workout?.stretching ? (
                <div className="flex-1 flex flex-col">
                  <div className="w-14 h-14 bg-gradient-to-br from-emerald-100 to-emerald-200 rounded-full mb-4 flex items-center justify-center text-emerald-500 shadow-inner">
                    <Droplets className="w-7 h-7" />
                  </div>
                  <h4 className="font-bold text-gray-900 text-[15px] mb-1.5">{aiRecommendations.workout.stretching.name}</h4>
                  <p className="text-xs text-gray-500 leading-relaxed">{compactText(aiRecommendations.workout.stretching.desc, 42)}</p>
                  <p className="mt-2 text-[11px] font-bold text-emerald-500">{aiRecommendations.workout.stretching.duration}</p>
                  {isWorkoutRecommendationAdded('stretching', aiRecommendations.workout.stretching) && <p className="mt-1 text-[11px] font-bold text-emerald-500">추가됨</p>}
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-xs text-gray-400">추천 항목 없음</div>
              )}
            </div>
          </div>
        </div>

        {/* Diet Recommendation Grid */}
        <div className="bg-white p-6 md:p-8 rounded-3xl shadow-[0_8px_30px_rgb(0,0,0,0.04)] border border-gray-100/60">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-lg font-bold text-gray-900 tracking-wide flex items-center">
              <span className="bg-gradient-to-r from-[#f59e0b] to-orange-500 text-transparent bg-clip-text mr-2">AI 식단 추천</span>
            </h2>
            <button
              onClick={() => void fetchHomeRecommendations('diet')}
              disabled={recommendationRefresh.diet || isRecommendationLoading}
              className="text-gray-400 hover:text-orange-500 hover:bg-orange-50 transition-all p-2 rounded-full cursor-pointer group focus:outline-none disabled:opacity-50"
            >
              <RefreshCw className={`w-5 h-5 transition-transform duration-500 ${recommendationRefresh.diet ? 'animate-spin' : 'group-hover:rotate-180'}`} />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {DIET_SLOTS.map((mealType) => {
              const dietData = aiRecommendations.diet[mealType as 'breakfast' | 'lunch' | 'dinner'];
              const dietHighlightId = getDietRecommendationHighlightId(mealType);
              const isDietHighlighted = highlightedRecommendationIds.includes(dietHighlightId);
              const mealLabel = mealType === 'breakfast' ? '아침' : mealType === 'lunch' ? '점심' : '저녁';
              return (
                <div
                  key={mealType}
                  onMouseEnter={() => dismissRecommendationHighlight(dietHighlightId)}
                  onPointerDown={() => dismissRecommendationHighlight(dietHighlightId)}
                  className={`rounded-2xl p-4 border relative group flex flex-col transition-all ${isDietHighlighted ? 'bg-emerald-50/70 border-emerald-200 ring-2 ring-emerald-200 shadow-sm' : 'bg-orange-50/30 border-orange-100/50'}`}
                >
                  {isDietHighlighted && (
                    <span className="absolute right-4 top-12 rounded-full bg-emerald-500 px-2.5 py-1 text-[10px] font-bold text-white shadow-sm">
                      방금 반영됨
                    </span>
                  )}
                  <div className="flex justify-between items-center mb-3">
                    <span className="px-2.5 py-1 bg-orange-50 border border-orange-100 text-orange-600 rounded-full text-xs font-bold shadow-sm">{mealLabel}</span>
                    {dietData && (
                      <button aria-label={`${mealType} 식단 추천 변경`} onClick={() => openDietRecommendationPopup(dietData, mealType)} disabled={isDietRecommendationAdded(mealType, dietData)} className="p-1.5 bg-orange-500 shadow-md text-white hover:bg-orange-600 rounded-full transition-colors z-10 disabled:bg-gray-300 disabled:hover:bg-gray-300">
                        <Plus className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                  {dietData ? (
                    <div className="flex-1 flex flex-col">
                      <div className="w-full h-24 bg-orange-50/50 border border-orange-100 shadow-sm rounded-xl mb-3 flex items-center justify-center">
                        <span className="text-4xl">{getFoodEmoji(dietData.name)}</span>
                      </div>
                      <h4 className="font-bold text-gray-900 text-sm mb-1">{compactText(dietData.name, 30)}</h4>
                      <p className="text-xs text-gray-500 mb-2 flex-1">{compactText(dietData.desc, 42)}</p>
                      <div className="text-xs font-bold text-orange-500">{dietData.calories} kcal</div>
                      {isDietRecommendationAdded(mealType, dietData) && <p className="mt-2 text-[11px] font-bold text-emerald-500">추가됨</p>}
                    </div>
                  ) : (
                    <div className="flex-1 flex items-center justify-center min-h-[120px] text-xs text-gray-400">추천 식단 없음</div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </section>
    </div>

    {/* Workout Add Popup */}
    <AnimatePresence>
      {workoutPopup.isOpen && workoutPopup.target && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setWorkoutPopup({ isOpen: false, target: null })} />
          <motion.div initial={{ opacity: 0, scale: 0.95, y: 20 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: 20 }} className="bg-white rounded-3xl shadow-xl w-full max-w-sm overflow-hidden z-10">
            <div className="p-6 text-center">
              <div className="w-14 h-14 bg-blue-50 text-blue-500 rounded-full flex items-center justify-center mx-auto mb-4">
                <Calendar className="w-7 h-7" />
              </div>
              <h3 className="font-bold text-lg text-gray-900 mb-2">일정 추가</h3>
              <p className="text-sm text-gray-600 mb-6">
                <strong className="text-blue-600">{workoutPopup.target.name}</strong> 운동을<br />
                오늘의 캘린더 일정에 추가하시겠어요?
              </p>
              <div className="flex space-x-3">
                <button onClick={() => setWorkoutPopup({ isOpen: false, target: null })} className="flex-1 py-3 bg-gray-100 text-gray-700 font-bold rounded-xl hover:bg-gray-200 transition-colors">취소</button>
                <button onClick={confirmWorkoutRecommendationAdd} className="flex-1 py-3 bg-blue-600 text-white font-bold rounded-xl hover:bg-blue-700 transition-colors shadow-md">추가하기</button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>

    {/* Diet Replace Popup */}
    <AnimatePresence>
      {dietPopup.isOpen && dietPopup.target && dietPopup.mealType && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setDietPopup({ isOpen: false, target: null, mealType: null })} />
          <motion.div initial={{ opacity: 0, scale: 0.95, y: 20 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: 20 }} className="bg-white rounded-3xl shadow-xl w-full max-w-md overflow-hidden z-10">
            <div className="p-6">
              <h3 className="font-bold text-lg text-gray-900 mb-1 text-center">식단 교체</h3>
              <p className="text-sm text-gray-500 mb-6 text-center">이 식단을 오늘의 캘린더 일정에 반영하시겠습니까?</p>

              <div className="flex items-center justify-between bg-gray-50 rounded-2xl p-4 mb-6 relative">
                {/* Old Diet */}
                <div className="flex-1 text-center max-w-[45%]">
                  <span className="text-[10px] font-bold text-gray-400 block mb-2">기존 식단</span>
                  {(() => {
                    const todayPlan = getPlanByDate(new Date());
                    const oldDiet = todayPlan?.diets.find((d) =>
                      matchesMealSlot(d.type, dietPopup.mealType as DietSlot)
                    );

                    if (oldDiet) {
                      return (
                        <div>
                          <div className="w-12 h-12 bg-white border border-gray-200 rounded-lg mx-auto mb-2 flex items-center justify-center text-gray-400 shadow-sm text-2xl">{getFoodEmoji(oldDiet.name)}</div>
                          <p className="text-xs font-bold text-gray-700 truncate px-1">{oldDiet.name}</p>
                          <p className="text-[10px] text-gray-500">{oldDiet.kcal}</p>
                        </div>
                      );
                    }
                    return (
                      <div className="flex flex-col items-center justify-center h-full min-h-[80px]">
                        <p className="text-xs text-gray-400 font-medium">비어 있음</p>
                      </div>
                    );
                  })()}
                </div>

                {/* Arrow */}
                <div className="px-1 text-gray-300">
                  <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" /></svg>
                </div>

                {/* New Diet */}
                <div className="flex-1 text-center max-w-[45%]">
                  <span className="text-[10px] font-bold text-orange-500 block mb-2">새로운 추천 식단</span>
                  <div>
                    <div className="w-12 h-12 bg-orange-50 border border-orange-100 text-orange-400 rounded-lg mx-auto mb-2 flex items-center justify-center shadow-sm text-2xl">{getFoodEmoji(dietPopup.target.name)}</div>
                    <p className="text-xs font-bold text-orange-600 truncate px-1">{dietPopup.target.name}</p>
                    <p className="text-[10px] text-orange-400 font-bold">{dietPopup.target.calories} kcal</p>
                  </div>
                </div>
              </div>

              <div className="flex space-x-3">
                <button onClick={() => setDietPopup({ isOpen: false, target: null, mealType: null })} className="flex-1 py-3 bg-gray-100 text-gray-700 font-bold rounded-xl hover:bg-gray-200 transition-colors">취소</button>
                <button onClick={confirmDietRecommendationReplace} className="flex-1 py-3 bg-orange-500 text-white font-bold rounded-xl hover:bg-orange-600 transition-colors shadow-md">변경하기</button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>

    {/* Planner Modal */}
    <AnimatePresence>
      {isPlannerOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setIsPlannerOpen(false)}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="bg-white rounded-3xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.15)] w-full max-w-sm overflow-hidden z-10"
          >
            <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-blue-50/50">
              <div className="flex items-center space-x-2">
                <CheckSquare className="w-5 h-5 text-[#2563eb]" />
                <h3 className="font-bold text-lg text-gray-900">오늘 할 일</h3>
              </div>
              <button onClick={() => setIsPlannerOpen(false)} className="text-gray-400 hover:text-gray-600 transition-colors p-1 bg-white rounded-full shadow-sm">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6">
              <div className="space-y-3 max-h-[300px] overflow-y-auto mb-6 pr-1 custom-scrollbar">
                {todos.length === 0 ? (
                  <p className="text-center text-gray-400 py-4 text-sm font-medium">오늘은 어떤 운동을 해볼까요?</p>
                ) : (
                  todos.map(todo => (
                    <div key={todo.id} className={`flex items-center justify-between p-3 rounded-xl border transition-all duration-200 ${todo.completed ? 'bg-gray-50 border-gray-100' : 'bg-white border-blue-100 shadow-[0_2px_8px_-4px_rgba(37,99,235,0.1)]'}`}>
                      <div className="flex items-center space-x-3 cursor-pointer select-none flex-1" onClick={() => toggleTodo(todo.id)}>
                        <div className={`w-5 h-5 rounded flex items-center justify-center border transition-colors ${todo.completed ? 'bg-[#2563eb] border-[#2563eb] text-white' : 'border-gray-300 bg-white'}`}>
                          {todo.completed && <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" /></svg>}
                        </div>
                        <span className={`text-sm font-medium transition-all ${todo.completed ? 'line-through text-gray-400' : 'text-gray-700'}`}>
                          {todo.text}
                          {todo.calories && <span className="ml-1.5 text-[10px] text-orange-400 bg-orange-50 px-1.5 py-0.5 rounded-md font-bold">{todo.calories} kcal</span>}
                          {todo.type === 'workout' && <span className="ml-1.5 text-[10px] text-blue-500 bg-blue-50 px-1.5 py-0.5 rounded-md font-bold">운동</span>}
                        </span>
                      </div>
                      <button onClick={() => deleteTodo(todo.id)} className="text-gray-300 hover:text-red-500 transition-colors p-1.5 focus:outline-none ml-2">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  ))
                )}
              </div>

              <form onSubmit={addTodo} className="flex gap-2 relative">
                <input
                  type="text"
                  value={newTodo}
                  onChange={(e) => setNewTodo(e.target.value)}
                  placeholder="새로운 할 일 추가"
                  className="flex-1 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all"
                />
                <button
                  type="submit"
                  disabled={!newTodo.trim()}
                  className="bg-[#2563eb] text-white p-3 rounded-xl shadow-[0_4px_12px_rgba(37,99,235,0.2)] hover:bg-blue-700 hover:shadow-[0_6px_16px_rgba(37,99,235,0.3)] transition-all disabled:opacity-50 disabled:shadow-none focus:outline-none"
                >
                  <Plus className="w-5 h-5" />
                </button>
              </form>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>

    {/* Diet Modal */}
    <AnimatePresence>
      {isDietModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setIsDietModalOpen(false)}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="bg-white rounded-3xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.15)] w-full max-w-sm overflow-hidden z-10"
          >
            <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-blue-50/50">
              <div className="flex items-center space-x-2">
                <Utensils className="w-5 h-5 text-[#2563eb]" />
                <h3 className="font-bold text-lg text-gray-900">식단 기록하기</h3>
              </div>
              <button onClick={() => { setIsDietModalOpen(false); setTimeout(() => { setDietAnalysis(null); setManualInput({ calories: '', carbs: '', protein: '', fat: '' }); }, 300); }} className="text-gray-400 hover:text-gray-600 transition-colors p-1 bg-white rounded-full shadow-sm">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6">
              <form onSubmit={handleDietSubmit} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="col-span-2">
                    <div className="flex justify-between items-center mb-2">
                      <label className="block text-sm font-bold text-gray-700">섭취 칼로리 (kcal)</label>
                      <button type="button" onClick={() => setResetConfirmModal({ isOpen: true, target: 'calories' })} className="text-[11px] text-gray-400 hover:text-red-500 transition-colors bg-gray-100 hover:bg-red-50 px-2 py-1 rounded-md font-bold">오늘 섭취량 초기화</button>
                    </div>
                    <input
                      type="number"
                      value={manualInput.calories}
                      onChange={(e) => setManualInput({ ...manualInput, calories: e.target.value })}
                      placeholder="예) 500"
                      className="w-full bg-gray-50 border border-gray-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all"
                    />
                  </div>
                  <div className="col-span-2 flex justify-between items-end mt-1 -mb-2">
                    <label className="block text-sm font-bold text-gray-700">상세 영양소 (g)</label>
                    <button type="button" onClick={() => setResetConfirmModal({ isOpen: true, target: 'macros' })} className="text-[11px] text-gray-400 hover:text-red-500 transition-colors bg-gray-100 hover:bg-red-50 px-2 py-1 rounded-md font-bold">오늘 섭취량 초기화</button>
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-700 mb-1">탄수화물 (g)</label>
                    <input
                      type="number"
                      value={manualInput.carbs}
                      onChange={(e) => setManualInput({ ...manualInput, carbs: e.target.value })}
                      placeholder="0"
                      className="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all text-center"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-700 mb-1">단백질 (g)</label>
                    <input
                      type="number"
                      value={manualInput.protein}
                      onChange={(e) => setManualInput({ ...manualInput, protein: e.target.value })}
                      placeholder="0"
                      className="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all text-center"
                    />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-xs font-bold text-gray-700 mb-1">지방 (g)</label>
                    <input
                      type="number"
                      value={manualInput.fat}
                      onChange={(e) => setManualInput({ ...manualInput, fat: e.target.value })}
                      placeholder="0"
                      className="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all text-center"
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={(!manualInput.calories && !manualInput.carbs && !manualInput.protein && !manualInput.fat) || isAnalyzing}
                  className="w-full bg-[#2563eb] text-white font-bold py-3.5 rounded-xl shadow-[0_4px_12px_rgba(37,99,235,0.2)] hover:bg-blue-700 transition-all disabled:opacity-50 disabled:shadow-none flex justify-center items-center h-[52px]"
                >
                  {isAnalyzing ? (
                    <div className="flex space-x-2 items-center">
                      <div className="w-2 h-2 bg-white rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                      <div className="w-2 h-2 bg-white rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                      <div className="w-2 h-2 bg-white rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                      <span className="ml-2">저장 중...</span>
                    </div>
                  ) : (
                    '영양소 기록하기'
                  )}
                </button>
              </form>

              <AnimatePresence>
                {dietAnalysis && (
                  <motion.div
                    initial={{ opacity: 0, height: 0, marginTop: 0 }}
                    animate={{ opacity: 1, height: 'auto', marginTop: 24 }}
                    exit={{ opacity: 0, height: 0, marginTop: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="bg-blue-50/50 border border-blue-100 rounded-2xl p-5 space-y-3 relative overflow-hidden">
                      <div className="absolute top-0 right-0 w-24 h-24 bg-blue-500/5 rounded-full blur-xl -mr-10 -mt-10"></div>
                      <div className="flex justify-between items-center relative z-10">
                        <span className="text-sm font-bold text-gray-600">추가된 칼로리</span>
                        <span className="text-lg font-extrabold text-[#2563eb]">{dietAnalysis.calories} kcal</span>
                      </div>
                      <div className="flex justify-between items-center relative z-10 text-xs font-medium text-gray-500">
                        <span>탄 {dietAnalysis.carbs}g • 단 {dietAnalysis.protein}g • 지 {dietAnalysis.fat}g</span>
                      </div>
                      <div className="h-px w-full bg-blue-100/50 relative z-10"></div>
                      <div className="pt-1">
                        <span className="inline-block px-2 py-1 bg-blue-100 text-blue-700 text-[10px] font-bold rounded-md mb-2 relative z-10">AI의 한 줄 조언</span>
                        <div className="flex items-start space-x-2.5 relative z-10">
                          <div className="w-6 h-6 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                            <span className="text-[10px]">🤖</span>
                          </div>
                          <p className="text-sm text-gray-700 leading-relaxed font-medium">
                            {dietAnalysis.message}
                          </p>
                        </div>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>

    {/* Calorie Goal Modal */}
    <AnimatePresence>
      {isCalorieModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setIsCalorieModalOpen(false)}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="bg-white rounded-3xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.15)] w-full max-w-sm overflow-hidden z-10"
          >
            <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-blue-50/50">
              <div className="flex items-center space-x-2">
                <Utensils className="w-5 h-5 text-[#2563eb]" />
                <h3 className="font-bold text-lg text-gray-900">목표 칼로리 설정</h3>
              </div>
              <button onClick={() => { setIsCalorieModalOpen(false); setGoalErrorMsg(''); }} className="text-gray-400 hover:text-gray-600 transition-colors p-1 bg-white rounded-full shadow-sm">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6">
              <form onSubmit={handleCalorieSubmit} className="space-y-4">
                <div className="bg-blue-50 text-blue-800 p-3 rounded-xl text-sm mb-4 border border-blue-100 font-medium text-center">
                  💡 체형 맞춤형 권장 섭취량: <br /> 하루 <span className="font-bold text-blue-600">{recommendedCalories} kcal</span>
                </div>
                <div>
                  <label className="block text-sm font-bold text-gray-700 mb-1.5">하루 목표 칼로리 (kcal)</label>
                  <input
                    type="number"
                    value={goalInput.calories}
                    onChange={(e) => setGoalInput({ ...goalInput, calories: e.target.value })}
                    placeholder="예) 2000"
                    className="w-full bg-gray-50 border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all"
                  />
                </div>

                {goalErrorMsg && (
                  <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-red-500 text-xs font-bold text-center mt-2">
                    {goalErrorMsg}
                  </motion.p>
                )}

                <button
                  type="submit"
                  disabled={isGoalSaving}
                  className="w-full bg-[#2563eb] text-white font-bold py-3.5 rounded-xl shadow-[0_4px_12px_rgba(37,99,235,0.2)] hover:bg-blue-700 transition-all disabled:opacity-50 disabled:shadow-none mt-2"
                >
                  {isGoalSaving ? '저장 중...' : '목표 저장'}
                </button>
              </form>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>

    {/* Nutrient Goal Modal */}
    <AnimatePresence>
      {isNutrientModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setIsNutrientModalOpen(false)}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="bg-white rounded-3xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.15)] w-full max-w-sm overflow-hidden z-10"
          >
            <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-blue-50/50">
              <div className="flex items-center space-x-2">
                <Activity className="w-5 h-5 text-[#2563eb]" />
                <h3 className="font-bold text-lg text-gray-900">목표 영양소 설정</h3>
              </div>
              <button onClick={() => { setIsNutrientModalOpen(false); setGoalErrorMsg(''); }} className="text-gray-400 hover:text-gray-600 transition-colors p-1 bg-white rounded-full shadow-sm">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6">
              <form onSubmit={handleNutrientSubmit} className="space-y-4">
                <div className="bg-emerald-50 text-emerald-800 p-3 rounded-xl text-sm mb-4 border border-emerald-100 font-medium text-center">
                  💡 체형 맞춤형 권장 탄단지 (5:3:2) <br />
                  <span className="font-bold">탄 {Math.round((recommendedCalories * 0.5) / 4)}g / 단 {Math.round((recommendedCalories * 0.3) / 4)}g / 지 {Math.round((recommendedCalories * 0.2) / 9)}g</span>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-bold text-gray-700 mb-1">탄수화물 (g)</label>
                    <input
                      type="number"
                      value={goalInput.carbs}
                      onChange={(e) => setGoalInput({ ...goalInput, carbs: e.target.value })}
                      className="w-full bg-gray-50 border border-gray-200 rounded-xl px-2 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all text-center"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-700 mb-1">단백질 (g)</label>
                    <input
                      type="number"
                      value={goalInput.protein}
                      onChange={(e) => setGoalInput({ ...goalInput, protein: e.target.value })}
                      className="w-full bg-gray-50 border border-gray-200 rounded-xl px-2 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all text-center"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-700 mb-1">지방 (g)</label>
                    <input
                      type="number"
                      value={goalInput.fat}
                      onChange={(e) => setGoalInput({ ...goalInput, fat: e.target.value })}
                      className="w-full bg-gray-50 border border-gray-200 rounded-xl px-2 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563eb]/20 focus:border-[#2563eb] transition-all text-center"
                    />
                  </div>
                </div>

                {goalErrorMsg && (
                  <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-red-500 text-xs font-bold text-center mt-2">
                    {goalErrorMsg}
                  </motion.p>
                )}

                <button
                  type="submit"
                  disabled={isGoalSaving}
                  className="w-full bg-[#2563eb] text-white font-bold py-3.5 rounded-xl shadow-[0_4px_12px_rgba(37,99,235,0.2)] hover:bg-blue-700 transition-all disabled:opacity-50 disabled:shadow-none mt-2"
                >
                  {isGoalSaving ? '저장 중...' : '목표 저장'}
                </button>
              </form>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
    {/* Alert Popup */}
    <AnimatePresence>
      {alertPopup.isOpen && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setAlertPopup({ isOpen: false, message: '' })} />
          <motion.div initial={{ opacity: 0, scale: 0.95, y: 20 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: 20 }} className="bg-white rounded-3xl shadow-xl w-full max-w-sm overflow-hidden z-10 px-6 py-8 text-center">
            <div className="w-16 h-16 bg-blue-50 text-blue-500 rounded-full flex items-center justify-center mx-auto mb-4">
              <Info className="w-8 h-8" />
            </div>
            <h3 className="font-bold text-lg text-gray-900 mb-2">알림</h3>
            <p className="text-gray-600 mb-6 font-medium">{alertPopup.message}</p>
            <button onClick={() => setAlertPopup({ isOpen: false, message: '' })} className="w-full py-3.5 bg-[#2563eb] text-white font-bold rounded-xl hover:bg-blue-700 transition-colors shadow-md">
              확인
            </button>
          </motion.div>
        </div>
      )}
    </AnimatePresence>

    <AnimatePresence>
      {planSyncToast.isOpen && (
        <motion.div
          data-testid="plan-sync-toast"
          role="status"
          aria-live="polite"
          initial={{ opacity: 0, y: 24, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 16, scale: 0.98 }}
          transition={{ type: 'spring', damping: 24, stiffness: 280 }}
          className="fixed inset-x-4 bottom-24 z-[115] mx-auto max-w-md rounded-2xl border border-emerald-100 bg-white p-4 shadow-[0_18px_50px_-20px_rgba(15,23,42,0.45)] md:bottom-8"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600">
              <CheckCircle2 className="h-5 w-5" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-black text-gray-900">
                {planSyncToast.title}
              </p>
              <p className="mt-1 truncate text-xs font-semibold text-gray-500">
                {planSyncToast.message}
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  data-testid="plan-sync-toast-plan-link"
                  type="button"
                  onClick={openPlanFromToast}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3 py-2 text-xs font-black text-white shadow-sm transition-colors hover:bg-emerald-700"
                >
                  플랜 보기
                  <ArrowRight className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={hidePlanSyncToast}
                  className="rounded-xl bg-gray-100 px-3 py-2 text-xs font-black text-gray-600 transition-colors hover:bg-gray-200"
                >
                  닫기
                </button>
              </div>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>

    {/* Reset Confirmation Modal */}
    <AnimatePresence>
      {resetConfirmModal.isOpen && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm" onClick={() => setResetConfirmModal({ isOpen: false, target: null })} />
          <motion.div initial={{ opacity: 0, scale: 0.95, y: 20 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: 20 }} className="bg-white rounded-3xl shadow-xl w-full max-w-sm overflow-hidden z-10 px-6 py-8 text-center border border-gray-100">
            <div className="w-16 h-16 bg-red-50 text-red-500 rounded-full flex items-center justify-center mx-auto mb-4">
              <RefreshCw className="w-8 h-8" />
            </div>
            <h3 className="font-bold text-lg text-gray-900 mb-2">섭취량 초기화</h3>
            <p className="text-gray-500 mb-6 font-medium text-sm">
              정말 오늘 기록을 초기화할까요?<br />
              진행 중인 {resetConfirmModal.target === 'calories' ? '칼로리' : '영양소(탄단지)'} 기록이 0으로 변경됩니다.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setResetConfirmModal({ isOpen: false, target: null })} className="flex-1 py-3.5 bg-gray-100 text-gray-600 font-bold rounded-xl hover:bg-gray-200 transition-colors">
                취소
              </button>
              <button onClick={confirmResetIntake} className="flex-1 py-3.5 bg-red-500 text-white font-bold rounded-xl hover:bg-red-600 transition-colors shadow-[0_4px_12px_rgba(239,68,68,0.3)]">
                확인 (초기화)
              </button>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  </div>
);
}
