export type WorkoutGroupKey =
  | "cardio"
  | "stretching"
  | "upper_body"
  | "lower_body"
  | "full_body"
  | "other";

type WorkoutLike = {
  title: string;
  type?: string;
  level?: string;
  time?: string;
  calories?: string;
};

type DietLike = {
  type: string;
  name: string;
  desc?: string;
  kcal?: string;
};

export const WORKOUT_GROUP_ORDER: WorkoutGroupKey[] = [
  "cardio",
  "stretching",
  "upper_body",
  "lower_body",
  "full_body",
  "other",
];

export const WORKOUT_GROUP_META: Record<
  WorkoutGroupKey,
  {
    label: string;
    shortLabel: string;
    badgeClass: string;
    panelClass: string;
    ringClass: string;
  }
> = {
  cardio: {
    label: "유산소",
    shortLabel: "유산소",
    badgeClass: "bg-rose-50 text-rose-600 border-rose-100",
    panelClass: "bg-rose-50/50 border-rose-100",
    ringClass: "ring-rose-200",
  },
  stretching: {
    label: "스트레칭",
    shortLabel: "스트레칭",
    badgeClass: "bg-emerald-50 text-emerald-600 border-emerald-100",
    panelClass: "bg-emerald-50/50 border-emerald-100",
    ringClass: "ring-emerald-200",
  },
  upper_body: {
    label: "상체 운동",
    shortLabel: "상체",
    badgeClass: "bg-blue-50 text-blue-600 border-blue-100",
    panelClass: "bg-blue-50/50 border-blue-100",
    ringClass: "ring-blue-200",
  },
  lower_body: {
    label: "하체 운동",
    shortLabel: "하체",
    badgeClass: "bg-indigo-50 text-indigo-600 border-indigo-100",
    panelClass: "bg-indigo-50/50 border-indigo-100",
    ringClass: "ring-indigo-200",
  },
  full_body: {
    label: "전신 운동",
    shortLabel: "전신",
    badgeClass: "bg-orange-50 text-orange-600 border-orange-100",
    panelClass: "bg-orange-50/50 border-orange-100",
    ringClass: "ring-orange-200",
  },
  other: {
    label: "기타 운동",
    shortLabel: "기타",
    badgeClass: "bg-gray-50 text-gray-600 border-gray-100",
    panelClass: "bg-gray-50/60 border-gray-100",
    ringClass: "ring-gray-200",
  },
};

const STRETCHING_KEYWORDS = [
  "stretching",
  "stretch",
  "스트레칭",
  "요가",
  "이완",
  "가동성",
  "mobility",
  "폼롤",
];

const CARDIO_KEYWORDS = [
  "cardio",
  "유산소",
  "걷기",
  "걷는",
  "산책",
  "러닝",
  "달리기",
  "조깅",
  "자전거",
  "사이클",
  "트레드밀",
  "제자리",
  "인터벌",
];

const UPPER_KEYWORDS = [
  "upper_body",
  "상체",
  "푸시업",
  "푸쉬업",
  "로우",
  "밴드",
  "가슴",
  "등",
  "어깨",
  "팔",
  "벤치",
  "풀업",
];

const LOWER_KEYWORDS = [
  "lower_body",
  "하체",
  "스쿼트",
  "런지",
  "브릿지",
  "둔근",
  "엉덩",
  "햄스트링",
  "종아리",
  "레그",
];

const FULL_BODY_KEYWORDS = ["full_body", "전신", "서킷", "circuit", "버드독", "플랭크"];

function hasAnyKeyword(text: string, keywords: string[]) {
  return keywords.some((keyword) => text.includes(keyword.toLowerCase()));
}

function normalizeText(...values: Array<string | undefined | null>) {
  return values
    .filter(Boolean)
    .join(" ")
    .trim()
    .toLowerCase();
}

