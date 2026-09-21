-- AI Bridge Supabase primary realtime transport schema.
-- Historical filename retained for Runtime 0.2.6.x compatibility.
-- Apply to a dedicated Supabase project.

create table if not exists public.ai_bridge_commands (
    command_id text primary key,
    bridge_id text not null,
    envelope jsonb not null,
    state text not null default 'queued'
        check (state in (
            'queued', 'accepted', 'success', 'failed',
            'denied', 'conflict', 'unknown', 'rejected'
        )),
    result jsonb,
    error jsonb,
    created_at timestamptz not null default now(),
    accepted_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz not null default now()
);

create index if not exists ai_bridge_commands_bridge_state_created_idx
    on public.ai_bridge_commands (bridge_id, state, created_at);

alter table public.ai_bridge_commands enable row level security;

revoke all on table public.ai_bridge_commands from anon, authenticated;
grant select, insert, update on table public.ai_bridge_commands to service_role;

comment on table public.ai_bridge_commands is
    'AI Bridge primary realtime command/result transport; GitHub V5 is fallback and GitHub remains durable authority.';
