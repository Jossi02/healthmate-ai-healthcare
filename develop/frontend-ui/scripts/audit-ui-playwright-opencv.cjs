/* eslint-disable @typescript-eslint/no-require-imports */
const { createRequire } = require('module');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const requireFromHere = createRequire(__filename);
const AUTH_TOKEN_STORAGE_KEY = 'healthAppToken';
const AUTH_USER_STORAGE_KEY = 'healthAppUser';
const PLAN_UPDATE_STORAGE_KEY = 'capstone.planUpdates.v1';
const CHAT_SESSION_STORAGE_KEY = 'healthAppChatSessionId';
const CHAT_MESSAGES_STORAGE_KEY = 'healthAppChatMessages';

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

  throw new Error(
    'Playwright is required. Install it locally or set NODE_PATH/PLAYWRIGHT_NODE_MODULES to a node_modules containing playwright.'
  );
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function sanitizeName(value) {
  return String(value || 'item')
    .toLowerCase()
    .replace(/[^a-z0-9가-힣_-]+/gi, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80) || 'item';
}

function viewportLabel(viewport) {
  return viewport.name || `${viewport.width}x${viewport.height}`;
}

function playwrightViewport(viewport) {
  return {
    width: viewport.width,
    height: viewport.height,
  };
}

function kstDate(offsetDays = 0) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
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
    user_id: 'ui-audit-user',
    login_id: 'uiaudit',
    nickname: 'Demo User',
    name: 'Demo User',
    email: 'demo@example.com',
    age: 29,
    gender: 'female',
    height: 165,
    weight: 64,
    bmi: 23.5,
    goal: 'fat_loss',
    activityLevel: 'low',
    activity_level: 'low',
    allergies: ['milk'],
    conditions: ['knee_pain'],
    selected_ai_persona: 'cheer_sis',
    has_health_profile: true,
  };
}

function makeCalendar(today) {
  return {
    [today]: {
      exercises: [
        {
          exercise_id: 1,
          exercise_type: 'upper_body',
          total_calories: 90,
          status: 0,
          target_date: today,
          exercise_items: [
            {
              item_id: 11,
              exercise_name: 'Band row',
              calories: 90,
              target_sets: 3,
              duration_minutes: null,
              is_completed: false,
            },
            {
              item_id: 12,
              exercise_name: 'Wall push-up',
              calories: 70,
              target_sets: 3,
              duration_minutes: null,
              is_completed: false,
            },
          ],
        },
        {
          exercise_id: 2,
          exercise_type: 'stretching',
          total_calories: 35,
          status: 0,
          target_date: today,
          exercise_items: [
            {
              item_id: 13,
              exercise_name: 'Full body stretch',
              calories: 35,
              target_sets: 2,
              duration_minutes: null,
              is_completed: false,
            },
          ],
        },
      ],
      meals: [
        {
          meal_id: 21,
          meal_type: 'breakfast',
          food_name: 'Oat berry bowl',
          calories: 320,
          is_completed: false,
          target_date: today,
        },
        {
          meal_id: 22,
          meal_type: 'lunch',
          food_name: 'Brown rice chicken bowl',
          calories: 480,
          is_completed: false,
          target_date: today,
        },
        {
          meal_id: 23,
          meal_type: 'dinner',
          food_name: 'Mushroom tofu rice',
          calories: 430,
          is_completed: false,
          target_date: today,
        },
      ],
    },
    [kstDate(1)]: {
      exercises: [],
      meals: [],
    },
  };
}

function recommendationPayload(today, scope = 'all') {
  return {
    date: today,
    scope,
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
  };
}

function createAuditState(today) {
  return {
    today,
    user: seededUser(),
    calendar: makeCalendar(today),
    counters: {
      login: 0,
      signup: 0,
      profileSave: 0,
      personaPatch: 0,
      chat: 0,
      feedback: 0,
      workoutAdd: 0,
      dietReplace: 0,
      planCheck: 0,
      planDelete: 0,
    },
  };
}

