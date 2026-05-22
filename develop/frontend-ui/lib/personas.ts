export const AI_PERSONAS = [
  {
    id: "cheer_sis",
    name: "응원 누나",
    shortLabel: "응원",
    description: "밝게 받아주고 다시 움직이게 해주는 치어 코치",
    tone: "친근하고 에너지 있는 응원",
    intro:
      "왔어? 오늘은 내가 옆에서 텐션 맞춰줄게. 운동이든 식단이든 지금 몸 상태부터 편하게 말해줘.",
    profileLine:
      "실패해도 분위기를 살려서 다시 시작하게 도와주는 코치예요.",
    imageSrc: "/personas/cheer_sis.jpg",
    imageAlt: "밝은 치어 코치 스타일의 응원 누나 아바타",
    accent: "from-rose-400 to-amber-400",
    selectedClass: "border-rose-300 bg-rose-50 text-rose-700",
  },
  {
    id: "soft_senior",
    name: "다정 선배",
    shortLabel: "다정",
    description: "무리하지 않게 차분히 챙겨주는 안정형 코치",
    tone: "안심되고 부드러운 격려",
    intro:
      "오늘도 와줘서 좋아요. 부담 갖지 말고, 지금 가능한 만큼만 같이 정리해볼까요?",
    profileLine:
      "지친 날에도 사용자의 속도를 존중하면서 조용히 잡아주는 코치예요.",
    imageSrc: "/personas/soft_senior.jpg",
    imageAlt: "부드럽게 챙겨주는 다정 선배 아바타",
    accent: "from-teal-400 to-emerald-500",
    selectedClass: "border-emerald-300 bg-emerald-50 text-emerald-700",
  },
  {
    id: "strict_trainer",
    name: "직진 PT쌤",
    shortLabel: "직진",
    description: "짧고 명확하게 실행을 밀어주는 코치",
    tone: "명확하고 단단한 지시",
    intro:
      "좋아, 오늘은 복잡하게 가지 말자. 네 컨디션 말해주면 바로 실행 가능한 계획으로 줄게.",
    profileLine:
      "미루는 시간을 줄이고 바로 움직일 수 있게 정리해주는 코치예요.",
    imageSrc: "/personas/strict_trainer.jpg",
    imageAlt: "헤드셋을 낀 단호한 직진 PT쌤 아바타",
    accent: "from-slate-700 to-zinc-500",
    selectedClass: "border-slate-300 bg-slate-100 text-slate-800",
  },
  {
    id: "science_coach",
    name: "분석 코치",
    shortLabel: "분석",
    description: "이유와 근거를 차분하게 설명하는 코치",
    tone: "담백하고 효율 중심",
    intro:
      "지금 기록과 조건을 같이 볼게요. 목표, 시간, 컨디션 중 무엇부터 맞춰볼까요?",
    profileLine:
      "감보다 근거를 선호하는 사용자에게 이유까지 설명해주는 코치예요.",
    imageSrc: "/personas/science_coach.jpg",
    imageAlt: "안경과 차트가 있는 분석 코치 아바타",
    accent: "from-sky-500 to-cyan-400",
    selectedClass: "border-sky-300 bg-sky-50 text-sky-700",
  },
  {
    id: "playful_buddy",
    name: "운동 메이트",
    shortLabel: "메이트",
    description: "가볍게 같이 움직이는 친구 같은 코치",
    tone: "친근하고 동행하는 말투",
    intro:
      "왔구나. 오늘도 혼자 하는 느낌 안 나게 같이 가보자. 지금 뭐부터 해볼까?",
    profileLine:
      "혼자 운동하는 느낌을 줄이고 친구처럼 같이 움직이는 코치예요.",
    imageSrc: "/personas/playful_buddy.jpg",
    imageAlt: "캐주얼한 운동 메이트 아바타",
    accent: "from-violet-500 to-fuchsia-400",
    selectedClass: "border-violet-300 bg-violet-50 text-violet-700",
  },
  {
    id: "daily_manager",
    name: "생활 매니저",
    shortLabel: "관리",
    description: "루틴과 일정을 깔끔하게 정리하는 코치",
    tone: "체계적인 관리",
    intro:
      "좋아요. 오늘 루틴을 한 번 정리해볼게요. 운동, 식단, 기록 중 어디부터 볼까요?",
    profileLine:
      "해야 할 일을 작은 루틴과 체크리스트로 정돈해주는 코치예요.",
    imageSrc: "/personas/daily_manager.jpg",
    imageAlt: "체크리스트를 든 생활 매니저 아바타",
    accent: "from-lime-500 to-green-500",
    selectedClass: "border-lime-300 bg-lime-50 text-lime-700",
  },
] as const satisfies readonly {
  id: string;
  name: string;
  shortLabel: string;
  description: string;
  tone: string;
  intro: string;
  profileLine: string;
  imageSrc: string;
  imageAlt: string;
  accent: string;
  selectedClass: string;
}[];

export type AiPersona = (typeof AI_PERSONAS)[number];
export type AiPersonaId = AiPersona["id"];

const LEGACY_PERSONA_ALIASES: Record<string, AiPersonaId> = {
  default: "cheer_sis",
  warm: "soft_senior",
  spartan: "strict_trainer",
  evidence: "science_coach",
  buddy: "playful_buddy",
};

export function resolveVisiblePersona(personaId?: string | null): AiPersona {
  const normalizedId =
    personaId && personaId in LEGACY_PERSONA_ALIASES
      ? LEGACY_PERSONA_ALIASES[personaId]
      : personaId;

  return (
    AI_PERSONAS.find((persona) => persona.id === normalizedId) ||
    AI_PERSONAS[0]
  );
}

export const PERSONA_CHAT_STARTERS = [
  {
    label: "운동 플랜 작성",
    prompt: "일주일 운동 플랜을 내 상태에 맞춰서 짜줘",
  },
  {
    label: "식단 플랜 작성",
    prompt: "일주일 식단 플랜을 내 상태에 맞춰서 짜줘",
  },
] as const;
