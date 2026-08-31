/* eslint-disable @typescript-eslint/no-require-imports */
const { chromium } = require('playwright');
const PLAN_UPDATE_STORAGE_KEY = 'capstone.planUpdates.v1';
const HOME_HIGHLIGHT_STORAGE_KEY = 'capstone.homeRecommendationHighlights.v1';

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

function parseArg(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1]) {
    return process.argv[index + 1];
  }
  return fallback;
}

function seededUser() {
  return {
    user_id: 'home-ux-smoke-user',
    login_id: 'home-ux-smoke',
    nickname: 'Demo',
    name: 'Demo',
    age: 29,
    gender: 'female',
    height: 165,
    weight: 64,
    goal: 'fat_loss',
    activityLevel: 'low',
    allergies: ['milk'],
    conditions: [],
    selected_ai_persona: 'cheer_sis',
  };
}

function emptyCalendar(today) {
  return { [today]: { exercises: [], meals: [] } };
}

async function createSeededPage(browser, viewport) {
  const user = seededUser();
  const context = await browser.newContext({ viewport });
  await context.addInitScript(
    ({ seededUser: browserUser, planKey, homeKey }) => {
      localStorage.setItem('healthAppToken', 'home-ux-smoke-token');
      localStorage.setItem('healthAppUser', JSON.stringify(browserUser));
      if (!localStorage.getItem('__homeUxSmokeSeeded')) {
        localStorage.removeItem(planKey);
        localStorage.removeItem(homeKey);
        localStorage.setItem('__homeUxSmokeSeeded', '1');
      }
    },
    { seededUser: user, planKey: PLAN_UPDATE_STORAGE_KEY, homeKey: HOME_HIGHLIGHT_STORAGE_KEY }
  );
  const page = await context.newPage();
  return { context, page, user };
}

async function installRoutes(page, { today, user, calendar, mode = 'success' }) {
  let nextItemId = 101;
  let nextMealId = 201;
  const counters = {
    addPostCount: 0,
    replacePostCount: 0,
  };

  await page.route('**/api/v1/users/profile', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(user) })
  );

  await page.route('**/api/v1/users/calendar**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(calendar) })
  );

  await page.route((url) => url.pathname === '/api/v1/home/recommendations', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: today,
        scope: 'all',
        workout: {
          upper_body: {
            exercise_name: 'Band row',
            summary: 'Upper body routine.',
            sets: 3,
            duration_minutes: null,
            calories: 90,
          },
          lower_body: {
            exercise_name: 'Glute bridge',
            summary: 'Lower body routine.',
            sets: 3,
            duration_minutes: null,
            calories: 80,
          },
          cardio: {
            exercise_name: 'Fast walk',
            summary: 'Cardio routine.',
            sets: null,
            duration_minutes: 20,
            calories: 130,
          },
          stretching: {
            exercise_name: 'Full body stretch',
            summary: 'Recovery routine.',
            sets: 2,
            duration_minutes: null,
            calories: 35,
          },
        },
        diet: {
          breakfast: { food_name: 'Oat berry bowl', summary: 'Breakfast.', calories: 320 },
          lunch: { food_name: 'Brown rice bowl', summary: 'Lunch.', calories: 480 },
          dinner: { food_name: 'Mushroom rice bowl', summary: 'Dinner.', calories: 430 },
        },
      }),
    })
  );

  await page.route('**/api/v1/home/recommendations/workout', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: today,
        scope: 'workout',
        workout: {
          upper_body: {
            exercise_name: 'Band row',
            summary: 'Upper body routine.',
            sets: 3,
            duration_minutes: null,
            calories: 90,
          },
          lower_body: {
            exercise_name: 'Glute bridge',
            summary: 'Lower body routine.',
            sets: 3,
            duration_minutes: null,
            calories: 80,
          },
          cardio: {
            exercise_name: 'Fast walk',
            summary: 'Cardio routine.',
            sets: null,
            duration_minutes: 20,
            calories: 130,
          },
          stretching: {
            exercise_name: 'Full body stretch',
            summary: 'Recovery routine.',
            sets: 2,
            duration_minutes: null,
            calories: 35,
          },
        },
        diet: { breakfast: null, lunch: null, dinner: null },
      }),
    })
  );

  await page.route('**/api/v1/home/recommendations/diet', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        date: today,
        scope: 'diet',
        workout: { upper_body: null, lower_body: null, cardio: null, stretching: null },
        diet: {
          breakfast: { food_name: 'Oat berry bowl', summary: 'Breakfast.', calories: 320 },
          lunch: { food_name: 'Brown rice bowl', summary: 'Lunch.', calories: 480 },
          dinner: { food_name: 'Mushroom rice bowl', summary: 'Dinner.', calories: 430 },
        },
      }),
    })
  );

  await page.route('**/api/v1/users/exercises/recommend-add', async (route) => {
    counters.addPostCount += 1;
    if (mode === 'workout-fail') {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'forced smoke failure' }),
      });
      return;
    }

    const payload = route.request().postDataJSON();
    const plan = {
      exercise_id: 10,
      exercise_type: payload.exercise_type,
      total_calories: payload.calories,
      status: 0,
      target_date: payload.target_date,
      exercise_items: [
        {
          item_id: nextItemId,
          exercise_name: payload.exercise_name,
          calories: payload.calories,
          target_sets: payload.target_sets,
          duration_minutes: payload.duration_minutes,
          is_completed: false,
        },
      ],
    };
    nextItemId += 1;
    calendar[payload.target_date] = {
      ...(calendar[payload.target_date] || { exercises: [], meals: [] }),
      exercises: [plan],
      meals: calendar[payload.target_date]?.meals || [],
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ message: 'ok', parent_plan: plan, item: plan.exercise_items[0] }),
    });
  });

  await page.route('**/api/v1/users/meals/recommend-replace', async (route) => {
    counters.replacePostCount += 1;
    if (mode === 'meal-already-exists') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'already exists', already_exists: true }),
      });
      return;
    }

    const payload = route.request().postDataJSON();
    const meal = {
      meal_id: nextMealId,
      meal_type: payload.meal_type,
      food_name: payload.food_name,
      calories: payload.calories,
      is_completed: false,
      target_date: payload.target_date,
    };
    nextMealId += 1;
    calendar[payload.target_date] = {
      ...(calendar[payload.target_date] || { exercises: [], meals: [] }),
      exercises: calendar[payload.target_date]?.exercises || [],
      meals: [meal],
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ message: 'ok', meal }),
    });
  });

  return counters;
}