async function fulfillJson(route, status, body) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installRoutes(page, state, networkLog) {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method().toUpperCase();
    const pathname = url.pathname;
    networkLog.push({ method, pathname });

    if (pathname.endsWith('/api/v1/auth/login') && method === 'POST') {
      state.counters.login += 1;
      await fulfillJson(route, 200, {
        token: 'ui-audit-token',
        user: {
          user_id: state.user.user_id,
          login_id: state.user.login_id,
          nickname: state.user.nickname,
          email: state.user.email,
          has_health_profile: true,
        },
      });
      return;
    }

    if (pathname.endsWith('/api/v1/auth/signup') && method === 'POST') {
      state.counters.signup += 1;
      await fulfillJson(route, 201, {
        user: {
          user_id: 'signup-user',
          login_id: 'signup-user',
        },
      });
      return;
    }

    if (pathname.endsWith('/api/v1/users/profile')) {
      if (method === 'POST' || method === 'PUT' || method === 'PATCH') {
        state.counters.profileSave += 1;
        await fulfillJson(route, 200, {
          data: {
            ...state.user,
            ...(request.postDataJSON?.() || {}),
            bmi: 23.4,
          },
        });
        return;
      }
      await fulfillJson(route, 200, state.user);
      return;
    }

    if (pathname.includes('/api/v1/users/') && pathname.endsWith('/settings/persona')) {
      state.counters.personaPatch += 1;
      await fulfillJson(route, 200, { ok: true });
      return;
    }

    if (pathname.endsWith('/api/v1/users/calendar')) {
      await fulfillJson(route, 200, state.calendar);
      return;
    }

    if (pathname.endsWith('/api/v1/home/recommendations')) {
      await fulfillJson(route, 200, recommendationPayload(state.today, 'all'));
      return;
    }

    if (pathname.endsWith('/api/v1/home/recommendations/workout')) {
      await fulfillJson(route, 200, {
        ...recommendationPayload(state.today, 'workout'),
        diet: { breakfast: null, lunch: null, dinner: null },
      });
      return;
    }

    if (pathname.endsWith('/api/v1/home/recommendations/diet')) {
      await fulfillJson(route, 200, {
        ...recommendationPayload(state.today, 'diet'),
        workout: { upper_body: null, lower_body: null, cardio: null, stretching: null },
      });
      return;
    }

    if (pathname.endsWith('/api/v1/users/exercises/recommend-add')) {
      state.counters.workoutAdd += 1;
      const payload = request.postDataJSON();
      const itemId = 500 + state.counters.workoutAdd;
      state.calendar[payload.target_date] = state.calendar[payload.target_date] || {
        exercises: [],
        meals: [],
      };
      state.calendar[payload.target_date].exercises.push({
        exercise_id: 500 + state.counters.workoutAdd,
        exercise_type: payload.exercise_type,
        total_calories: payload.calories,
        status: 0,
        target_date: payload.target_date,
        exercise_items: [
          {
            item_id: itemId,
            exercise_name: payload.exercise_name,
            calories: payload.calories,
            target_sets: payload.target_sets,
            duration_minutes: payload.duration_minutes,
            is_completed: false,
          },
        ],
      });
      await fulfillJson(route, 200, { message: 'ok', item_id: itemId });
      return;
    }

    if (pathname.endsWith('/api/v1/users/meals/recommend-replace')) {
      state.counters.dietReplace += 1;
      const payload = request.postDataJSON();
      state.calendar[payload.target_date] = state.calendar[payload.target_date] || {
        exercises: [],
        meals: [],
      };
      const meal = {
        meal_id: 600 + state.counters.dietReplace,
        meal_type: payload.meal_type,
        food_name: payload.food_name,
        calories: payload.calories,
        is_completed: false,
        target_date: payload.target_date,
      };
      state.calendar[payload.target_date].meals = [
        ...state.calendar[payload.target_date].meals.filter(
          (item) => item.meal_type !== payload.meal_type
        ),
        meal,
      ];
      await fulfillJson(route, 200, { message: 'ok', meal });
      return;
    }

    if (pathname.endsWith('/api/v1/users/plans/check')) {
      state.counters.planCheck += 1;
      await fulfillJson(route, 200, { ok: true });
      return;
    }

    if (pathname.includes('/api/v1/users/plans/') && method === 'DELETE') {
      state.counters.planDelete += 1;
      await fulfillJson(route, 200, { ok: true });
      return;
    }

    if (pathname.endsWith('/api/v1/chat/threads')) {
      await fulfillJson(route, 200, {
        threads: [
          {
            session_id: 'thread-audit-1',
            title: 'Audit thread',
            updated_at: new Date().toISOString(),
          },
        ],
      });
      return;
    }

    if (pathname.includes('/api/v1/chat/threads/')) {
      await fulfillJson(route, 200, {
        messages: [
          {
            role: 'user',
            content: 'Make a light weekly workout plan.',
            created_at: new Date().toISOString(),
          },
          {
            role: 'assistant',
            content: 'Here is a concise weekly workout plan.',
            created_at: new Date().toISOString(),
            intent: 'workout_plan',
          },
        ],
      });
      return;
    }

    if (pathname.endsWith('/api/v1/chat') && method === 'POST') {
      state.counters.chat += 1;
      const payload = request.postDataJSON();
      await fulfillJson(route, 200, {
        session_id: payload.session_id || 'thread-audit-new',
        client_message_id: payload.client_message_id,
        response:
          'Weekly workout plan: Mon band row, Tue walk, Wed stretch, Thu glute bridge, Fri walk, Sat mobility, Sun rest.',
        intent: 'workout_plan',
        plan_sync_applied: true,
        pending_writes_count: 0,
      });
      return;
    }

    if (pathname.endsWith('/api/v1/chat/feedback')) {
      state.counters.feedback += 1;
      await fulfillJson(route, 200, { ok: true });
      return;
    }

    await fulfillJson(route, 200, { ok: true });
  });
}

