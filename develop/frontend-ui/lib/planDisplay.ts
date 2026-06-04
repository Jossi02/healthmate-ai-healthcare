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

export type DietSlotDisplayType = "Breakfast" | "Lunch" | "Dinner";

export type SplitDietEntry = {
  type: DietSlotDisplayType;
  name: string;
};

const DIET_SLOT_PATTERN = /(아침|점심|저녁|breakfast|lunch|dinner)\s*[:：-]\s*/gi;
const DIET_SLOT_PREFIX_PATTERN = /^\s*(아침|점심|저녁|breakfast|lunch|dinner)\s*[:：-]\s*/i;

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

function hasStrengthKeyword(text: string) {
  return text.includes("근력") || text.includes("strength");
}

function normalizeText(...values: Array<string | undefined | null>) {
  return values
    .filter(Boolean)
    .join(" ")
    .trim()
    .toLowerCase();
}

function classifyWorkoutText(text: string, options: { allowStretchingFirst?: boolean } = {}) {
  const hasUpper = hasAnyKeyword(text, UPPER_KEYWORDS);
  const hasLower = hasAnyKeyword(text, LOWER_KEYWORDS);
  const hasCardio = hasAnyKeyword(text, CARDIO_KEYWORDS);
  const hasStretching = hasAnyKeyword(text, STRETCHING_KEYWORDS);
  const hasFullBody = hasAnyKeyword(text, FULL_BODY_KEYWORDS);
  const hasStrength = hasStrengthKeyword(text);

  if (options.allowStretchingFirst && hasStretching && !hasUpper && !hasLower && !hasCardio && !hasStrength) {
    return "stretching";
  }
  if ((hasUpper && hasLower) || (hasFullBody && (hasUpper || hasLower || hasStrength))) {
    return "full_body";
  }
  if (hasUpper) return "upper_body";
  if (hasLower) return "lower_body";
  if (hasCardio) return "cardio";
  if (hasStretching) return "stretching";
  if (hasFullBody) return "full_body";
  return null;
}

export function classifyWorkoutGroup(workout: Partial<WorkoutLike>): WorkoutGroupKey {
  const titleText = normalizeText(workout.title);
  const typeText = normalizeText(workout.type, workout.level);
  const combined = `${titleText} ${typeText}`.trim();
  const titleCategory = classifyWorkoutText(titleText, { allowStretchingFirst: true });
  if (titleCategory) return titleCategory;

  if (typeText.includes("upper_body")) return "upper_body";
  if (typeText.includes("lower_body")) return "lower_body";
  if (typeText.includes("cardio") || typeText.includes("유산소")) return "cardio";
  if (typeText.includes("full_body") || typeText.includes("전신")) return "full_body";
  if (typeText.includes("stretching") || typeText.includes("스트레칭")) return "stretching";

  const combinedCategory = classifyWorkoutText(combined);
  if (combinedCategory) return combinedCategory;
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

export function getDietSlotDisplayType(value?: string): DietSlotDisplayType | null {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized.includes("breakfast") || normalized.includes("아침")) return "Breakfast";
  if (normalized.includes("lunch") || normalized.includes("점심")) return "Lunch";
  if (normalized.includes("dinner") || normalized.includes("저녁")) return "Dinner";
  return null;
}

export function stripDietSlotPrefix(value: string | undefined | null) {
  return String(value || "").replace(DIET_SLOT_PREFIX_PATTERN, "").trim();
}

function trimDietSegment(value: string) {
  return value.replace(/^[\s,，;|/]+|[\s,，;|/]+$/g, "").trim();
}

export function splitCompoundDietText(value: string | undefined | null): SplitDietEntry[] {
  const raw = String(value || "").replace(/\s+/g, " ").trim();
  if (!raw) return [];

  const matches = [...raw.matchAll(DIET_SLOT_PATTERN)];
  if (matches.length === 0) return [];

  return matches
    .map((match, index) => {
      const type = getDietSlotDisplayType(match[1]);
      const start = (match.index ?? 0) + match[0].length;
      const end = matches[index + 1]?.index ?? raw.length;
      const name = cleanDietPlanValue(trimDietSegment(raw.slice(start, end)));
      return type && name ? { type, name } : null;
    })
    .filter((item): item is SplitDietEntry => Boolean(item));
}

export function normalizeDietKcal(value: string | number | undefined | null) {
  const matched = String(value ?? "").match(/\d+/);
  if (!matched) return "";
  const calories = Number(matched[0]);
  if (!Number.isFinite(calories) || calories <= 0) return "";
  return `${Math.round(calories)} kcal`;
}

export function compactText(value: string | undefined | null, maxLength = 52) {
  const cleaned = String(value || "")
    .replace(/\s+/g, " ")
    .trim();
  if (cleaned.length <= maxLength) return cleaned;
  return `${cleaned.slice(0, maxLength - 1).trim()}…`;
}

const DIET_EVIDENCE_MARKERS = [
  "\uadfc\uac70",
  "\uc774\uc720",
  "\ucd94\ucc9c \uc774\uc720",
  "\uc120\uc815 \uc774\uc720",
  "\uc124\uba85",
  "\uace0\ub824",
  "\uc8fc\uc758",
  "\ucc38\uace0",
  "\uc54c\ub808\ub974\uae30",
  "\uc9c8\ud658",
  "\ubaa9\ud45c",
  "reason",
  "evidence",
  "rationale",
  "because",
  "note",
  "guideline",
  "profile",
  "allergy",
  "disease",
  "constraint",
];

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function stripDietEvidenceTail(value: string) {
  let next = value;
  for (const marker of DIET_EVIDENCE_MARKERS) {
    const pattern = new RegExp(
      `\\s*(?:[/|;,.]\\s*)?${escapeRegExp(marker)}\\s*(?:[:\\-]|\\s).*`,
      "i"
    );
    next = next.replace(pattern, "");
  }
  return next.trim();
}

function isDietEvidencePiece(value: string) {
  const normalized = value.toLowerCase();
  return DIET_EVIDENCE_MARKERS.some((marker) =>
    normalized.includes(marker.toLowerCase())
  );
}

export function cleanDietPlanValue(value: string | undefined | null) {
  const raw = String(value || "")
    .replace(/\s+/g, " ")
    .trim();
  if (!raw) return "";

  const withoutEvidenceTail = stripDietEvidenceTail(raw);
  const pieces = withoutEvidenceTail
    .split(/\s*(?:\/|\||;)\s*/)
    .map((piece) => stripDietEvidenceTail(piece).trim())
    .filter(Boolean)
    .filter((piece) => !isDietEvidencePiece(piece));

  return (pieces.length ? pieces.join(", ") : withoutEvidenceTail).trim();
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
  const splitEntries = splitCompoundDietText(diet.name);
  const splitEntry = splitEntries[0] || null;
  const cleaned =
    splitEntry?.name ||
    cleanDietPlanValue(stripDietSlotPrefix(diet.name)) ||
    cleanDietPlanValue(diet.desc);
  const parts = cleaned
    .split(/\s*(?:,|·|\+|와|과)\s*/)
    .map((part) => part.trim())
    .filter(Boolean);

  const concreteParts = parts.length >= 2 ? parts.slice(0, 3) : [cleaned];
  const title = compactText(concreteParts.join(" + "), 34);

  return {
    mealLabel: getDietSlotLabel(splitEntry?.type || diet.type),
    title: title || getDietSlotLabel(splitEntry?.type || diet.type),
    subtitle: "",
    detail: cleaned || diet.name,
    kcal: normalizeDietKcal(diet.kcal),
  };
}
