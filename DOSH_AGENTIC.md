# DOSH agentic surface

Status: design document, not implementation.

Verified current state, from checks run for this document: `webresearch/shell.py`
routes 22 commands over `Session`, the isolated control realm, `NetworkLedger`, and
the `ShellStore` workspace. `tests/test_shell.py` passes 73 cases. `shell.py`,
`telemetry.py`, and `test_shell.py` are untracked in git, which matches them being
recent work, not merged work.

Nothing in this document exists yet unless `DOSH_TERMINAL.md` already describes it.
An item becomes real when code lands in `webresearch/` with tests built from
captured markup, and the status table at the end moves. Reading this document is not
evidence of capability.

## What browsing means for an agent

A person browses pages and pixels. An agent browses arbitrary, uncooperative
programs and must commit effects under authority it does not fully control. Under
that framing the current surface is missing three primitives, not commands:

1. Situation: one compiled view of origin, document, controls, state, capability
   limits, and pending approvals, taken before action.
2. Authority: risk scoped permission, budgets, and human approval as first class
   browser objects rather than boolean arguments.
3. Evidence: receipts binding intent, dispatch, DOM delta, network observation, and
   a verified outcome into one addressable record.

More commands without these three only increase the number of silent clicks.

## Where the current surface stops

| Existing  | Works today                                                   | Still missing for an agent                                              |
|-----------|---------------------------------------------------------------|-------------------------------------------------------------------------|
| `actions` | full top level inventory with honest pagination coverage      | no preconditions, argument schema, risk class, or verification plan per action |
| `act`     | host gated dispatch, stale ref validation                     | no receipt, no idempotency key, no verify step; dispatch reads as success |
| `eval`    | bounded isolated realm                                        | expert lane only; no path to typed, reviewed automation                 |
| `skill.*` | pinned source, origin allowlist, version counter              | scripts lack preconditions, typed args, verification, recovery, drift checks |
| `net`, `console`, `storage`, `frames`, `layers` | bounded observations with disclosed limits | scattered views; the agent assembles the picture by hand; no cross source correlation |
| `snapshot`, `diff` | control evidence without DOM text                    | detects ref churn but cannot survive it; no identity across re-renders  |
| `tabs`    | list and switch                                               | tabs are transport details; goals, budgets, and states are unrepresented |

Two conclusions follow. The surface is honest today but routes mechanics, so the
missing primitive is the verified effect, not vocabulary. And several engine items
already named in `AGENTS.md` (element identity, mutation stream, causal trace,
replay substrate) are the foundation this design stands on, so shell work and
engine work must advance together.

## Design targets

Ordered by priority. Each target names proposed commands, data shape, and the test
that would prove it.

### P0: receipts and verification

Every mutating command returns a receipt, keyed and evidence linked:

```
act {ref, args, verification}
receipt {
  id, task_id, ref, risk, args_shape,
  dispatched, dom_delta, net_ids,
  verified: one of {confirmed, unverified, failed},
  verification_method, reversible, compensation_hint
}
receipt.get {id}, receipt.list {task_id}
```

Verification criteria are built into the browser, not guessed by the script:
`url_matches`, `text_visible`, `element_state`, `storage_key`, `cookie_name`,
`network_pattern`, `form_requires_user` (honest no probe). A mutating step ends as
`confirmed` only when a criterion evaluates on fresh document state. Tests: receipt
shape, criteria miss producing `unverified` without assertion drift, replay of a
captured page where the click target disappears before dispatch.

### P0: risk classes and approval

Actions carry one class, fixed by kind and effect:

| Class | Meaning | Default |
|-------|---------|---------|
| `observe` | read only, no document or server state change | auto |
| `navigate` | no committed form data | auto |
| `type` | local state change, no submission | explicit |
| `submit` | sends data to the origin | explicit |
| `external_effect` | email, payment, booking, posting, messaging | approval |
| `irreversible` | effect the engine cannot compensate | approval |

`policy.get`, `policy.set {risk_ceiling, allowed_origins, external_per_task}` and
`approval.request {receipt_preview}` let a human approve a step, not a session.
Form analysis supplies the class: a submit button inside a form posting outside
page scope is at minimum `submit`, and `external_effect` when the form targets an
email or payment flow. Tests: class assignment from captured forms, ceiling blocks,
approvals serialize and survive a session restart.

