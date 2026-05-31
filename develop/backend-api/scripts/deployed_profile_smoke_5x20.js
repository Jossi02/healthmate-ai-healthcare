const crypto = require('crypto');

const BACKEND = (process.env.SMOKE_BACKEND_URL || 'http://localhost:3000/api').replace(/\/$/, '');
const PROFILE_LIMIT = Number(process.env.SMOKE_PROFILE_LIMIT || 5);
const TURN_LIMIT = Number(process.env.SMOKE_TURN_LIMIT || 20);
const PASSWORD = process.env.SMOKE_PASSWORD || 'CodexDemo!123';

const profiles = [
  {
    label: 'knee_beginner',
    body: {
      goal: 'weight_loss',
      gender: 'female',
      age: 35,
      height: 164,
      weight: 76,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      injury_history: ['knee pain'],
      allergies: [],
      medical_history: [],
    },
    forbidden: [/점프|버피|전력질주/i],
  },
  {
    label: 'dairy_free_muscle',
    body: {
      goal: 'muscle_gain',
      gender: 'male',
      age: 28,
      height: 178,
      weight: 72,
      exercise_level: 'intermediate',
      available_time_minutes: 45,
      allergies: ['dairy'],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/우유|치즈|그릭\s*요거트|유제품/i],
  },
  {
    label: 'diabetes_diet',
    body: {
      goal: 'stable_blood_sugar',
      gender: 'female',
      age: 51,
      height: 160,
      weight: 68,
      exercise_level: 'beginner',
      available_time_minutes: 25,
      allergies: [],
      medical_history: ['type 2 diabetes'],
      injury_history: [],
    },
    forbidden: [/주스|케이크|시럽|설탕|탄산/i],
  },
  {
    label: 'hypertension_low_time',
    body: {
      goal: 'general_health',
      gender: 'male',
      age: 62,
      height: 170,
      weight: 82,
      exercise_level: 'beginner',
      available_time_minutes: 15,
      allergies: [],
      medical_history: ['hypertension'],
      injury_history: ['back pain'],
    },
    forbidden: [/데드리프트|무거운\s*스쿼트|라면|소시지|베이컨/i],
  },
  {
    label: 'vegetarian_soy_free',
    body: {
      goal: 'balanced_diet',
      gender: 'female',
      age: 24,
      height: 168,
      weight: 58,
      exercise_level: 'intermediate',
      available_time_minutes: 40,
      diet_type: 'vegetarian',
      allergies: ['soy'],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/닭가슴살|소고기|돼지고기|연어|참치|두부|두유/i],
  },
  {
    label: 'back_pain_busy',
    body: {
      goal: 'general_health',
      gender: 'male',
      age: 42,
      height: 174,
      weight: 78,
      exercise_level: 'beginner',
      available_time_minutes: 15,
      lifestyle: '야근이 잦고 시간이 부족함',
      injury_history: ['back pain'],
      allergies: [],
      medical_history: [],
    },
    forbidden: [/데드리프트|굿모닝|무거운\s*스쿼트/i],
  },
  {
    label: 'older_high_weight',
    body: {
      goal: 'weight_loss',
      gender: 'female',
      age: 66,
      height: 158,
      weight: 91,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      allergies: [],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/점프|버피|전력질주|HIIT/i],
  },
  {
    label: 'gluten_free_diet',
    body: {
      goal: 'balanced_diet',
      gender: 'female',
      age: 31,
      height: 162,
      weight: 59,
      exercise_level: 'intermediate',
      available_time_minutes: 35,
      allergies: ['gluten'],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/빵|파스타|밀가루|글루텐/i],
  },
  {
    label: 'egg_free_muscle',
    body: {
      goal: 'muscle_gain',
      gender: 'male',
      age: 33,
      height: 181,
      weight: 80,
      exercise_level: 'intermediate',
      available_time_minutes: 45,
      allergies: ['egg'],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/계란|달걀|egg/i],
  },
  {
    label: 'nut_free_weight_loss',
    body: {
      goal: 'weight_loss',
      gender: 'female',
      age: 29,
      height: 165,
      weight: 64,
      exercise_level: 'beginner',
      available_time_minutes: 25,
      allergies: ['peanut', 'tree nut'],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/견과|땅콩|아몬드|호두|peanut|almond|walnut/i],
  },
  {
    label: 'shellfish_free',
    body: {
      goal: 'balanced_diet',
      gender: 'male',
      age: 37,
      height: 176,
      weight: 74,
      exercise_level: 'intermediate',
      available_time_minutes: 30,
      allergies: ['shellfish'],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/새우|꽃게|대게|게살|갑각류|shrimp|crab/i],
  },
  {
    label: 'vegan_beginner',
    body: {
      goal: 'general_health',
      gender: 'female',
      age: 26,
      height: 163,
      weight: 55,
      exercise_level: 'beginner',
      available_time_minutes: 25,
      diet_type: 'vegan',
      allergies: [],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/닭|소고기|돼지고기|연어|참치|생선|계란|달걀|우유|치즈|요거트/i],
  },
  {
    label: 'asthma_cardio',
    body: {
      goal: 'endurance',
      gender: 'male',
      age: 22,
      height: 172,
      weight: 67,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      allergies: [],
      medical_history: ['asthma'],
      injury_history: [],
    },
    forbidden: [/전력질주|고강도\s*인터벌|HIIT/i],
  },
  {
    label: 'shoulder_pain',
    body: {
      goal: 'muscle_gain',
      gender: 'female',
      age: 39,
      height: 166,
      weight: 62,
      exercise_level: 'beginner',
      available_time_minutes: 25,
      allergies: [],
      medical_history: [],
      injury_history: ['shoulder pain'],
    },
    forbidden: [/버피|마운틴클라이머|딥스/i],
  },
  {
    label: 'wrist_pain',
    body: {
      goal: 'general_health',
      gender: 'male',
      age: 45,
      height: 171,
      weight: 70,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      allergies: [],
      medical_history: [],
      injury_history: ['wrist pain'],
    },
    forbidden: [/버피|플랭크|마운틴클라이머/i],
  },
  {
    label: 'kidney_disease',
    body: {
      goal: 'balanced_diet',
      gender: 'male',
      age: 58,
      height: 169,
      weight: 73,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      allergies: [],
      medical_history: ['kidney disease'],
      injury_history: [],
    },
    forbidden: [/고단백|프로틴|단백질\s*쉐이크|크레아틴/i],
  },
  {
    label: 'gout_diet',
    body: {
      goal: 'balanced_diet',
      gender: 'male',
      age: 49,
      height: 173,
      weight: 86,
      exercise_level: 'beginner',
      available_time_minutes: 25,
      allergies: [],
      medical_history: ['gout'],
      injury_history: [],
    },
    forbidden: [/내장|곱창|정어리|멸치|맥주|새우/i],
  },
  {
    label: 'pregnancy_safe',
    body: {
      goal: 'general_health',
      gender: 'female',
      age: 34,
      height: 164,
      weight: 63,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      allergies: [],
      medical_history: ['pregnancy'],
      injury_history: [],
    },
    forbidden: [/생선회|날달걀|알코올|술|전력질주|HIIT/i],
  },
  {
    label: 'eating_risk',
    body: {
      goal: 'healthy_routine',
      gender: 'female',
      age: 21,
      height: 167,
      weight: 50,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      allergies: [],
      medical_history: ['history of eating disorder'],
      injury_history: [],
    },
    forbidden: [/900kcal|800kcal|단식|굶|원푸드|절식/i],
  },
  {
    label: 'ankle_pain',
    body: {
      goal: 'weight_loss',
      gender: 'male',
      age: 30,
      height: 177,
      weight: 88,
      exercise_level: 'beginner',
      available_time_minutes: 20,
      allergies: [],
      medical_history: [],
      injury_history: ['ankle pain'],
    },
    forbidden: [/점프|버피|전력질주/i],
  },
  {
    label: 'introvert_low_time',
    body: {
      goal: 'general_health',
      gender: 'female',
      age: 27,
      height: 160,
      weight: 54,
      exercise_level: 'beginner',
      available_time_minutes: 10,
      social_orientation: 'introvert',
      allergies: [],
      medical_history: [],
      injury_history: [],
    },
    forbidden: [/60분|70분|80분|90분|1시간/i],
  },
];

