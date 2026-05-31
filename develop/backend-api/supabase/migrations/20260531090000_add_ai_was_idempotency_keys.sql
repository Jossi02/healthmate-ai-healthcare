create table if not exists public.ai_was_idempotency_keys (
  idempotency_key text primary key,
  user_id uuid not null,
  operation text not null,
  request_hash text,
  status text not null default 'processing',
  status_code integer,
  response_body jsonb,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists idx_ai_was_idempotency_user_operation
  on public.ai_was_idempotency_keys (user_id, operation);

create index if not exists idx_ai_was_idempotency_status_updated
  on public.ai_was_idempotency_keys (status, updated_at);
