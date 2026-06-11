const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const supabase = require('../src/config/db');
const internalController = require('../src/controllers/internalController');

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return {};
  return fs
    .readFileSync(filePath, 'utf8')
    .split(/\r?\n/)
    .reduce((acc, line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) return acc;
      const [key, ...rest] = trimmed.split('=');
      acc[key.trim()] = rest.join('=').trim();
      return acc;
    }, {});
}

function hasValue(env, key) {
  return Boolean(String(env[key] || process.env[key] || '').trim());
}

function checkKeys(label, env, keys) {
  const missing = keys.filter((key) => !hasValue(env, key));
  return {
    label,
    ok: missing.length === 0,
    missing,
    present_count: keys.length - missing.length,
    total_count: keys.length,
  };
}

function readCurrentBranch() {
  try {
    return execSync('git branch --show-current', {
      cwd: path.join(__dirname, '..', '..', '..'),
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return null;
  }
}

async function checkIdempotencyTable() {
  const { error } = await supabase
    .from('ai_was_idempotency_keys')
    .select('idempotency_key')
    .limit(1);

  if (!error) return { ok: true };

  return {
    ok: false,
    code: error.code || null,
    message: error.message || String(error),
  };
}

async function main() {
  const repoRoot = path.join(__dirname, '..', '..', '..');
  const backendEnv = parseEnvFile(path.join(__dirname, '..', '.env'));
  const frontendEnv = parseEnvFile(path.join(repoRoot, 'develop', 'frontend-ui', '.env.local'));
  const aiEnv = parseEnvFile(path.join(repoRoot, 'develop', 'ai-model', 'v2', '.env'));
  const workflowPath = path.join(repoRoot, '.github', 'workflows', 'gcp-two-vm-deploy.yml');
  const workflowText = fs.existsSync(workflowPath) ? fs.readFileSync(workflowPath, 'utf8') : '';
  const migrationPath = path.join(
    __dirname,
    '..',
    'supabase',
    'migrations',
    '20260531090000_add_ai_was_idempotency_keys.sql'
  );

  const checks = [
    checkKeys('backend-env', backendEnv, [
      'SUPABASE_URL',
      'SUPABASE_SERVICE_ROLE_KEY',
      'FASTAPI_URL',
      'INTERNAL_API_KEY',
      'JWT_SECRET',
    ]),
    checkKeys('ai-env', aiEnv, [
      'WAS_BASE_URL',
      'INTERNAL_API_KEY',
      'GEMINI_API_KEY',
    ]),
    checkKeys('frontend-env', frontendEnv, ['NEXT_PUBLIC_BACKEND_URL']),
  ];

  const idempotencyTable = await checkIdempotencyTable();
  const requireDatabaseIdempotency = String(process.env.REQUIRE_IDEMPOTENCY_TABLE || '').toLowerCase() === 'true';
  const idempotencyFallbackReady = idempotencyTable.code === 'PGRST205' || idempotencyTable.code === '42P01';
  const idempotencyMode = idempotencyTable.ok ? 'database' : 'memory_fallback';
  const blockingIssues = [];
  const warnings = [];
  if (!fs.existsSync(migrationPath)) {
    blockingIssues.push('missing_idempotency_migration_file');
  }
  if (!workflowText.includes('test/all')) {
    blockingIssues.push('deploy_workflow_not_bound_to_test_all');
  }
  for (const check of checks) {
    if (!check.ok) {
      blockingIssues.push(`missing_${check.label}_keys`);
    }
  }
  if (!idempotencyTable.ok && (!idempotencyFallbackReady || requireDatabaseIdempotency)) {
    blockingIssues.push('idempotency_table_unavailable');
  }
  if (!idempotencyTable.ok && idempotencyFallbackReady) {
    warnings.push(
      'ai_was_idempotency_keys table is missing; demo can run with memory fallback, but duplicate protection resets on backend restart.'
    );
  }
  const result = {
    ok: blockingIssues.length === 0,
    current_branch: readCurrentBranch(),
    deploy_branch_configured: workflowText.includes('test/all'),
    migration_file_present: fs.existsSync(migrationPath),
    env_checks: checks,
    idempotency_table: idempotencyTable,
    idempotency_mode: idempotencyMode,
    idempotency_database_required: requireDatabaseIdempotency,
    idempotency_memory_fallback: internalController.__private?.getMemoryIdempotencyStatus?.() || null,
    blocking_issues: blockingIssues,
    warnings,
    idempotency_migration_hint: idempotencyTable.ok
      ? null
      : 'Apply develop/backend-api/supabase/migrations/20260531090000_add_ai_was_idempotency_keys.sql to Supabase, then rerun with REQUIRE_IDEMPOTENCY_TABLE=true.',
  };

  console.log(JSON.stringify(result, null, 2));
  if (!result.ok) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message }, null, 2));
  process.exitCode = 1;
});
