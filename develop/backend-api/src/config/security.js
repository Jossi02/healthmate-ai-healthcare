const crypto = require('crypto');

const DEFAULT_DEVELOPMENT_ORIGINS = Object.freeze([
  'http://localhost:3000',
  'http://127.0.0.1:3000',
]);
const MIN_PRODUCTION_SECRET_LENGTH = 32;

class SecurityConfigError extends Error {
  constructor(message) {
    super(message);
    this.name = 'SecurityConfigError';
    this.code = 'SECURITY_CONFIG_INVALID';
  }
}

function requiredSecret(env, name, nodeEnv) {
  const value = String(env[name] || '').trim();
  if (!value) {
    throw new SecurityConfigError(`${name} is required.`);
  }

  const normalized = value.toLowerCase();
  const placeholder = normalized === 'capstone_jwt_secret_key'
    || normalized.includes('your_')
    || normalized.includes('your-')
    || normalized.includes('replace-with')
    || normalized.includes('change-me');

  if (placeholder) {
    throw new SecurityConfigError(`${name} must not use an example or placeholder value.`);
  }

  if (nodeEnv === 'production' && value.length < MIN_PRODUCTION_SECRET_LENGTH) {
    throw new SecurityConfigError(
      `${name} must be a non-placeholder secret of at least ${MIN_PRODUCTION_SECRET_LENGTH} characters in production.`
    );
  }
  return value;
}

function positiveInteger(env, name, fallback) {
  const value = env[name] === undefined || env[name] === '' ? fallback : Number(env[name]);
  if (!Number.isInteger(value) || value <= 0) {
    throw new SecurityConfigError(`${name} must be a positive integer.`);
  }
  return value;
}

function allowedOrigins(env, nodeEnv) {
  const configured = String(env.CORS_ALLOWED_ORIGINS || '')
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean);

  if (configured.includes('*')) {
    throw new SecurityConfigError('CORS_ALLOWED_ORIGINS must not contain a wildcard.');
  }
  if (configured.length > 0) {
    return Object.freeze([...new Set(configured)]);
  }
  if (nodeEnv === 'production') {
    throw new SecurityConfigError('CORS_ALLOWED_ORIGINS is required in production.');
  }
  return DEFAULT_DEVELOPMENT_ORIGINS;
}

function loadSecurityConfig(env = process.env) {
  const nodeEnv = String(env.NODE_ENV || 'development').trim().toLowerCase();
  const trustProxyHops = env.TRUST_PROXY_HOPS === undefined || env.TRUST_PROXY_HOPS === ''
    ? 0
    : Number(env.TRUST_PROXY_HOPS);
  if (!Number.isInteger(trustProxyHops) || trustProxyHops < 0) {
    throw new SecurityConfigError('TRUST_PROXY_HOPS must be a non-negative integer.');
  }
  return Object.freeze({
    nodeEnv,
    jwtSecret: requiredSecret(env, 'JWT_SECRET', nodeEnv),
    jwtExpiresIn: String(env.JWT_EXPIRES_IN || '7d').trim() || '7d',
    internalApiKey: requiredSecret(env, 'INTERNAL_API_KEY', nodeEnv),
    corsAllowedOrigins: allowedOrigins(env, nodeEnv),
    trustProxyHops,
    authRateLimitWindowMs: positiveInteger(env, 'AUTH_RATE_LIMIT_WINDOW_MS', 15 * 60 * 1000),
    authRateLimitMax: positiveInteger(env, 'AUTH_RATE_LIMIT_MAX', 10),
  });
}

let cachedConfig;

function getSecurityConfig() {
  if (!cachedConfig) {
    cachedConfig = loadSecurityConfig(process.env);
  }
  return cachedConfig;
}

function safeSecretEqual(provided, expected) {
  if (!provided || !expected || typeof provided !== 'string' || typeof expected !== 'string') return false;
  const left = Buffer.from(provided);
  const right = Buffer.from(expected);
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

module.exports = {
  DEFAULT_DEVELOPMENT_ORIGINS,
  SecurityConfigError,
  getSecurityConfig,
  loadSecurityConfig,
  safeSecretEqual,
};