async function clickRecommendationAndConfirm(page, button) {
  await button.waitFor({ state: 'visible', timeout: 10000 });
  await page.waitForFunction(
    (element) => element instanceof HTMLButtonElement && !element.disabled,
    await button.elementHandle(),
    { timeout: 10000 }
  );
  await button.click();
  const modalButtons = page.locator('div.fixed.inset-0 button:visible');
  await modalButtons.last().waitFor({ state: 'visible', timeout: 5000 });
  await modalButtons.last().click();
}

function recommendationButton(page, label) {
  return page.locator(`button[aria-label="${label}"]`).first();
}

async function storedPlanHighlights(page) {
  return page.evaluate((key) => localStorage.getItem(key), PLAN_UPDATE_STORAGE_KEY);
}

async function waitForStoredHighlights(page, expectedTokens, description) {
  try {
    await page.waitForFunction(
      ({ key, tokens }) => {
        const value = localStorage.getItem(key) || '';
        return tokens.every((token) => value.includes(token));
      },
      { key: PLAN_UPDATE_STORAGE_KEY, tokens: expectedTokens },
      { timeout: 5000 }
    );
  } catch {
    const current = await storedPlanHighlights(page);
    throw new Error(`Timed out waiting for ${description}, got ${current}`);
  }
  return storedPlanHighlights(page);
}