const turns = [
  '일주일 운동 플랜 작성해줘',
  '좋아 그걸로 진행해줘',
  '이번 주 식단 플랜 작성해줘',
  '점심을 더 간단하게 바꿔줘',
  '좋아 진행해줘',
  '오늘 컨디션은 좀 피곤해',
  '운동 플랜을 15분 안쪽으로 다시 작성해줘',
  '식단 플랜에서 부담 적은 저녁으로 수정해줘',
  '운동이랑 식단 중 뭐부터 할지 헷갈려',
  '내 프로필에 맞는 주의점만 짧게 알려줘',
  '스트레칭 위주 운동 플랜 작성해줘',
  '좋아 그걸로 저장해줘',
  '단백질 챙기는 식단 플랜 작성해줘',
  '너무 빡세면 줄여줘',
  '오늘 한 운동 체크해줘',
  '식단에서 피해야 하는 음식이 들어갔는지 봐줘',
  '내일 운동 플랜 작성해줘',
  '무릎이나 허리에 부담 없는 버전으로 바꿔줘',
  '좋아 확정해줘',
  '지금까지 플랜이 내 프로필이랑 맞는지 짧게 확인해줘',
];

function uniqueLogin(label) {
  return `codex_${label}_${crypto.randomBytes(4).toString('hex')}`;
}

