/* eslint-disable @typescript-eslint/no-require-imports */
const { createRequire } = require('module');

const requireFromHere = createRequire(__filename);

function loadPlaywright() {
  try {
    return requireFromHere('playwright');
  } catch {
    throw new Error(
      'Playwright is required for this smoke test. Install it in this package or run with NODE_PATH pointing to a node_modules that contains playwright.'
    );
  }
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

function parseArg(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1]) {
    return process.argv[index + 1];
  }
  return fallback;
}

async function main() {
  const { chromium } = loadPlaywright();
  const baseUrl = parseArg('--url', process.env.SMOKE_BASE_URL || 'http://localhost:3100');
  const today = kstDate();
  const user = {
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
  const calendar = { [today]: { exercises: [], meals: [] } };
  let nextItemId = 101;
  let addPostCount = 0;

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });

  await page.addInitScript(
    ({ seededUser }) => {
      localStorage.setItem('healthAppToken', 'home-ux-smoke-token');
      localStorage.setItem('healthAppUser', JSON.stringify(seededUser));
      if (!localStorage.getItem('__homeUxSmokeSeeded')) {
        localStorage.removeItem('capstone.planUpdates.v1');
        localStorage.removeItem('capstone.homeRecommendationHighlights.v1');
        localStorage.setItem('__homeUxSmokeSeeded', '1');
      }
    },
    { seededUser: user }
  );

  await page.route('**/api/v1/users/profile', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(user) })
  );

  await page.route('**/api/v1/users/calendar**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(calendar) })
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
    addPostCount += 1;
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
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ message: 'ok', parent_plan: plan, item: plan.exercise_items[0] }),
    });
  });

  await page.route('**/api/v1/users/meals/recommend-replace', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'ok' }) })
  );

  await page.goto(baseUrl);
  await page.waitForLoadState('networkidle');

  const recommendationButtons = page.locator('button[aria-label]');
  const recommendationButtonCount = await recommendationButtons.count();
  if (recommendationButtonCount < 7) {
    throw new Error(`Expected recommendation buttons to render, got ${recommendationButtonCount}`);
  }

  const firstWorkoutButton = recommendationButtons.nth(0);
  await firstWorkoutButton.click();
  const modalButtons = page.locator('div.fixed.inset-0 button');
  await modalButtons.last().waitFor({ state: 'visible', timeout: 5000 });
  await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes('/api/v1/users/exercises/recommend-add') && response.status() === 200
    ),
    modalButtons.last().click(),
  ]);
  await page.waitForLoadState('networkidle');

  if (addPostCount !== 1) {
    throw new Error(`Expected exactly one recommendation add POST, got ${addPostCount}`);
  }
  if (!(await firstWorkoutButton.isDisabled())) {
    throw new Error('Expected applied recommendation button to be disabled');
  }

  const storedHighlights = await page.evaluate(() => localStorage.getItem('capstone.planUpdates.v1'));
  if (!storedHighlights || !storedHighlights.includes('workout')) {
    throw new Error(`Expected workout plan highlight to be stored, got ${storedHighlights}`);
  }

  await page.goto(`${baseUrl.replace(/\/$/, '')}/recommend`);
  await page.waitForLoadState('networkidle');
  const highlighted = page.locator('[data-plan-update-highlight="true"]');
  const beforeHover = await highlighted.count();
  if (beforeHover < 1) {
    throw new Error('Expected at least one highlighted plan item on recommendation page');
  }
  await highlighted.nth(0).hover();
  await page.waitForTimeout(300);
  const afterHover = await highlighted.count();
  if (afterHover !== 0) {
    throw new Error(`Expected plan highlight to clear after hover, got ${afterHover}`);
  }

  console.log(
    JSON.stringify({
      ok: true,
      today,
      recommendationButtonCount,
      addPostCount,
      beforeHover,
      afterHover,
    })
  );

  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
