-- Expense Policy Agent: database schema (Postgres 15+ with pgvector).
-- Safe to run more than once.

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- Policy chunks: one row per policy section, searched by the RAG step.
-- 1536 dimensions matches OpenAI text-embedding-3-small. With ~20 rows an
-- exact scan is fast, so there is deliberately no vector index.
-- ---------------------------------------------------------------------------
create table if not exists policy_chunks (
    section_id      text primary key,            -- e.g. '§4.2'
    chapter         text not null,               -- e.g. '4. Meals and Entertainment'
    title           text not null,               -- e.g. 'Client Entertainment'
    content         text not null,               -- full section text, cited verbatim
    policy_version  text not null,
    embedding       vector(1536)
);

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------
create table if not exists roles (
    role            text primary key,
    max_approval    numeric(10, 2) not null      -- policy §2.1
);

-- Monthly budget per employee by category (policy §8.1).
-- 'other' has no budget on purpose: the policy is silent, so the agent should flag.
create table if not exists budget_categories (
    category              text primary key,
    default_monthly_limit numeric(10, 2)
);

-- ---------------------------------------------------------------------------
-- Employees and expense history (what the agent's tools query)
-- ---------------------------------------------------------------------------
create table if not exists employees (
    employee_id     text primary key,
    name            text not null,
    role            text not null references roles (role),
    department      text not null,
    manager_id      text references employees (employee_id),
    active          boolean not null default true
);

create table if not exists expenses (
    expense_id         text primary key,
    employee_id        text not null references employees (employee_id),
    transaction_date   date not null,
    submitted_date     date not null,
    vendor             text not null,
    amount             numeric(10, 2) not null check (amount >= 0),
    category           text not null references budget_categories (category),
    status             text not null check (status in ('pending', 'approved', 'rejected', 'paid')),
    approver_id        text references employees (employee_id),
    business_purpose   text,
    attendee_count     integer check (attendee_count is null or attendee_count > 0),
    has_external_attendee boolean,
    receipt_itemized   boolean not null default false
);

-- find_duplicates (policy §2.3) and budget lookups (§8.1)
create index if not exists expenses_dup_idx
    on expenses (employee_id, vendor, amount, transaction_date);
create index if not exists expenses_budget_idx
    on expenses (employee_id, category, transaction_date);

-- ---------------------------------------------------------------------------
-- Audit log: written by record_decision, the agent's only write.
-- ---------------------------------------------------------------------------
create table if not exists decisions (
    decision_id        uuid primary key default gen_random_uuid(),
    request_id         text not null,
    employee_id        text not null references employees (employee_id),
    amount             numeric(10, 2) not null,
    category           text not null,
    model_decision     text not null check (model_decision in ('approve', 'flag', 'reject')),
    final_decision     text not null check (final_decision in ('approve', 'flag', 'reject')),
    guard_overruled    boolean not null default false,   -- the guard changed the model's call
    guard_reason       text,
    policy_ids         text[] not null default '{}',
    reason             text not null,
    langsmith_run_id   text,
    created_at         timestamptz not null default now(),
    -- What the agent saw, kept so a judge can grade the decision later.
    request            jsonb,
    retrieved_ids      text[] not null default '{}',
    tool_results       jsonb,
    prompt_commit      text,
    model              text
);

-- For databases created before these columns existed.
alter table decisions
    add column if not exists request       jsonb,
    add column if not exists retrieved_ids text[] not null default '{}',
    add column if not exists tool_results  jsonb,
    add column if not exists prompt_commit text,
    add column if not exists model         text,
    add column if not exists trace_url     text;

create index if not exists decisions_request_idx on decisions (request_id);

-- ---------------------------------------------------------------------------
-- Judge scores: one row per graded decision, written after the LLM judge runs.
-- ---------------------------------------------------------------------------
create table if not exists judgements (
    judgement_id       uuid primary key default gen_random_uuid(),
    decision_id        uuid not null references decisions (decision_id) on delete cascade,
    grounded           integer not null check (grounded between 1 and 5),
    complete           integer not null check (complete between 1 and 5),
    clear              integer not null check (clear between 1 and 5),
    safe               integer not null check (safe between 1 and 5),
    evidence           jsonb not null default '{}',
    judge_model        text not null,
    judge_prompt       text not null,
    created_at         timestamptz not null default now()
);

create index if not exists judgements_decision_idx on judgements (decision_id);

-- ---------------------------------------------------------------------------
-- Seed the reference data that comes straight from the policy.
-- ---------------------------------------------------------------------------
insert into roles (role, max_approval) values
    ('employee', 0),
    ('manager', 500),
    ('director', 2000),
    ('vp', 10000)
on conflict (role) do update set max_approval = excluded.max_approval;

insert into budget_categories (category, default_monthly_limit) values
    ('meals', 300),
    ('travel', 2000),
    ('software', 500),
    ('hardware', 1000),
    ('training', 250),
    ('office_supplies', 200),
    ('client_entertainment', 1000),
    ('team_events', 300),
    ('other', null)
on conflict (category) do update set default_monthly_limit = excluded.default_monthly_limit;

-- ---------------------------------------------------------------------------
-- Lock the tables down. The agent connects directly as the database owner,
-- which bypasses RLS; nothing is exposed through the Data API.
-- ---------------------------------------------------------------------------
alter table policy_chunks      enable row level security;
alter table roles              enable row level security;
alter table budget_categories  enable row level security;
alter table employees          enable row level security;
alter table expenses           enable row level security;
alter table decisions          enable row level security;
alter table judgements         enable row level security;