async function runSuccessScenario(browser, baseUrl, today) {
  const { context, page, user } = await createSeededPage(browser, { width: 390, height: 844 });
  const calendar = emptyCalendar(today);
  const counters = await installRoutes(page, { today, user, calendar });

  try {
    await page.goto(baseUrl);
    await page.waitForLoadState('networkidle');

    const recommendationButtons = page.locator('button[aria-label$="추천 추가"], button[aria-label$="추천 반영"]');
    const recommendationButtonCount = await recommendationButtons.count();
    if (recommendationButtonCount < 7) {
      throw new Error(`Expected recommendation buttons to render, got ${recommendationButtonCount}`);
    }

    const firstWorkoutButton = recommendationButton(page, '상체 운동 추천 추가');
    await Promise.all([
      page.waitForResponse((response) =>
        response.url().includes('/api/v1/users/exercises/recommend-add') && response.status() === 200
      ),
      clickRecommendationAndConfirm(page, firstWorkoutButton),
    ]);
    await page.waitForLoadState('networkidle');
    if (counters.addPostCount !== 1) {
      throw new Error(`Expected exactly one recommendation add POST, got ${counters.addPostCount}`);
    }
    if (!(await firstWorkoutButton.isDisabled())) {
      throw new Error('Expected applied workout recommendation button to be disabled');
    }

    const firstDietButton = recommendationButton(page, '아침 식단 추천 반영');
    await Promise.all([
      page.waitForResponse((response) =>
        response.url().includes('/api/v1/users/meals/recommend-replace') && response.status() === 200
      ),
      clickRecommendationAndConfirm(page, firstDietButton),
    ]);
    await page.waitForLoadState('networkidle');
    if (counters.replacePostCount !== 1) {
      throw new Error(`Expected exactly one meal replace PUT, got ${counters.replacePostCount}`);
    }

    await waitForStoredHighlights(
      page,
      ['workout', 'diet'],
      'workout and diet plan highlights'
    );

    await page.goto(`${baseUrl.replace(/\/$/, '')}/recommend`);
    await page.waitForLoadState('networkidle');
    const highlighted = page.locator('[data-plan-update-highlight="true"]');
    const beforeHover = await highlighted.count();
    if (beforeHover < 1) {
      throw new Error('Expected highlighted plan items on recommendation page');
    }

    await highlighted.nth(0).hover();
    await page.waitForTimeout(300);
    const afterHover = await highlighted.count();
    if (afterHover > 0) {
      await highlighted.nth(0).click();
      await page.waitForTimeout(300);
    }
    const afterTap = await highlighted.count();
    if (afterTap !== 0) {
      throw new Error(`Expected plan highlights to clear after hover/tap, got ${afterTap}`);
    }

    return {
      recommendationButtonCount,
      addPostCount: counters.addPostCount,
      replacePostCount: counters.replacePostCount,
      beforeHover,
      afterHover,
      afterTap,
    };
  } finally {
    await context.close();
  }
}

async function runAlreadyExistsScenario(browser, baseUrl, today) {
  const { context, page, user } = await createSeededPage(browser, { width: 390, height: 844 });
  const calendar = emptyCalendar(today);
  const counters = await installRoutes(page, { today, user, calendar, mode: 'meal-already-exists' });

  try {
    await page.goto(baseUrl);
    await page.waitForLoadState('networkidle');
    const firstDietButton = recommendationButton(page, '아침 식단 추천 반영');
    await Promise.all([
      page.waitForResponse((response) =>
        response.url().includes('/api/v1/users/meals/recommend-replace') && response.status() === 200
      ),
      clickRecommendationAndConfirm(page, firstDietButton),
    ]);
    await page.waitForLoadState('networkidle');

    const storedHighlights = await storedPlanHighlights(page);
    if (storedHighlights && storedHighlights.includes('diet')) {
      throw new Error(`already_exists response must not create diet highlight, got ${storedHighlights}`);
    }
    return { replacePostCount: counters.replacePostCount, storedHighlights };
  } finally {
    await context.close();
  }
}

async function runFailureScenario(browser, baseUrl, today) {
  const { context, page, user } = await createSeededPage(browser, { width: 430, height: 932 });
  const calendar = emptyCalendar(today);
  const counters = await installRoutes(page, { today, user, calendar, mode: 'workout-fail' });

  try {
    await page.goto(baseUrl);
    await page.waitForLoadState('networkidle');
    const firstWorkoutButton = recommendationButton(page, '상체 운동 추천 추가');
    await Promise.all([
      page.waitForResponse((response) =>
        response.url().includes('/api/v1/users/exercises/recommend-add') && response.status() === 500
      ),
      clickRecommendationAndConfirm(page, firstWorkoutButton),
    ]);
    await page.waitForLoadState('networkidle');

    if (await firstWorkoutButton.isDisabled()) {
      throw new Error('Failed recommendation add must not disable the workout button');
    }
    const storedHighlights = await storedPlanHighlights(page);
    if (storedHighlights && storedHighlights.includes('workout')) {
      throw new Error(`failed add must not create workout highlight, got ${storedHighlights}`);
    }
    return { addPostCount: counters.addPostCount, storedHighlights };
  } finally {
    await context.close();
  }
}

async function main() {
  const baseUrl = parseArg('--url', process.env.SMOKE_BASE_URL || 'http://localhost:3100');
  const today = kstDate();
  const browser = await chromium.launch({ headless: true });

  try {
    const success = await runSuccessScenario(browser, baseUrl, today);
    const alreadyExists = await runAlreadyExistsScenario(browser, baseUrl, today);
    const failure = await runFailureScenario(browser, baseUrl, today);

    console.log(
      JSON.stringify({
        ok: true,
        today,
        success,
        alreadyExists,
        failure,
      })
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
