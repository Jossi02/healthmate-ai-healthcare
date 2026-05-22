create extension if not exists pgcrypto;

create table if not exists public.chat_threads (
  user_id text not null,
  session_id text not null,
  title text not null default '새 대화',
  message_count integer not null default 0 check (message_count >= 0),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  last_message_at timestamptz not null default timezone('utc', now()),
  primary key (user_id, session_id)
);

create table if not exists public.chat_messages (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  session_id text not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  client_message_id text,
  intent text,
  role_order smallint not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists chat_threads_user_last_message_idx
  on public.chat_threads (user_id, last_message_at desc);

create index if not exists chat_messages_user_session_created_idx
  on public.chat_messages (user_id, session_id, created_at asc, role_order asc);

create index if not exists chat_messages_client_message_idx
  on public.chat_messages (user_id, client_message_id)
  where client_message_id is not null;
