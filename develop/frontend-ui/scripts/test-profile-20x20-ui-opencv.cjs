/* eslint-disable @typescript-eslint/no-require-imports */
const { createRequire } = require('module');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const requireFromHere = createRequire(__filename);
const AUTH_TOKEN_STORAGE_KEY = 'healthAppToken';
const AUTH_USER_STORAGE_KEY = 'healthAppUser';
const CHAT_SESSION_STORAGE_KEY = 'healthAppChatSessionId';
const CHAT_MESSAGES_STORAGE_KEY = 'healthAppChatMessages';
const PLAN_UPDATE_STORAGE_KEY = 'capstone.planUpdates.v1';

const WORKOUT_TYPES = ['upper_body', 'lower_body', 'cardio', 'stretching'];
const WORKOUT_LABELS = {
  upper_body: '상체 운동',
  lower_body: '하체 운동',
  cardio: '유산소',
  stretching: '스트레칭',
};
const MEAL_TYPES = ['breakfast', 'lunch', 'dinner'];

function loadPlaywright() {
  try {
    return requireFromHere('playwright');
  } catch {
    const candidates = [
      process.env.PLAYWRIGHT_NODE_MODULES,
      process.env.NODE_PATH,
      process.env.APPDATA
        ? path.join(process.env.APPDATA, 'npm', 'node_modules', '@playwright', 'cli', 'node_modules')
        : null,
    ].filter(Boolean);

    for (const base of candidates) {
      try {
        return require(path.join(base, 'playwright'));
      } catch {}
    }
  }

  throw new Error('Playwright is required. Set NODE_PATH to a node_modules containing playwright.');
}

function parseArg(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1]) return process.argv[index + 1];
  return fallback;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function sanitizeName(value) {
  return String(value || 'item')
    .toLowerCase()
    .replace(/[^a-z0-9가-힣_-]+/gi, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120) || 'item';
}

function kstDate() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function resolvePythonWithCv2() {
  const candidates = [
    process.env.PYTHON,
    process.env.USERPROFILE
      ? path.join(process.env.USERPROFILE, '.pyenv', 'pyenv-win', 'versions', '3.11.5', 'python.exe')
      : null,
    'python',
  ].filter(Boolean);

  for (const candidate of candidates) {
    const result = spawnSync(candidate, ['-c', 'import cv2, sys; print(sys.executable)'], {
      encoding: 'utf-8',
    });
    if (result.status === 0) return candidate;
  }
  return process.env.PYTHON || 'python';
}

function makeProfiles(count) {
  const goals = ['fat_loss', 'muscle_gain', 'maintain', 'mobility', 'blood_sugar_control'];
  const allergies = [[], ['milk'], ['nuts'], ['shellfish'], ['wheat'], ['soy'], ['egg']];
  const conditions = [[], ['knee_pain'], ['hypertension'], ['diabetes'], ['asthma'], ['back_pain']];
  const personas = ['cheer_sis', 'soft_senior', 'strict_trainer', 'science_coach', 'playful_buddy', 'daily_manager'];
  const activityLevels = ['low', 'moderate', 'high'];

  return Array.from({ length: count }, (_, index) => {
    const profileNo = index + 1;
    return {
      user_id: `profile-20x20-${String(profileNo).padStart(2, '0')}`,
      login_id: `p20x20-${String(profileNo).padStart(2, '0')}`,
      nickname: `Profile ${String(profileNo).padStart(2, '0')}`,
      name: `Profile ${String(profileNo).padStart(2, '0')}`,
      email: `profile${profileNo}@example.com`,
      age: 22 + (index % 24),
      gender: index % 2 === 0 ? 'female' : 'male',
      height: 158 + (index % 18),
      weight: 52 + (index % 28),
      bmi: 21 + (index % 8),
      goal: goals[index % goals.length],
      allergies: allergies[index % allergies.length],
      conditions: conditions[index % conditions.length],
      medical_history: conditions[index % conditions.length],
      activityLevel: activityLevels[index % activityLevels.length],
      activity_level: activityLevels[index % activityLevels.length],
      selected_ai_persona: personas[index % personas.length],
      has_health_profile: true,
    };
  });
}

function makeTurn(profileIndex, turnIndex, targetDate) {
  const profileToken = `P${String(profileIndex + 1).padStart(2, '0')}`;
  const turnToken = `T${String(turnIndex + 1).padStart(2, '0')}`;
  const token = `${profileToken}-${turnToken}`;
  const workoutType = WORKOUT_TYPES[turnIndex % WORKOUT_TYPES.length];
  const mealType = MEAL_TYPES[turnIndex % MEAL_TYPES.length];
  return {
    token,
    targetDate,
    message: `${token} 일주일 건강 플랜을 프로필에 맞게 업데이트해줘`,
    answer: `${token} 답변 완료.`,
    workoutType,
    workoutLabel: WORKOUT_LABELS[workoutType],
    workoutName: `${token} ${WORKOUT_LABELS[workoutType]} 루틴`,
    mealType,
    mealName: `${token} ${mealType} 균형 식단`,
    calories: 300 + ((turnIndex + profileIndex) % 9) * 25,
  };
}