async function createPage(browser, routeConfig, state, viewport) {
  const networkLog = [];
  const consoleMessages = [];
  const pageErrors = [];
  const context = await browser.newContext({ viewport: playwrightViewport(viewport) });
  if (routeConfig.auth !== false) {
    await context.addInitScript(
      ({ user, tokenKey, userKey, planKey, chatSessionKey, chatMessagesKey }) => {
        localStorage.setItem(tokenKey, 'ui-audit-token');
        localStorage.setItem(userKey, JSON.stringify(user));
        localStorage.setItem(planKey, JSON.stringify([]));
        sessionStorage.removeItem(chatSessionKey);
        sessionStorage.removeItem(chatMessagesKey);
      },
      {
        user: state.user,
        tokenKey: AUTH_TOKEN_STORAGE_KEY,
        userKey: AUTH_USER_STORAGE_KEY,
        planKey: PLAN_UPDATE_STORAGE_KEY,
        chatSessionKey: CHAT_SESSION_STORAGE_KEY,
        chatMessagesKey: CHAT_MESSAGES_STORAGE_KEY,
      }
    );
  }

  const page = await context.newPage();
  page.on('console', (message) => {
    if (['error', 'warning'].includes(message.type())) {
      consoleMessages.push({ type: message.type(), text: message.text() });
    }
  });
  page.on('pageerror', (error) => {
    pageErrors.push(error.message);
  });
  await installRoutes(page, state, networkLog);
  return { context, page, networkLog, consoleMessages, pageErrors };
}

async function gotoRoute(page, baseUrl, routeConfig) {
  await page.goto(`${baseUrl.replace(/\/$/, '')}${routeConfig.path}`);
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(250);
}

async function collectDomAudit(page) {
  return page.evaluate(() => {
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    const interactive = Array.from(
      document.querySelectorAll('button,a[href],input,textarea,select')
    ).map((element, index) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      const disabled =
        element instanceof HTMLButtonElement ||
        element instanceof HTMLInputElement ||
        element instanceof HTMLTextAreaElement ||
        element instanceof HTMLSelectElement
          ? element.disabled
          : false;
      const label =
        element.getAttribute('aria-label') ||
        element.textContent?.trim() ||
        element.getAttribute('placeholder') ||
        element.getAttribute('name') ||
        element.getAttribute('id') ||
        element.getAttribute('type') ||
        '';
      const visible =
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== 'hidden' &&
        style.display !== 'none' &&
        Number(style.opacity || '1') > 0.01;
      return {
        index,
        tag: element.tagName.toLowerCase(),
        type: element.getAttribute('type') || '',
        label,
        disabled,
        visible,
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
        overflow:
          !['hidden', 'clip'].includes(style.overflowX) &&
          !['hidden', 'clip'].includes(style.overflowY) &&
          (element.scrollWidth > element.clientWidth + 2 ||
            element.scrollHeight > element.clientHeight + 2),
      };
    });

    const visibleInteractive = interactive.filter((item) => item.visible);
    const overlaps = [];
    for (let left = 0; left < visibleInteractive.length; left += 1) {
      for (let right = left + 1; right < visibleInteractive.length; right += 1) {
        const a = visibleInteractive[left].rect;
        const b = visibleInteractive[right].rect;
        const x = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
        const y = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
        const area = x * y;
        const minArea = Math.min(a.width * a.height, b.width * b.height);
        const nestedSubmitControl =
          ((visibleInteractive[left].tag === 'input' ||
            visibleInteractive[left].tag === 'textarea') &&
            visibleInteractive[right].tag === 'button' &&
            visibleInteractive[right].type === 'submit') ||
          ((visibleInteractive[right].tag === 'input' ||
            visibleInteractive[right].tag === 'textarea') &&
            visibleInteractive[left].tag === 'button' &&
            visibleInteractive[left].type === 'submit');
        if (!nestedSubmitControl && minArea > 0 && area / minArea > 0.35) {
          overlaps.push({
            left: visibleInteractive[left].label,
            right: visibleInteractive[right].label,
            ratio: Number((area / minArea).toFixed(3)),
          });
        }
      }
    }

    const bodyText = document.body.innerText || '';
    const mojibakeMatches =
      bodyText.match(/\uFFFD|ì|ê|ë|í|î|ï|Â|Ã|\?{3,}|[�]/g) || [];

    return {
      viewport,
      title: document.title,
      url: location.href,
      currentPageLinkCount: document.querySelectorAll('a[aria-current="page"]').length,
      bodyTextLength: bodyText.length,
      bodyTextSample: bodyText.slice(0, 500),
      mojibakeCount: mojibakeMatches.length,
      interactive,
      visibleInteractiveCount: visibleInteractive.length,
      emptyButtonLabels: visibleInteractive
        .filter((item) => item.tag === 'button' && !item.label.trim())
        .map((item) => item.index),
      smallTouchTargets: visibleInteractive
        .filter((item) => ['button', 'a', 'input', 'select', 'textarea'].includes(item.tag))
        .filter((item) => item.rect.width < 24 || item.rect.height < 24)
        .map((item) => ({ index: item.index, label: item.label, rect: item.rect })),
      overflowingElements: visibleInteractive
        .filter((item) => item.overflow)
        .map((item) => ({ index: item.index, label: item.label, rect: item.rect })),
      overlaps,
    };
  });
}