### P1: situation report

One call replaces assembled fragments:

```
observe {frame = ""}
{
  origin, title, http, document_meta,
  capability_manifest, sections, forms, tables,
  controls: {by_kind, by_risk, unavailable: cause},
  outstanding_approvals, recent_net_console,
  knowledge_hits, suggested_next: null
}
```

`capability_manifest` is first class here: every unavailable action with its cause
from `DOSH_TERMINAL.md` sources (engine gap, budget cap, missing secret, live
realm absent) rather than prose scattered across notes. Tests: observe on
`testhtml/test1.html` (205 links, 14 buttons, Nuxt) returns complete inventory with
coverage counts and per action causes for the omitted classes.

### P1: typed document view

The machine payload of most sites is forms and tables, not prose:

```
doc {what: forms|tables|outline|state}
forms → [{form_ref, fields: [{field_ref, name, type, required,
          visible, value_present, value_secret}], method, action_origin}]
tables → [{table_ref, columns, rows, cell_refs}]
```

Values stay metadata by default; `values=true` remains the explicit opt in with
key based redaction as today. P0 risk classes read from this structure: a form
field marked payment or email get raised class on submit. Tests: captured form
markup, captured table markup, required field detection, no value leak without
`values=true`.

### P2: tasks, not tabs

```
task.begin {goal, risk_ceiling}, task.status, task.abort, task.close
```

Work progresses under a task. Receipts, snapshots, ledger filtering, and console
queries join on `task_id`. Navigation inside a task is an implementation detail of
the goal, never the end in itself. `policy.set external_per_task` gives a hard cap
per goal. Tests: create, compute receipts, abort, close; blocked by ceiling mid
task; task isolation between two tasks on one session.

### P2: host vault for credentials

The model never sees passwords. The vault holds them, the browser types them:

```
account.list                    metadata only
account.login {account_alias}   uses the login flow, records receipt
account.totp {account_alias}    one pass fill, never photo in output
account.revoke {account_alias}
```

Current `browser_login` and OTP paths run by alias with secrets held in memory
only, never in arguments, results, console, or ledger. Tests: fake origin with
fixture form, no output contains the secret or the typed values, wrong credentials
produce an honest failed receipt.

### P3: procedures replace raw skill scripts

`skill.*` today is pinned JavaScript plus an origin allowlist. Procedures make
that data:

```
procedure {
  name, version, sha256, origins,
  preconditions: [{url_pattern, required_fact}],
  args_schema, steps: [{act, args, verification}],
  recovery: [{on: stale_ref|verification_failed, do}],
  secrets: [account_alias], tested_at, drift_status
}
procedure.run {name, args, allow_steps}
procedure.test {name, args, from_fixture}
procedure.drift {name}
```

Dry run steps against a recorded fixture page, never the live site. Drift checks
current page facts against preconditions and reports incompatibility. The engine
root: generators or model produced steps run only after a human approves the
procedure and it passes tests, never in the default path. Tests: fixture based dry
run, drift detection on changed captured markup, refusal on unapproved procedures
by default.

### P3: evidence correlation and change streams

Already on the engine list in `AGENTS.md` and required by the receipts above:

- `trace {receipt_id}`: the causal chain for one action: DOM delta, requests caused,
  console entries, in one correlated view
- `mutation.subscribe`: deltas pushed during script evaluation instead of re-reading
- multi realm eval as an expert lane, gated like eval

### P4: replay and deterministic testing

Snapshots as evidence, receipts as the ledger, and seeded control realm turns into
a replay system: rerun a detected code mismatch or a mistaken action from records,
not from live site reuse. Engine requirement: unified SQL state database from
`AGENTS.md`. Tests: replay reproduces the receipt chain for one captured session
without network.

## Further construction, shell level

Beyond the priorities above:

- Dry run mode for every mutating command: full validation, zero dispatch
- Idempotency keys persisted with receipts, so safe retries carry the same key
- Compensation library keyed by control kind and form semantics: clear fields,
  close dialogs, cancel step. Where no compensation exists, say so against a
  receipt rather than faking an undo