function emptyCalendar(today) {
  return { [today]: { exercises: [], meals: [] } };
}

function addTurnToCalendar(calendar, turn, sequence) {
  const day = calendar[turn.targetDate] || { exercises: [], meals: [] };
  day.exercises.push({
    exercise_id: 10000 + sequence,
    exercise_type: turn.workoutType,
    total_calories: 70 + (sequence % 6) * 10,
    status: 0,
    target_date: turn.targetDate,
    exercise_items: [
      {
        item_id: 20000 + sequence,
        exercise_name: turn.workoutName,
        calories: 70 + (sequence % 6) * 10,
        target_sets: turn.workoutType === 'cardio' ? null : 3,
        duration_minutes: turn.workoutType === 'cardio' ? 20 : null,
        is_completed: false,
      },
    ],
  });
  day.meals.push({
    meal_id: 30000 + sequence,
    meal_type: turn.mealType,
    food_name: turn.mealName,
    calories: turn.calories,
    is_completed: false,
    target_date: turn.targetDate,
  });
  calendar[turn.targetDate] = day;
}

async function fulfillJson(route, status, body) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installRoutes(page, state) {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method().toUpperCase();
    state.network.push({ method, pathname });

    if (pathname.endsWith('/api/v1/users/profile')) {
      await fulfillJson(route, 200, state.profile);
      return;
    }

    if (pathname.endsWith('/api/v1/users/calendar')) {
      await fulfillJson(route, 200, state.calendar);
      return;
    }

    if (pathname.endsWith('/api/v1/chat/threads')) {
      await fulfillJson(route, 200, { threads: [] });
      return;
    }

    if (pathname.includes('/api/v1/chat/threads/')) {
      await fulfillJson(route, 200, { messages: [] });
      return;
    }

    if (pathname.endsWith('/api/v1/chat') && method === 'POST') {
      const payload = request.postDataJSON();
      const matched = String(payload.message || '').match(/P(\d{2})-T(\d{2})/);
      if (!matched) {
        await fulfillJson(route, 400, { error: 'missing test token' });
        return;
      }

      const turnIndex = Number(matched[2]) - 1;
      const turn = state.turns[turnIndex];
      state.chatCalls += 1;
      addTurnToCalendar(state.calendar, turn, state.sequenceBase + turnIndex);
      await fulfillJson(route, 200, {
        session_id: payload.session_id || `session-${state.profile.user_id}`,
        client_message_id: payload.client_message_id,
        response: turn.answer,
        intent: 'mixed_plan',
        plan_sync_applied: true,
        pending_writes_count: 0,
      });
      return;
    }

    if (pathname.endsWith('/api/v1/chat/feedback')) {
      await fulfillJson(route, 200, { ok: true });
      return;
    }

    await fulfillJson(route, 200, { ok: true });
  });
}

async function createProfilePages(browser, baseUrl, profile, turns, today, viewport) {
  const state = {
    profile,
    turns,
    today,
    calendar: emptyCalendar(today),
    chatCalls: 0,
    sequenceBase: Number(profile.user_id.match(/(\d+)$/)?.[1] || 1) * 1000,
    network: [],
  };
  const context = await browser.newContext({ viewport });
  context.setDefaultTimeout(10000);
  await context.addInitScript(
    ({ user, tokenKey, userKey, chatSessionKey, chatMessagesKey, planKey }) => {
      window.__HEALTH_MATE_TEST_FAST_STREAM__ = true;
      localStorage.setItem(tokenKey, `token-${user.user_id}`);
      localStorage.setItem(userKey, JSON.stringify(user));
      localStorage.setItem(planKey, JSON.stringify([]));
      sessionStorage.removeItem(chatSessionKey);
      sessionStorage.removeItem(chatMessagesKey);
    },
    {
      user: profile,
      tokenKey: AUTH_TOKEN_STORAGE_KEY,
      userKey: AUTH_USER_STORAGE_KEY,
      chatSessionKey: CHAT_SESSION_STORAGE_KEY,
      chatMessagesKey: CHAT_MESSAGES_STORAGE_KEY,
      planKey: PLAN_UPDATE_STORAGE_KEY,
    }
  );
  const chatPage = await context.newPage();
  const healthPage = await context.newPage();
  await installRoutes(chatPage, state);
  await installRoutes(healthPage, state);
  await chatPage.goto(`${baseUrl.replace(/\/$/, '')}/chat`);
  await chatPage.waitForLoadState('networkidle', { timeout: 12000 }).catch(() => {});
  return { context, chatPage, healthPage, state };
}