async function request(path, options = {}) {
  const response = await fetch(`${BACKEND}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    throw new Error(`${options.method || 'GET'} ${path} failed: ${response.status} ${text}`);
  }
  return body;
}

async function createUser(profile) {
  const loginId = uniqueLogin(profile.label);
  const email = `${loginId}@example.com`;
  await request('/v1/auth/signup', {
    method: 'POST',
    body: JSON.stringify({
      login_id: loginId,
      password: PASSWORD,
      nickname: loginId,
      email,
    }),
  });
  const login = await request('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ login_id: loginId, password: PASSWORD }),
  });
  await request('/v1/users/profile', {
    method: 'POST',
    headers: { Authorization: `Bearer ${login.token}` },
    body: JSON.stringify(profile.body),
  });
  return login;
}

function assertResponse(profile, turn, payload) {
  const response = String(payload.response || payload.answer || payload.message || '');
  const conflictText = scrubAllowedConstraintMentions(response);
  const issues = [];
  const intent = String(payload.intent || '');
  const approvalTurn = intent === '계획_승인' || /진행|저장|확정/.test(turn);
  const planLike = /계획|수정/.test(intent) || /작성할까요|수정할까요/.test(response);
  if (response.trim().length < 12 && !approvalTurn) issues.push('short_response');
  if (/[怨諛吏寃媛瑜�]/.test(response)) issues.push('mojibake');
  if (planLike) {
    for (const pattern of profile.forbidden || []) {
      if (pattern.test(conflictText)) issues.push(`profile_conflict:${pattern}`);
    }
  }
  if (/식단\s*플랜|식단.*작성/.test(turn) && /스쿼트|푸쉬업|세트|유산소/.test(response)) {
    issues.push('diet_response_contains_workout_terms');
  }
  if (/운동\s*플랜|운동.*작성|스트레칭/.test(turn) && /아침|점심|저녁|현미밥|식단 플랜으로 작성/.test(response)) {
    issues.push('workout_response_contains_diet_terms');
  }
  return { response, issues };
}

function scrubAllowedConstraintMentions(text) {
  return String(text || '')
    .replace(/(?:글루텐|gluten)\s*(?:프리|없는|없이|제외|free)/gi, '')
    .replace(/무\s*글루텐|gluten[-\s]?free/gi, '')
    .replace(/(?:유제품|우유|dairy|milk)\s*(?:프리|없는|없이|제외|free)/gi, '')
    .replace(/무\s*유제품|dairy[-\s]?free|non[-\s]?dairy/gi, '')
    .replace(/(?:계란|달걀|egg)\s*(?:없는|없이|제외|free)/gi, '')
    .replace(/(?:견과|땅콩|nut|peanut)\s*(?:없는|없이|제외|free)/gi, '')
    .replace(/(?:대두|두부|두유|soy)\s*(?:없는|없이|제외|free)/gi, '')
    .replace(/(?:갑각류|새우|shellfish|shrimp)\s*(?:없는|없이|제외|free)/gi, '');
}

async function run() {
  const selectedProfiles = profiles.slice(0, PROFILE_LIMIT);
  const selectedTurns = turns.slice(0, TURN_LIMIT);
  const results = [];
  for (const profile of selectedProfiles) {
    const login = await createUser(profile);
    const sessionId = `smoke-${profile.label}-${Date.now()}`;
    for (const [index, turn] of selectedTurns.entries()) {
      let payload;
      try {
        payload = await request('/v1/chat', {
          method: 'POST',
          headers: { Authorization: `Bearer ${login.token}` },
          body: JSON.stringify({
            user_message: turn,
            session_id: sessionId,
            client_user_message_id: `${sessionId}-u-${index}`,
            client_message_id: `${sessionId}-a-${index}`,
          }),
        });
      } catch (error) {
        results.push({
          profile: profile.label,
          turn: index + 1,
          prompt: turn,
          intent: null,
          pending_writes_count: null,
          issues: [`request_error:${error.message}`],
          preview: '',
        });
        console.log(`[${profile.label}] ${index + 1}/${selectedTurns.length} ERROR ${turn}`);
        continue;
      }
      const checked = assertResponse(profile, turn, payload);
      results.push({
        profile: profile.label,
        turn: index + 1,
        prompt: turn,
        intent: payload.intent || null,
        pending_writes_count: payload.pending_writes_count || 0,
        issues: checked.issues,
        preview: checked.response.slice(0, 180),
      });
      const issueLabel = checked.issues.length ? `WARN ${checked.issues.join(',')}` : 'OK';
      console.log(`[${profile.label}] ${index + 1}/${selectedTurns.length} ${issueLabel} ${turn}`);
    }
  }
  const failures = results.filter((item) => item.issues.length);
  const summary = {
    ok: failures.length === 0,
    backend: BACKEND,
    profiles: selectedProfiles.length,
    turns_per_profile: selectedTurns.length,
    total_turns: results.length,
    failure_count: failures.length,
    failures,
  };
  console.log(JSON.stringify(summary, null, 2));
  if (!summary.ok) process.exitCode = 1;
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