export function classifyWorkoutGroup(workout: Partial<WorkoutLike>): WorkoutGroupKey {
  const titleText = normalizeText(workout.title);
  const typeText = normalizeText(workout.type, workout.level);
  const combined = `${titleText} ${typeText}`.trim();

  if (hasAnyKeyword(titleText, STRETCHING_KEYWORDS)) return "stretching";
  if (typeText.includes("stretching") || typeText.includes("스트레칭")) return "stretching";
  if (typeText.includes("upper_body")) return "upper_body";
  if (typeText.includes("lower_body")) return "lower_body";
  if (typeText.includes("cardio") || typeText.includes("유산소")) return "cardio";
  if (hasAnyKeyword(combined, STRETCHING_KEYWORDS)) return "stretching";
  if (hasAnyKeyword(combined, CARDIO_KEYWORDS)) return "cardio";
  if (hasAnyKeyword(combined, UPPER_KEYWORDS)) return "upper_body";
  if (hasAnyKeyword(combined, LOWER_KEYWORDS)) return "lower_body";
  if (hasAnyKeyword(combined, FULL_BODY_KEYWORDS)) return "full_body";
  return "other";
}

export function groupWorkoutsByCategory<T extends WorkoutLike>(items: T[]) {
  const grouped = new Map<
    WorkoutGroupKey,
    {
      key: WorkoutGroupKey;
      label: string;
      items: Array<{ item: T; index: number }>;
    }
  >();

  items.forEach((item, index) => {
    const key = classifyWorkoutGroup(item);
    const existing =
      grouped.get(key) ||
      {
        key,
        label: WORKOUT_GROUP_META[key].label,
        items: [],
      };
    existing.items.push({ item, index });
    grouped.set(key, existing);
  });

  return WORKOUT_GROUP_ORDER.map((key) => grouped.get(key)).filter(Boolean) as Array<{
    key: WorkoutGroupKey;
    label: string;
    items: Array<{ item: T; index: number }>;
  }>;
}

export function getDietSlotLabel(type?: string) {
  const normalized = String(type || "").trim().toLowerCase();
  if (normalized.includes("breakfast") || normalized.includes("아침")) return "아침";
  if (normalized.includes("lunch") || normalized.includes("점심")) return "점심";
  if (normalized.includes("dinner") || normalized.includes("저녁")) return "저녁";
  if (normalized.includes("snack") || normalized.includes("간식")) return "간식";
  return type || "식단";
}

export function compactText(value: string | undefined | null, maxLength = 52) {
  const cleaned = String(value || "")
    .replace(/\s+/g, " ")
    .trim();
  if (cleaned.length <= maxLength) return cleaned;
  return `${cleaned.slice(0, maxLength - 1).trim()}…`;
}

const LOW_VALUE_DETAIL_MARKERS = [
  "알레르기",
  "질환",
  "제약",
  "고려",
  "목표",
  "가능 시간",
  "운동 종류",
  "대체",
  "제외",
  "저강도",
  "중급자",
  "초보자",
  "숙련자",
];

export function cleanPlannerDetail(value: string | undefined | null) {
  const raw = String(value || "").trim();
  if (!raw) return "";

  const pieces = raw
    .split(/\s*\/\s*/)
    .map((piece) => piece.trim())
    .filter(Boolean)
    .filter(
      (piece) =>
        !LOW_VALUE_DETAIL_MARKERS.some((marker) => piece.includes(marker))
    );

  return pieces[0] || raw.split(/\s*\/\s*/)[0]?.trim() || raw;
}

export function getDietDisplay(diet: DietLike) {
  const cleaned = cleanPlannerDetail(diet.name);
  const parts = cleaned
    .split(/\s*(?:,|·|\+|와|과)\s*/)
    .map((part) => part.trim())
    .filter(Boolean);

  const concreteParts = parts.length >= 2 ? parts.slice(0, 3) : [cleaned];
  const title = compactText(concreteParts.slice(0, 2).join(" + "), 30);
  const subtitleSource =
    concreteParts.length > 2 ? concreteParts.slice(2).join(" + ") : cleanPlannerDetail(diet.desc);

  return {
    mealLabel: getDietSlotLabel(diet.type),
    title: title || getDietSlotLabel(diet.type),
    subtitle: compactText(subtitleSource, 44),
    detail: cleaned || diet.name,
    kcal: diet.kcal || "",
  };
}
