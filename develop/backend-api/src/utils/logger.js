const { createLogger, format, transports } = require('winston');
const path = require('path');

const {
  combine,
  timestamp,
  printf,
  colorize,
  splat,
} = format;

const REDACTED = '[REDACTED]';
const SPLAT = Symbol.for('splat');
const SENSITIVE_KEY_PATTERN =
  '(?:authorization|proxy[_-]?authorization|cookie|set[_-]?cookie|x[_-]?api[_-]?key|internal[_-]?api[_-]?key|api[_-]?key|access[_-]?(?:key|token)|refresh[_-]?token|id[_-]?token|token|password[_-]?hash|password|passwd|pwd|secret[_-]?key|secret|credential|private[_-]?key|client[_-]?secret|service[_-]?role(?:[_-]?key)?|supabase[_-]?service[_-]?role[_-]?key|jwt)';
const SENSITIVE_KEY = /(?:proxyauthorization|authorization|setcookie|cookie|xapikey|apikey|accesstoken|refreshtoken|idtoken|token|password|passwd|pwd|secret|credential|privatekey|clientsecret|servicerolekey|servicerole|supabaseservicerolekey|jwt)/i;
const CREDENTIAL_HEADER = new RegExp(
  `((?:["']?${SENSITIVE_KEY_PATTERN}["']?)\\s*[:=]\\s*)(?:Bearer|Basic)\\s+[^\\s,;}&#]+`,
  'gi'
);
const CREDENTIAL_ASSIGNMENT = new RegExp(
  `((?:["']?${SENSITIVE_KEY_PATTERN}["']?)\\s*[:=]\\s*)(?!\\[REDACTED\\])(?:"(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|[^\\s,;}&#]+)`,
  'gi'
);
const CREDENTIAL_URL = /(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi;

function isSensitiveKey(key) {
  const normalized = String(key).toLowerCase().replace(/[^a-z0-9]/g, '');
  return SENSITIVE_KEY.test(normalized);
}

function redactString(value) {
  if (typeof value !== 'string' || value.length === 0) return value;

  return value
    .replace(CREDENTIAL_HEADER, `$1${REDACTED}`)
    .replace(CREDENTIAL_ASSIGNMENT, `$1${REDACTED}`)
    .replace(CREDENTIAL_URL, `$1${REDACTED}@`);
}

function errorMetadata(value) {
  const source = value && typeof value === 'object' ? value : {};
  const result = {};
  const name = typeof source.name === 'string' ? source.name : '';
  if (/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(name)) result.name = name;

  for (const property of ['code', 'status', 'statusCode', 'errno', 'syscall']) {
    const item = source[property];
    if (typeof item === 'string' && /^[A-Za-z0-9_.-]{1,64}$/.test(item)) {
      result[property] = item;
    } else if (typeof item === 'number' && Number.isFinite(item)) {
      result[property] = item;
    }
  }

  const upstreamStatus = Number(source.response?.status);
  if (Number.isInteger(upstreamStatus) && upstreamStatus >= 100 && upstreamStatus <= 599) {
    result.upstreamStatus = upstreamStatus;
  }
  return Object.keys(result).length > 0 ? result : { name: 'Error' };
}

function redactValue(value, key, seen = new WeakSet()) {
  if (isSensitiveKey(key)) return REDACTED;
  if (typeof value === 'string') return redactString(value);
  if (value === null || value === undefined || typeof value === 'boolean' || typeof value === 'number') {
    return value;
  }
  if (typeof value === 'bigint') return String(value);
  if (typeof value !== 'object') return String(value);
  if (
    value instanceof Error
    || (
      key === undefined
      && !Array.isArray(value)
      && ('message' in value || 'stack' in value || 'response' in value || 'config' in value)
    )
  ) {
    return errorMetadata(value);
  }
  if (seen.has(value)) return '[Circular]';
  seen.add(value);

  if (value instanceof Date) return value.toISOString();

  if (Array.isArray(value)) {
    return value.map((item) => redactValue(item, undefined, seen));
  }

  const result = {};
  for (const property of Object.keys(value)) {
    try {
      result[property] = redactValue(value[property], property, seen);
    } catch {
      result[property] = '[Unavailable]';
    }
  }
  return result;
}

function serializeLogValue(value) {
  const safeValue = redactValue(value);
  if (typeof safeValue === 'string') return safeValue;
  try {
    return JSON.stringify(safeValue);
  } catch {
    return redactString(String(value));
  }
}

const redactFormat = format((info) => {
  for (const property of Object.keys(info)) {
    if (property !== 'level') info[property] = redactValue(info[property], property);
  }
  if (info[SPLAT]) info[SPLAT] = redactValue(info[SPLAT], undefined);
  return info;
});

const logFormat = printf((info) => {
  const { level, message, timestamp: time, ...metadata } = info;
  const detail = Object.keys(metadata).length > 0
    ? ` ${serializeLogValue(metadata)}`
    : '';
  return `${time} [${level}]: ${serializeLogValue(message)}${detail}`;
});

function buildFormat(timeFormat, includeColor = false) {
  return combine(
    ...(includeColor ? [colorize({ all: true })] : []),
    timestamp({ format: timeFormat }),
    redactFormat(),
    splat(),
    redactFormat(),
    logFormat
  );
}

const fileOptions = {
  maxsize: 5 * 1024 * 1024,
  maxFiles: 5,
  tailable: true,
};

const logger = createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: buildFormat('YYYY-MM-DD HH:mm:ss'),
  transports: [
    new transports.File({
      ...fileOptions,
      filename: path.join('logs', 'error.log'),
      level: 'error',
    }),
    new transports.File({
      ...fileOptions,
      filename: path.join('logs', 'combined.log'),
    }),
  ],
});

for (const level of Object.keys(logger.levels)) {
  const write = logger[level].bind(logger);
  logger[level] = (message, ...values) => write(
    redactValue(message),
    ...values.map((value) => redactValue(value))
  );
}

logger.add(
  new transports.Console({
    format: buildFormat(
      process.env.NODE_ENV === 'production' ? 'YYYY-MM-DD HH:mm:ss' : 'HH:mm:ss',
      process.env.NODE_ENV !== 'production'
    ),
  })
);

// Keep the sanitizer reusable for the external-free runtime regression check.
logger.redactSensitive = redactValue;
logger.serializeLogValue = serializeLogValue;
logger.errorMetadata = errorMetadata;

module.exports = logger;
