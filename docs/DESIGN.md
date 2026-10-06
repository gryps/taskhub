# TaskHub V2 Design System

## Direction

TaskHub is an operations console for technical owners and operators: calm, precise, dense enough for
daily supervision, and explicit about state and recovery. Neutral surfaces carry most information;
green identifies primary actions and active context, while semantic colors are reserved for status.
Motion is brief and functional.

The detailed incumbent contract remains `docs/frontend-design.md`. This file is the canonical summary
required by the engineering baseline; changes to durable tokens, interaction patterns, or responsive
behavior must update both until the older document is consolidated.

## Interface principles

- Organize navigation around stable user goals: tasks, development flow, and system configuration.
- Give each surface one primary purpose and keep current project/run context visible.
- Present first use as two numbered phases: global Seed infrastructure first, then project activation
  after repository attachment. Never label a project-specific Windows node as a global prerequisite.
- Prefer compact tables for repeated comparison, cards for distinct objects, and timelines for process
  history. Do not convert all information into cards or dialogs.
- Render server-owned facts; expose loading, empty, error, permission, stale, partial-success, and
  long-running states where applicable.
- Development-agent pairing belongs under access security: show the device label, confirmation
  code, granted role, expiry, last use and revocation action, but never render the Bearer credential.
- Preserve API, route, SSE, permissions, stable DOM hooks, and keyboard behavior during visual work.

## Tokens and visual rules

- System sans-serif; Chinese prefers `PingFang SC` / `Microsoft YaHei`.
- Type scale: page title 24px, section title 16–18px, body 14px, auxiliary text at least 12px unless it
  is purely decorative.
- Spacing follows 4px increments; common values are 8, 12, 16, 24, and 32px.
- Cards use 12px radii and borders before shadows. Shadows are limited to global/floating hierarchy.
- Semantic roles include canvas, surface, subtle surface, primary/secondary text, border, accent,
  focus, success, warning, danger, info, disabled, overlay, and selection.
- Tertiary text uses `#5f6f69` and form placeholders use `#64716d` on white/light surfaces; their
  verified minimum contrast is 4.93:1. Functional interface text must not be smaller than 11px.
- Text and controls target WCAG 2.2 AA. Functional touch targets are at least 44px in touch contexts,
  and touch inputs use at least 16px text.

## Responsive and motion rules

- Desktop authority: 1280–1600px; mandatory verification near 1440px.
- Intermediate verification: 768px; project compatibility coverage may additionally retain 680px.
- Phone verification: 390px. Tables may scroll within their own container, but the page must not.
- Narrow layouts recompose controls and information; they are not merely compressed desktop layouts.
- Motion explains feedback, hierarchy, or direction in 150–200ms and respects
  `prefers-reduced-motion`.

## Completion definition

Frontend work is complete only after type/lint/build and focused tests pass, the three required
viewports are rendered, keyboard focus and overflow are checked, representative states are exercised,
and this design contract is synchronized with implementation.