- Structured data lane: JSON-LD, repeated patterns, and schema like records as a
  first class reading projection next to `content.py`
- Role and landmark projection: landmarks, roles, and names resolved from real
  accessibility attributes, with recipe applied openly rather than hidden
- Per origin site memory: receipts and observations collect the facts of what
  worked; they expire and need re-verification before reuse
- Per origin procedure drafts: repeated successful sequences can be returned by
  potential procedures for review, never run by generators without approval
- Interruption report: wall, challenge, consent gate, and paywall detected under a
  plan causes explicit state change with the cause named, never silent flow slide
- Politeness envelope: same origin request pacing, retry backoff honoring server
  signals, ledger recorded so audits show pacing was real
- Keyboard and scroll lanes exposed at shell level for named keys already in the
  engine, plus focus moves, without coordinate math
- Downloads and uploads at shell level: `act` accepts file payloads today through
  the host path; shell command surface should reconnect that flow
- Cookie and history surfaces: cookie metadata with values gated, history list
  with stepwise traversal of pushState entries
- Watch lanes: per origin diffing over time where price, availability, or content
  state changes against stored evidence
- Handoff protocol: a task transfers between sessions with a chain of receipts and
  procedure state, never VM state

## Further construction, engine level

Required substrate, from `AGENTS.md` status and roadmap:

- Element identity tracker: stable references surviving re-renders, resolving ref
  churn after any action
- Mutation subscription stream: deltas at the engine, consumed by the projection
- Causal trace completion: action, delta, network, console in one stream, not four
- Unified SQL state database with snapshots and rollback points for workspace state
- Deterministic replay substrate: ledger, snapshots, and seeds
- Scoped WASM interpreter for real challenge module opcodes, with hermetic tests
- WebSocket binary frame decode, today counted and skipped
- Real rAF frame clock replacing the setTimeout shim
- CSS transitions and keyframes timing engine
- Sticky, flex-wrap distribution, floats, then bidirectional text

## Hard invariants

Held regardless of what this surface grows:

1. No external browser, no CDP, no browser binary lane. A capability gap is a work
   item in `minidom.js`, `runtime.py`, `parse.py`, `session.py`, never a bypass.
2. Reading filters never gate the action inventory. Controls are never silently
   dropped and coverage is always reported.
3. Dispatch is never reported as outcome. Every mutating path ends in evidence or
   an honest `unverified`.
4. Secrets live in `.env` or the vault, in memory only, and stay out of source,
   tests, and result fields.
5. The control realm stays isolated, synchronous, and bounded. It is a projection
   and an action queue, not an evaluation of untrusted code against the page.
6. No operating system shell semantics. This is a terminal over owned browser
   sessions.

## Non goals

- Presenting the control realm or this surface as a security boundary. It is not
  one today and the isolation is bounded, not proven.
- Using eval as a default path or making promise driven control realm turns.
- Closing tabs or destroying session state that receipts and snapshots reference
  before they are exported.
- Auto running generated automation steps without human approval and passing tests.

## Status

All rows other than shipped work are open. Nothing here is complete until code and
tests exist.

| Target       | State          |
|--------------|----------------|
| receipts     | open           |
| verification | open           |
| risk classes | open           |
| approvals    | open           |
| situation    | open           |
| doc view     | open           |
| tasks        | open           |
| vault        | open           |
| procedures   | open           |
| traces       | open           |
| replay       | open           |
| suggestions  | open           |

## Where the code goes

`shell.py` stays the router. New modules hold the new behavior so the command
surface stays thin and tests stay reachable:

```
webresearch/agent/policy.py      risk classes, ceilings, approvals
webresearch/agent/receipts.py    receipt store and criteria evaluation
webresearch/agent/situation.py   observe projection
webresearch/agent/document.py    forms, tables, state view
webresearch/agent/tasks.py       task lifecycle
webresearch/agent/accounts.py    vault integration, alias only surface
webresearch/agent/procedures.py  skill upgrade path
```

Each module needs tests from captured markup first, per repo verification rules.
Hand written fixtures stay second to `testhtml/test1.html` style captures.