async function screenshot(page, outputDir, name) {
  const file = path.join(outputDir, `${sanitizeName(name)}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

function actionResult(name, route, ok, details = {}) {
  return {
    name,
    route: route.path,
    ok,
    ...details,
  };
}

async function countVisible(page, selector) {
  return page.locator(selector).count();
}

async function withFreshPage(browser, baseUrl, routeConfig, stateFactory, viewport, callback) {
  const state = stateFactory();
  const session = await createPage(browser, routeConfig, state, viewport);
  try {
    await gotoRoute(session.page, baseUrl, routeConfig);
    return await callback(session, state);
  } finally {
    await session.context.close();
  }
}

async function auditRoute(browser, baseUrl, routeConfig, stateFactory, outputDir, viewport) {
  const label = viewportLabel(viewport);
  return withFreshPage(browser, baseUrl, routeConfig, stateFactory, viewport, async (session) => {
    const beforePath = await screenshot(session.page, outputDir, `${label}-${routeConfig.name}-initial`);
    const dom = await collectDomAudit(session.page);
    return {
      route: routeConfig.path,
      name: routeConfig.name,
      viewport: label,
      screenshot: beforePath,
      dom,
      networkLog: session.networkLog,
      consoleMessages: session.consoleMessages,
      pageErrors: session.pageErrors,
    };
  });
}

async function auditButtonClicks(browser, baseUrl, routeConfig, stateFactory, outputDir, viewport) {
  const label = viewportLabel(viewport);
  const baseline = await withFreshPage(
    browser,
    baseUrl,
    routeConfig,
    stateFactory,
    viewport,
    async (session) => collectDomAudit(session.page)
  );
  const buttons = baseline.interactive.filter((item) => item.tag === 'button' && item.visible);
  const results = [];

  for (let i = 0; i < buttons.length; i += 1) {
    const buttonMeta = buttons[i];
    if (buttonMeta.disabled) {
      results.push(actionResult('button-disabled', routeConfig, true, { viewport: label, label: buttonMeta.label, index: i }));
      continue;
    }

    const state = stateFactory();
    const session = await createPage(browser, routeConfig, state, viewport);
    try {
      await gotoRoute(session.page, baseUrl, routeConfig);
      const locator = session.page.locator('button:visible').nth(i);
      const currentCount = await countVisible(session.page, 'button:visible');
      if (i >= currentCount) {
        results.push(actionResult('button-missing-after-reload', routeConfig, false, { viewport: label, label: buttonMeta.label, index: i }));
        continue;
      }
      const disabled = await locator.evaluate((element) => element.disabled).catch(() => true);
      if (disabled) {
        results.push(actionResult('button-disabled-after-reload', routeConfig, true, { viewport: label, label: buttonMeta.label, index: i }));
        continue;
      }
      await locator.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => {});
      const before = await screenshot(session.page, outputDir, `${label}-${routeConfig.name}-button-${i}-before`);
      await locator.click({ timeout: 5000 }).catch(async (error) => {
        throw new Error(`click failed: ${error.message}`);
      });
      await session.page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
      await session.page.waitForTimeout(300);
      const after = await screenshot(session.page, outputDir, `${label}-${routeConfig.name}-button-${i}-after`);
      const bodyText = await session.page.evaluate(() => document.body.innerText.slice(0, 800));
      results.push(
        actionResult('button-click', routeConfig, true, {
          viewport: label,
          label: buttonMeta.label,
          index: i,
          before,
          after,
          outputUrl: session.page.url(),
          outputTextSample: bodyText,
          networkLog: session.networkLog,
          consoleMessages: session.consoleMessages,
          pageErrors: session.pageErrors,
        })
      );
    } catch (error) {
      results.push(
        actionResult('button-click', routeConfig, false, {
          viewport: label,
          label: buttonMeta.label,
          index: i,
          error: error.message,
          consoleMessages: session.consoleMessages,
          pageErrors: session.pageErrors,
        })
      );
    } finally {
      await session.context.close();
    }
  }

  return results;
}

function valueForInput(type, name) {
  const key = `${type || ''} ${name || ''}`.toLowerCase();
  if (key.includes('email')) return 'audit@example.com';
  if (key.includes('password')) return 'Passw0rd!23';
  if (key.includes('age')) return '31';
  if (key.includes('height')) return '166';
  if (key.includes('weight')) return '63';
  if (key.includes('calorie')) return '2100';
  if (key.includes('carb')) return '240';
  if (key.includes('protein')) return '95';
  if (key.includes('fat')) return '60';
  if (type === 'number') return '42';
  return 'audit input';
}

async function auditInputs(browser, baseUrl, routeConfig, stateFactory, outputDir, viewport) {
  const label = viewportLabel(viewport);
  const baseline = await withFreshPage(
    browser,
    baseUrl,
    routeConfig,
    stateFactory,
    viewport,
    async (session) => collectDomAudit(session.page)
  );
  const fields = baseline.interactive.filter((item) =>
    ['input', 'textarea', 'select'].includes(item.tag) && item.visible
  );
  const results = [];

  for (let i = 0; i < fields.length; i += 1) {
    const fieldMeta = fields[i];
    const state = stateFactory();
    const session = await createPage(browser, routeConfig, state, viewport);
    try {
      await gotoRoute(session.page, baseUrl, routeConfig);
      const locator = session.page.locator(`${fieldMeta.tag}:visible`).nth(
        fields.slice(0, i).filter((field) => field.tag === fieldMeta.tag).length
      );
      await locator.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => {});
      const before = await screenshot(session.page, outputDir, `${label}-${routeConfig.name}-field-${i}-before`);
      const descriptor = await locator.evaluate((element) => ({
        tag: element.tagName.toLowerCase(),
        type: element.getAttribute('type') || '',
        name: element.getAttribute('name') || '',
        id: element.getAttribute('id') || '',
        disabled: Boolean(element.disabled),
        options: element instanceof HTMLSelectElement
          ? Array.from(element.options).map((option) => option.value)
          : [],
      }));
      if (descriptor.disabled) {
        results.push(actionResult('field-disabled', routeConfig, true, { viewport: label, index: i, field: descriptor }));
        continue;
      }

      let outputValue = '';
      if (descriptor.tag === 'select') {
        const options = descriptor.options.filter(Boolean);
        const target = options[1] || options[0];
        if (target) {
          await locator.selectOption(target);
          outputValue = target;
        }
      } else if (descriptor.type === 'checkbox' || descriptor.type === 'radio') {
        await locator.check({ force: true });
        outputValue = 'checked';
      } else {
        outputValue = valueForInput(descriptor.type, `${descriptor.name} ${descriptor.id}`);
        await locator.fill(outputValue);
      }
      await session.page.waitForTimeout(200);
      const actualValue = await locator.evaluate((element) =>
        element instanceof HTMLInputElement ||
        element instanceof HTMLTextAreaElement ||
        element instanceof HTMLSelectElement
          ? element.value
          : ''
      );
      const after = await screenshot(session.page, outputDir, `${label}-${routeConfig.name}-field-${i}-after`);
      results.push(
        actionResult('field-input', routeConfig, true, {
          viewport: label,
          index: i,
          field: descriptor,
          inputValue: outputValue,
          outputValue: actualValue,
          before,
          after,
          networkLog: session.networkLog,
          consoleMessages: session.consoleMessages,
          pageErrors: session.pageErrors,
        })
      );
    } catch (error) {
      results.push(
        actionResult('field-input', routeConfig, false, {
          viewport: label,
          index: i,
          label: fieldMeta.label,
          error: error.message,
          consoleMessages: session.consoleMessages,
          pageErrors: session.pageErrors,
        })
      );
    } finally {
      await session.context.close();
    }
  }

  return results;
}

async function runScenario(name, browser, baseUrl, routeConfig, stateFactory, outputDir, viewport, scenario) {
  const label = viewportLabel(viewport);
  const state = stateFactory();
  const session = await createPage(browser, routeConfig, state, viewport);
  try {
    await gotoRoute(session.page, baseUrl, routeConfig);
    const before = await screenshot(session.page, outputDir, `${label}-${name}-before`);
    const details = await scenario(session.page, state, session);
    await session.page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    await session.page.waitForTimeout(400);
    const after = await screenshot(session.page, outputDir, `${label}-${name}-after`);
    const bodyText = await session.page.evaluate(() => document.body.innerText.slice(0, 1200));
    return actionResult(name, routeConfig, true, {
      viewport: label,
      before,
      after,
      expectVisualChange: true,
      outputUrl: session.page.url(),
      outputTextSample: bodyText,
      details,
      networkLog: session.networkLog,
      consoleMessages: session.consoleMessages,
      pageErrors: session.pageErrors,
    });
  } catch (error) {
    return actionResult(name, routeConfig, false, {
      viewport: label,
      error: error.message,
      networkLog: session.networkLog,
      consoleMessages: session.consoleMessages,
      pageErrors: session.pageErrors,
    });
  } finally {
    await session.context.close();
  }
}

async function runScenarios(browser, baseUrl, stateFactory, outputDir, viewport) {
  const routes = Object.fromEntries(ROUTES.map((route) => [route.name, route]));
  return [
    await runScenario('login-empty-error', browser, baseUrl, routes.login, stateFactory, outputDir, viewport, async (page) => {
      await page.locator('button[type="submit"]').click();
      await page.waitForTimeout(200);
      const text = await page.evaluate(() => document.body.innerText);
      if (!text.trim()) throw new Error('No visible output after empty login submit.');
      return { textLength: text.length };
    }),
    await runScenario('login-success', browser, baseUrl, routes.login, stateFactory, outputDir, viewport, async (page) => {
      await page.locator('#loginId').fill('uiaudit');
      await page.locator('#password').fill('Passw0rd!23');
      await page.locator('button[type="submit"]').click();
      await page.waitForURL('**/', { timeout: 8000 });
      return { url: page.url() };
    }),
    await runScenario('signup-success', browser, baseUrl, routes.signup, stateFactory, outputDir, viewport, async (page) => {
      await page.locator('#loginId').fill('signup-audit');
      await page.locator('#password').fill('Passw0rd!23');
      await page.locator('#nickname').fill('Audit');
      await page.locator('#email').fill('audit@example.com');
      await page.locator('button[type="submit"]').click();
      await page.waitForTimeout(500);
      return { formCount: await page.locator('form').count() };
    }),
    await runScenario('onboarding-submit', browser, baseUrl, routes.onboarding, stateFactory, outputDir, viewport, async (page) => {
      await page.locator('select').nth(0).selectOption({ index: 1 }).catch(() => {});
      await page.locator('input[type="number"]').nth(0).fill('30');
      await page.locator('input[type="number"]').nth(1).fill('166');
      await page.locator('input[type="number"]').nth(2).fill('63');
      const selectCount = await page.locator('select').count();
      for (let i = 1; i < selectCount; i += 1) {
        await page.locator('select').nth(i).selectOption({ index: 1 }).catch(() => {});
      }
      await page.locator('button[type="button"]').filter({ hasText: /\uC5C6\uC74C/ }).first().click();
      await page.locator('button[type="button"]').filter({ hasText: /\uD574\uB2F9 \uC5C6\uC74C/ }).first().click();
      await page.locator('button[type="submit"]').click();
      await page.waitForURL('**/', { timeout: 8000 });
      return { url: page.url() };
    }),
    await runScenario('home-recommendation-apply', browser, baseUrl, routes.home, stateFactory, outputDir, viewport, async (page, state) => {
      await page.locator('button[aria-label]').filter({ hasText: '' }).count().catch(() => {});
      const workoutButton = page.locator('button[aria-label$="추천 추가"]:not([disabled])').first();
      await workoutButton.click();
      await page.locator('div.fixed.inset-0 button:visible').last().click();
      await page.waitForTimeout(300);
      const dietButton = page.locator('button[aria-label$="추천 반영"]:not([disabled])').first();
      await dietButton.click();
      await page.locator('div.fixed.inset-0 button:visible').last().click();
      await page.waitForTimeout(300);
      if (state.counters.workoutAdd < 1 || state.counters.dietReplace < 1) {
        throw new Error('Recommendation apply did not call expected APIs.');
      }
      await page
        .locator('a[href="/recommend"] .sr-only', { hasText: '변경된 플랜 있음' })
        .waitFor({ state: 'attached', timeout: 5000 });
      return { workoutAdd: state.counters.workoutAdd, dietReplace: state.counters.dietReplace };
    }),
    await runScenario('chat-send-feedback', browser, baseUrl, routes.chat, stateFactory, outputDir, viewport, async (page, state) => {
      const input = page.locator('input[type="text"]').last();
      await input.fill('Please make a light weekly workout plan.');
      await page.locator('button[type="submit"]').click();
      await page.waitForFunction(() => document.body.innerText.includes('Weekly workout plan'), null, { timeout: 12000 });
      await page.locator('button[aria-label="좋아요"]').last().click();
      await page.waitForTimeout(200);
      if (state.counters.chat < 1 || state.counters.feedback < 1) {
        throw new Error('Chat or feedback API was not called.');
      }
      return { chat: state.counters.chat, feedback: state.counters.feedback };
    }),
    await runScenario('chat-thumbs-down-feedback', browser, baseUrl, routes.chat, stateFactory, outputDir, viewport, async (page, state) => {
      const input = page.locator('input[type="text"]').last();
      await input.fill('Please make a light weekly diet plan.');
      await page.locator('button[type="submit"]').click();
      await page.waitForFunction(() => document.body.innerText.includes('Weekly workout plan'), null, { timeout: 12000 });
      await page.locator('button[aria-label="싫어요"]').last().click();
      await page.locator('div.fixed.inset-0 button:visible').filter({ hasText: '도움이 안 됐어요' }).click();
      await page.locator('div.fixed.inset-0 textarea').fill('Audit feedback comment');
      await page.locator('div.fixed.inset-0 button:visible').filter({ hasText: '보내기' }).click();
      await page.waitForTimeout(300);
      if (state.counters.chat < 1 || state.counters.feedback < 1) {
        throw new Error('Thumbs-down feedback API was not called.');
      }
      return { chat: state.counters.chat, feedback: state.counters.feedback };
    }),
    await runScenario('profile-edit-persona-goal', browser, baseUrl, routes.profile, stateFactory, outputDir, viewport, async (page, state) => {
      const personaButtons = page.locator('button').filter({ hasText: /AI|코치|누나|언니|형|선생|메이트/ });
      if (await personaButtons.count()) {
        await personaButtons.nth(0).click();
        await page.waitForTimeout(200);
      }
      await page.locator('button').filter({ hasText: '내 정보 수정' }).click();
      await page.locator('input[name="nickname"]').fill('Audit Demo');
      await page.locator('input[name="age"]').fill('31');
      await page.locator('input[name="height"]').fill('166');
      await page.locator('input[name="weight"]').fill('63');
      await page.locator('button').filter({ hasText: '저장' }).last().click();
      await page.waitForTimeout(400);
      await page.locator('button').filter({ hasText: '운동 목표 설정' }).click();
      await page.locator('select').last().selectOption({ index: 1 }).catch(() => {});
      await page.locator('button').filter({ hasText: '저장' }).last().click();
      await page.waitForTimeout(400);
      if (state.counters.profileSave < 1) {
        throw new Error('Profile save API was not called.');
      }
      return { profileSave: state.counters.profileSave, personaPatch: state.counters.personaPatch };
    }),
    await runScenario('profile-notice-actions', browser, baseUrl, routes.profile, stateFactory, outputDir, viewport, async (page) => {
      await page.locator('button').filter({ hasText: '알림 설정' }).click();
      await page.waitForFunction(() => document.body.innerText.includes('데모 버전에서는 알림'), null, { timeout: 5000 });
      await page.locator('div.fixed.inset-0 button:visible').filter({ hasText: '확인' }).click();
      await page.locator('button').filter({ hasText: '고객 문의' }).click();
      await page.waitForFunction(() => document.body.innerText.includes('정식 문의 폼'), null, { timeout: 5000 });
      return { noticeButtons: 2 };
    }),
    await runScenario('recommend-detail-complete', browser, baseUrl, routes.recommend, stateFactory, outputDir, viewport, async (page, state) => {
      await page.locator('button').filter({ hasText: /상체|스트레칭|운동|식단|Breakfast|Lunch|Dinner/ }).first().click();
      await page.waitForTimeout(300);
      const detailButtons = page.locator('div.fixed.inset-0 button:visible');
      if ((await detailButtons.count()) > 1) {
        await detailButtons.last().click();
        await page.waitForTimeout(300);
      }
      const confirmButtons = page.locator('div.fixed.inset-0 button:visible');
      if ((await confirmButtons.count()) > 1) {
        await confirmButtons.last().click();
        await page.waitForTimeout(300);
      }
      return { planCheck: state.counters.planCheck, planDelete: state.counters.planDelete };
    }),
  ];
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
    if (result.status === 0) {
      return candidate;
    }
  }

  return process.env.PYTHON || 'python';
}

const ROUTES = [
  { name: 'home', path: '/', auth: true },
  { name: 'chat', path: '/chat', auth: true },
  { name: 'recommend', path: '/recommend', auth: true },
  { name: 'profile', path: '/profile', auth: true },
  { name: 'login', path: '/login', auth: false },
  { name: 'signup', path: '/signup', auth: false },
  { name: 'onboarding', path: '/onboarding', auth: true },
];

const VIEWPORTS = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'desktop', width: 1280, height: 900 },
];

async function main() {
  const { chromium } = loadPlaywright();
  const baseUrl = parseArg('--url', process.env.AUDIT_BASE_URL || 'http://localhost:3100');
  const outputRoot = parseArg(
    '--output',
    path.join(process.cwd(), 'docs', 'ui-audit', new Date().toISOString().replace(/[:.]/g, '-'))
  );
  const screenshotDir = path.join(outputRoot, 'screenshots');
  ensureDir(screenshotDir);

  const today = kstDate();
  const stateFactory = () => createAuditState(today);
  const browser = await chromium.launch({ headless: true });
  const report = {
    ok: true,
    baseUrl,
    today,
    outputRoot,
    viewports: VIEWPORTS,
    routes: [],
    buttonActions: [],
    fieldActions: [],
    scenarios: [],
    issues: [],
    opencv: null,
  };

  try {
    for (const viewport of VIEWPORTS) {
      for (const routeConfig of ROUTES) {
        report.routes.push(
          await auditRoute(browser, baseUrl, routeConfig, stateFactory, screenshotDir, viewport)
        );
        report.buttonActions.push(
          ...(await auditButtonClicks(browser, baseUrl, routeConfig, stateFactory, screenshotDir, viewport))
        );
        report.fieldActions.push(
          ...(await auditInputs(browser, baseUrl, routeConfig, stateFactory, screenshotDir, viewport))
        );
      }
      report.scenarios.push(...(await runScenarios(browser, baseUrl, stateFactory, screenshotDir, viewport)));
    }
  } finally {
    await browser.close();
  }

  for (const route of report.routes) {
    if (route.pageErrors.length) {
      report.issues.push({ level: 'error', type: 'page-error', route: route.route, viewport: route.viewport, details: route.pageErrors });
    }
    if (route.dom.mojibakeCount > 0) {
      report.issues.push({
        level: 'error',
        type: 'mojibake-text',
        route: route.route,
        viewport: route.viewport,
        count: route.dom.mojibakeCount,
        sample: route.dom.bodyTextSample,
      });
    }
    if (route.dom.emptyButtonLabels.length) {
      report.issues.push({
        level: 'error',
        type: 'button-without-label',
        route: route.route,
        viewport: route.viewport,
        buttons: route.dom.emptyButtonLabels,
      });
    }
    if (['/', '/chat', '/recommend', '/profile'].includes(route.route) && route.dom.currentPageLinkCount !== 1) {
      report.issues.push({
        level: 'error',
        type: 'active-nav-state-invalid',
        route: route.route,
        viewport: route.viewport,
        currentPageLinkCount: route.dom.currentPageLinkCount,
      });
    }
    if (route.dom.overlaps.length) {
      report.issues.push({
        level: 'warning',
        type: 'interactive-overlap',
        route: route.route,
        viewport: route.viewport,
        overlaps: route.dom.overlaps.slice(0, 10),
      });
    }
    if (route.dom.smallTouchTargets.length) {
      report.issues.push({
        level: 'warning',
        type: 'small-touch-target',
        route: route.route,
        viewport: route.viewport,
        targets: route.dom.smallTouchTargets.slice(0, 10),
      });
    }
    if (route.dom.overflowingElements.length) {
      report.issues.push({
        level: 'warning',
        type: 'interactive-overflow',
        route: route.route,
        viewport: route.viewport,
        elements: route.dom.overflowingElements.slice(0, 10),
      });
    }
  }

  for (const action of [...report.buttonActions, ...report.fieldActions, ...report.scenarios]) {
    if (!action.ok) {
      report.issues.push({
        level: 'error',
        type: 'action-failed',
        route: action.route,
        viewport: action.viewport,
        name: action.name,
        label: action.label,
        error: action.error,
      });
    }
    if (action.pageErrors?.length) {
      report.issues.push({
        level: 'error',
        type: 'action-page-error',
        route: action.route,
        viewport: action.viewport,
        name: action.name,
        details: action.pageErrors,
      });
    }
  }

  const reportPath = path.join(outputRoot, 'playwright-ui-audit.json');
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));

  const opencvPython = resolvePythonWithCv2();
  const opencv = spawnSync(opencvPython, [path.join(__dirname, 'audit-ui-opencv.py'), reportPath], {
    encoding: 'utf-8',
  });
  if (opencv.stdout) process.stdout.write(opencv.stdout);
  if (opencv.stderr) process.stderr.write(opencv.stderr);
  if (opencv.status !== 0) {
    report.ok = false;
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
    routes: report.routes.length,
    buttonActions: report.buttonActions.length,
    fieldActions: report.fieldActions.length,
    scenarios: report.scenarios.length,
    issues: report.issues.length,
    errors: report.issues.filter((issue) => issue.level === 'error').length,
    warnings: report.issues.filter((issue) => issue.level === 'warning').length,
    reportPath,
  };
  console.log(JSON.stringify(summary, null, 2));

  if (!report.ok) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