async function screenshot(page, screenshotDir, name) {
  const file = path.join(screenshotDir, `${sanitizeName(name)}.png`);
  await page.screenshot({ path: file, fullPage: false });
  return file;
}

async function waitForBodyText(page, expected, timeout = 12000) {
  await page.waitForFunction(
    (text) => document.body.innerText.includes(text),
    expected,
    { timeout }
  );
}

async function runTurn(chatPage, healthPage, baseUrl, profile, profileIndex, turn, turnIndex, screenshotDir) {
  const input = chatPage.locator('input[type="text"]').last();
  await input.fill(turn.message);
  await chatPage.locator('button[type="submit"]').click();
  await waitForBodyText(chatPage, turn.answer, 15000);
  const chatText = await chatPage.evaluate(() => document.body.innerText);
  const chatAfter = await screenshot(chatPage, screenshotDir, `${turn.token}-chat-answer`);

  await healthPage.goto(`${baseUrl.replace(/\/$/, '')}/recommend`);
  await healthPage.waitForLoadState('networkidle', { timeout: 12000 }).catch(() => {});
  await waitForBodyText(healthPage, turn.mealName, 8000);

  const todayWorkoutButton = healthPage.locator('button:visible').filter({
    hasText: turn.workoutLabel,
  }).first();
  await todayWorkoutButton.scrollIntoViewIfNeeded();
  await todayWorkoutButton.click();
  await waitForBodyText(healthPage, turn.workoutName, 8000);
  await healthPage.locator('button[aria-label="세부 항목 닫기"]').click();

  await healthPage.locator(`[aria-label="캘린더 ${turn.targetDate} 상세 보기"]`).click();
  await waitForBodyText(healthPage, turn.mealName, 8000);

  const calendarModalText = await healthPage.evaluate(() => document.body.innerText);
  const calendarHasMeal = calendarModalText.includes(turn.mealName);
  const calendarHasDate = calendarModalText.includes(String(Number(turn.targetDate.slice(-2))));
  if (!calendarHasMeal || !calendarHasDate) {
    throw new Error(`${turn.token} calendar detail did not include expected meal/date.`);
  }

  const groupButton = healthPage.locator('div.fixed.inset-0 button:visible').filter({
    hasText: turn.workoutLabel,
  }).first();
  await groupButton.scrollIntoViewIfNeeded();
  await groupButton.click();
  await waitForBodyText(healthPage, turn.workoutName, 8000);
  const healthAfter = await screenshot(healthPage, screenshotDir, `${turn.token}-health-detail`);
  const detailText = await healthPage.evaluate(() => document.body.innerText);

  const checks = {
    chat_has_answer: chatText.includes(turn.answer),
    today_diet_section_has_meal: calendarModalText.includes(turn.mealName),
    calendar_has_meal: detailText.includes(turn.mealName),
    detail_has_workout: detailText.includes(turn.workoutName),
    detail_has_calories: detailText.includes(`${turn.calories} kcal`),
    no_reason_leak: !/근거|reason|evidence/i.test(detailText),
  };
  const ok = Object.values(checks).every(Boolean);

  return {
    name: `profile-20x20-${turn.token}`,
    route: '/chat -> /recommend',
    viewport: 'mobile',
    ok,
    profile_id: profile.user_id,
    profile_index: profileIndex + 1,
    turn_index: turnIndex + 1,
    token: turn.token,
    user_message: turn.message,
    expected_answer: turn.answer,
    expected_workout: turn.workoutName,
    expected_meal: turn.mealName,
    checks,
    before: chatAfter,
    after: healthAfter,
    expectVisualChange: true,
  };
}

