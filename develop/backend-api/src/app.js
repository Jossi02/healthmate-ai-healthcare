const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const morgan = require('morgan');
require('dotenv').config();
const { errorHandler, notFoundHandler } = require('./middleware/errorHandler');
const logger = require('./utils/logger');
const supabase = require('./config/db');
const rateLimitModule = require('express-rate-limit');
const { getSecurityConfig } = require('./config/security');

const rateLimit = rateLimitModule.rateLimit || rateLimitModule;

const app = express();
const securityConfig = getSecurityConfig();
if (securityConfig.trustProxyHops > 0) {
  app.set('trust proxy', securityConfig.trustProxyHops);
}

async function checkIdempotencyTable() {
  try {
    const { error } = await supabase
      .from('ai_was_idempotency_keys')
      .select('idempotency_key')
      .limit(1);

    if (!error) return { ok: true, status: 'available' };

    logger.warn('Readiness idempotency check failed.');
    return {
      ok: false,
      status: idempotencyTableIsMissing(error) ? 'missing' : 'unavailable',
    };
  } catch {
    logger.error('Readiness idempotency check rejected.');
    return { ok: false, status: 'unavailable' };
  }
}

function idempotencyTableIsMissing(check) {
  return check?.code === 'PGRST205' || check?.code === '42P01';
}

const authRateLimiter = rateLimit({
  windowMs: securityConfig.authRateLimitWindowMs,
  limit: securityConfig.authRateLimitMax,
  standardHeaders: true,
  legacyHeaders: false,
  handler: (_req, res) => {
    res.status(429).json({ error: 'Too many authentication attempts.' });
  },
});

// ─── 보안 및 기본 미들웨어 ────────────────────────────────────────────
app.use(helmet()); // HTTP 보안 헤더 자동 설정

// CORS: security config의 명시적 allowlist만 허용
app.use(
  cors({
    origin: securityConfig.corsAllowedOrigins,
    credentials: false,
  })
);

// 요청 본문(Body) 파싱
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));

// ─── HTTP 요청 로깅 (morgan) ──────────────────────────────────────────
// 개발: 컬러 상세 로그, 프로덕션: 간결한 combined 포맷
const morganFormat = process.env.NODE_ENV === 'production' ? 'combined' : 'dev';
app.use(
  morgan(morganFormat, {
    stream: {
      // morgan 로그를 winston logger로 전달
      write: (message) => logger.http(message.trim()),
    },
  })
);

// ─── 헬스 체크 엔드포인트 ─────────────────────────────────────────────
app.get('/api/health', (req, res) => {
  res.status(200).json({
    success: true,
    message: '서버가 정상 동작 중입니다.',
    timestamp: new Date().toISOString(),
    environment: process.env.NODE_ENV || 'development',
  });
});

app.get('/api/readiness', async (req, res) => {
  const requireDatabaseIdempotency =
    String(process.env.REQUIRE_IDEMPOTENCY_TABLE || '').toLowerCase() === 'true';
  const idempotencyTable = await checkIdempotencyTable();
  const idempotencyMode = idempotencyTable.ok ? 'database' : 'memory_fallback';
  const blockingIssues = [];
  const warnings = [];

  if (!idempotencyTable.ok && (idempotencyTable.status !== 'missing' || requireDatabaseIdempotency)) {
    blockingIssues.push('idempotency_table_unavailable');
  }
  if (!idempotencyTable.ok && idempotencyTable.status === 'missing') {
    warnings.push('idempotency_database_unavailable');
  }

  const ok = blockingIssues.length === 0;
  res.status(ok ? 200 : 503).json({
    ok,
    timestamp: new Date().toISOString(),
    environment: process.env.NODE_ENV || 'development',
    idempotency: {
      mode: idempotencyMode,
      database_required: requireDatabaseIdempotency,
      table_status: idempotencyTable.status,
      table_available: idempotencyTable.ok,
      memory_fallback_enabled: idempotencyTable.status === 'missing' && !requireDatabaseIdempotency,
    },
    warnings,
    blocking_issues: blockingIssues,
  });
});

// ─── API 라우터 (프론트엔드용) ────────────────────────────────────────
app.post('/api/v1/auth/signup', authRateLimiter);
app.post('/api/v1/auth/login', authRateLimiter);
app.use('/api/v1/auth', require('./routes/auth'));
app.use('/api/v1/users', require('./routes/users'));
app.use('/api/v1/ai', require('./routes/ai'));
app.use('/api/v1/home', require('./routes/home'));
app.use('/api/v1/admin', require('./routes/admin'));
app.use('/api/v1/chat', require('./routes/chat'));

// ─── Internal API 라우터 (FastAPI/AI 서버용) ──────────────────────────
// was_api_contract.md 기준: /api/user/profile/:user_id 등
app.use('/api', require('./routes/internal'));

// ─── 에러 핸들러 (라우터 이후에 위치해야 함) ─────────────────────────
app.use(notFoundHandler);
app.use(errorHandler);

module.exports = app;