async function runProfile({
  browser,
  baseUrl,
  profile,
  profileIndex,
  turnsPerProfile,
  today,
  screenshotDir,
}) {
  const turns = Array.from({ length: turnsPerProfile }, (_, turnIndex) =>
    makeTurn(profileIndex, turnIndex, today)
  );
  const profileReport = {
    scenarios: [],
    issues: [],
  };
  let context = null;

  try {
    const pages = await createProfilePages(
      browser,
      baseUrl,
      profile,
      turns,
      today,
      { width: 390, height: 844 }
    );
    context = pages.context;

    for (let turnIndex = 0; turnIndex < turns.length; turnIndex += 1) {
      const turn = turns[turnIndex];
      try {
        const result = await runTurn(
          pages.chatPage,
          pages.healthPage,
          baseUrl,
          profile,
          profileIndex,
          turn,
          turnIndex,
          screenshotDir
        );
        profileReport.scenarios.push(result);
        if (!result.ok) {
          profileReport.issues.push({
            level: 'error',
            type: 'profile-turn-ui-reflection-failed',
            profile_id: profile.user_id,
            token: turn.token,
            checks: result.checks,
          });
        }
      } catch (error) {
        profileReport.issues.push({
          level: 'error',
          type: 'profile-turn-exception',
          profile_id: profile.user_id,
          token: turn.token,
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }

    if (pages.state.chatCalls !== turnsPerProfile) {
      profileReport.issues.push({
        level: 'error',
        type: 'chat-call-count-mismatch',
        profile_id: profile.user_id,
        expected: turnsPerProfile,
        actual: pages.state.chatCalls,
      });
    }
  } catch (error) {
    profileReport.issues.push({
      level: 'error',
      type: 'profile-setup-exception',
      profile_id: profile.user_id,
      message: error instanceof Error ? error.message : String(error),
    });
  } finally {
    if (context) await context.close();
  }

  return profileReport;
}

async function runWithConcurrency(items, limit, worker) {
  const results = new Array(items.length);
  let nextIndex = 0;
  const workerCount = Math.max(1, Math.min(limit, items.length));

  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (nextIndex < items.length) {
        const currentIndex = nextIndex;
        nextIndex += 1;
        results[currentIndex] = await worker(items[currentIndex], currentIndex);
      }
    })
  );

  return results;
}

async function main() {
  const { chromium } = loadPlaywright();
  const baseUrl = parseArg('--url', process.env.PROFILE_20X20_BASE_URL || 'http://localhost:3100');
  const profileCount = Number(parseArg('--profiles', '20'));
  const turnsPerProfile = Number(parseArg('--turns', '20'));
  const concurrency = Math.max(
    1,
    Math.min(
      profileCount,
      Number(parseArg('--concurrency', process.env.PROFILE_20X20_CONCURRENCY || '4')) || 1
    )
  );
  const outputRoot = parseArg(
    '--output',
    path.join(process.cwd(), 'docs', 'ui-audit', 'profile-20x20-latest')
  );
  const screenshotDir = path.join(outputRoot, 'screenshots');
  ensureDir(screenshotDir);

  const today = kstDate();
  const profiles = makeProfiles(profileCount);
  const browser = await chromium.launch({ headless: true });
  const report = {
    ok: true,
    baseUrl,
    today,
    profileCount,
    turnsPerProfile,
    concurrency,
    totalTurns: profileCount * turnsPerProfile,
    outputRoot,
    routes: [],
    buttonActions: [],
    fieldActions: [],
    scenarios: [],
    issues: [],
    opencv: null,
  };

  try {
    const profileReports = await runWithConcurrency(profiles, concurrency, (profile, profileIndex) =>
      runProfile({
        browser,
        baseUrl,
        profile,
        profileIndex,
        turnsPerProfile,
        today,
        screenshotDir,
      })
    );

    for (const profileReport of profileReports) {
      report.scenarios.push(...profileReport.scenarios);
      report.issues.push(...profileReport.issues);
    }
    report.scenarios.sort(
      (left, right) =>
        left.profile_index - right.profile_index || left.turn_index - right.turn_index
    );
  } finally {
    await browser.close();
  }

  const reportPath = path.join(outputRoot, 'profile-20x20-ui-report.json');
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));

  const opencvPython = resolvePythonWithCv2();
  const opencv = spawnSync(opencvPython, [path.join(__dirname, 'audit-ui-opencv.py'), reportPath], {
    encoding: 'utf-8',
  });
  if (opencv.stdout) process.stdout.write(opencv.stdout);
  if (opencv.stderr) process.stderr.write(opencv.stderr);
  if (opencv.status !== 0) {
    report.issues.push({ level: 'error', type: 'opencv-audit-failed', status: opencv.status });
  } else {
    const opencvPath = path.join(outputRoot, 'opencv-ui-audit.json');
    if (fs.existsSync(opencvPath)) {
      report.opencv = JSON.parse(fs.readFileSync(opencvPath, 'utf-8'));
      for (const issue of report.opencv.issues || []) {
        report.issues.push(issue);
      }
    }
  }

  report.ok = report.issues.filter((issue) => issue.level === 'error').length === 0;
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));

  const summary = {
    ok: report.ok,
    profiles: profileCount,
    turnsPerProfile,
    concurrency,
    totalTurns: report.scenarios.length,
    issues: report.issues.length,
    errors: report.issues.filter((issue) => issue.level === 'error').length,
    opencvImages: report.opencv?.image_count ?? null,
    opencvDiffs: report.opencv?.diff_count ?? null,
    reportPath,
  };
  console.log(JSON.stringify(summary, null, 2));
  if (!report.ok) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
