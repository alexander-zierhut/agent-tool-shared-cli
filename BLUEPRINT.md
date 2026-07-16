# Blueprint: `agent-tool-<x>-cli`

The internal engineering standard for agent-ready CLIs. Distilled from `agent-tool-openproject-cli` v0.4.0 (5189 LOC in `src/opcli/` — 2323 of that the chassis, the rest `commands/`; 233 test functions collecting as 247 cases across 24 files; live-tested against OpenProject 13–17), corrected by a live spike against a real gitea + drone + drone-runner stack. Drone is tool #2; Jira, Nextcloud and GitLab follow.

Read once, then work §7 as a checklist.

| Tag | Meaning |
|---|---|
| **VERBATIM** | Copy the file, change strings only. If you are redesigning it, you are wrong — or you found a v3 improvement, in which case write it down here. |
| **PARAMETERIZED** | The shape is fixed; the content is per-tool. Copy the skeleton, replace the body. |
| **FRESH** | Written per tool. This gives the *rules* it must obey, not the code. |

`opcli` is the source of every pattern here and it is not clean. A dozen of its decisions are scar tissue, collected in §8. **Copy the lessons, not the bugs.**

Claims are tagged **[live]** (observed against a running server) or **[src]** (read in the vendor's source) where the distinction matters. Untagged claims about our own code were checked against the tree while writing.

---

## 1. What "agent-ready" means

An agent-ready CLI is one where **an LLM with no prior knowledge of the tool, no documentation and no human in the loop can discover what it can do, do it correctly, and know whether it worked** — cheaply, and without ever being asked a question it cannot answer.

Seven contracts. Each is load-bearing. Break one and the tool degrades from "an agent can drive this" to "an agent can sometimes drive this, and will confidently lie about the rest."

### 1.1 stdout is a machine channel, and nothing else touches it

JSON on stdout by default. Errors as JSON on stderr. Prompts on stderr. Human status lines suppressed in machine formats. `highlight=False` on the Rich console, or it injects ANSI into a JSON string it thinks looks like a URL.

The test is one pipeline: `<tool> <anything> | jq` must never break. Not when config is missing, not on a first run, not when a prompt fires, not when a column accessor raises.

```python
_err_console = Console(stderr=True)                          # errors
self.console = Console(no_color=not color, highlight=False)  # payload

def message(self, text: str) -> None:
    if self.fmt == OutputFormat.table:   # ALLOWLIST. opcli ships `!= json`, which
        self.console.print(text)         # corrupts csv and markdown. See §8.
```

**If any command emits non-JSON — log text is the usual one — the output contract must say so on the guide's first screen**, or an agent `json.loads` a log dump and crashes. OpenProject never needed this carve-out; every CI tool does.

### 1.2 Exit codes are the error taxonomy

An agent must branch on *what kind* of failure occurred without parsing prose. The exit code carries the class; the JSON body carries the detail. This table is **published API** in three places (README, in-binary `guide`, `SKILL.md`): you may leave a code unallocated, you may repurpose one deliberately, you may **never renumber one**.

| Code | Class | Cause |
|---|---|---|
| 0 | — | success (including a successful `--dry-run`) |
| 1 | `OpError` | generic, expected failure |
| 2 | *(Click/Typer)* | usage error. Never allocate. `guide` raises `typer.Exit(code=2)` for an unknown topic — deliberately, not an `OpError`. |
| 3 | `ConfigError` | no profile, malformed config file |
| 4 | `AuthError` | 401 / 403 |
| 5 | `NotFoundError` | 404 |
| 6 | `ConflictError` | 409 / optimistic-locking conflict. **Per-tool decision** — §7.1. |
| 7 | `ValidationError` | 422, or 400 where the API has no 422 |
| 8+ | *(per-tool)* | only for a condition you have **observed**. |
| 130 | — | SIGINT |

`errors.py` is 93 lines and copies character for character: `OpError` with `.message`/`.detail`/`to_dict()`, then one-line subclasses setting `exit_code`. Two details carry their weight:

- **`ValidationError.field_errors` is the highest-value line in the file.** A 422 as an opaque blob ends the agent's turn; the same 422 as `fieldErrors: ["Subject can't be blank"]` gets fixed on the next call.
- **`DryRun` lives here and deliberately does not inherit `OpError`**, so the funnel doesn't catch it and a dry run exits 0.

**Never invent an exit code for a condition you have not seen.** The Drone drafts allocated exit 8 for HTTP 501, believing `drone/drone:2` was an `-tags oss` build with a large 501 surface. Live, nothing 501s: repo secrets, org secrets, crons, templates, promote and admin users all returned 200 **[live]**, and the vendor's build scripts publish an *untagged* binary — the oss-tagged build is compiled to `/dev/null` as a check and never shipped **[src]**. An exit code, a "mandatory" capability probe, a shell script and a pytest marker were designed for a configuration that never existed. If a build *could* compile features out, a `doctor` command that probes and reports beats an exit code that encodes the fear. Verify, then allocate.

**Never leak observed status into the exit code.** `build wait` exiting non-zero when the build goes red conflates *the CLI failed* with *the thing the CLI observed failed*. Put the outcome in the JSON. If gating is genuinely wanted, put it behind an explicit `--exit-code`, documented as overriding the contract, in a band that cannot collide with the error band (20–29).

### 1.3 The tool teaches itself, and prefers discovery to facts

Two mechanisms, and they are different things.

**Self-documentation** — `<tool> guide` is a zero-auth, zero-config, offline manual compiled into the binary. It is the first command a context-free agent runs, so it must be *structurally impossible* for it to fail with exit 3 or 4, or to block on a prompt.

**Discovery** — wherever the instance is the source of truth (custom fields, statuses, filterable fields, allowed values), the docs point at a **live introspection command** instead of embedding a list. `search fields` / `search operators` / `search values <field>` are the anti-hallucination surface. An embedded field list is wrong on someone's server on day one, and an LLM that read it emits invalid filter JSON with total confidence.

Corollary: **for every domain, ship `<thing> fields` and `<thing> values` before you write the guide section that would otherwise list them.** Where the API has no schema endpoint (Drone), the static registry becomes the *only* discoverability source — that does not excuse you from the trio; it means the trio reads the registry and the registry needs guard tests (§4).

### 1.4 Preview before you write

`--dry-run` is implemented **once, in the transport layer, as a control-flow exception**. Never a per-command `if dry_run:` branch — that rots the instant someone adds a command and forgets.

```python
# client.py, before any header or transport work
if self.dry_run and method in _WRITE_METHODS:
    raise DryRun({"method": method, "url": url, "params": clean_params or None,
                  "body": json if json is not None else ("<multipart upload>" if files else None)})
```

Four decisions in four lines: **one chokepoint** (every write command, present and future, gets it free and cannot bypass it); **reads still execute** (name→id resolution and lock-version fetches must actually hit the server or the printed request is a guess with `href: null` in it — dry-run means *no writes*, not *no network*); **an exception, not a return value** (it unwinds through arbitrarily deep command code with no sentinel checks); **it exits 0** (a dry run succeeded).

Its value scales with blast radius. For OpenProject it previews a ticket edit; for Drone, `build promote` ships code and `repo rm` is irreversible.

### 1.5 Token thrift

Context window is the agent's budget and the tool spends it. Four affordances, all in the output layer, all free once built:

- `--fields id,subject,assignee.name` — dotted-path projection, flat output keys.
- `--count` — ask for one row, read the authoritative total; never fetch N rows to learn N. Echo the compiled query back beside the total: it makes an opaque number checkable and doubles as the query-language debugger.
- `--stream` — NDJSON, one object per line, flushed, generator-lazy from the page loop to stdout.
- List paths pass `include_description=False` to the serializer. Unbounded prose must not dominate a 50-row listing.

**`--count` is not portable** — it depends on an authoritative total. Without one, counting client-side within a page budget is a lie unless you also report truncation (§6, Pagination).

### 1.6 No hidden state without an escape hatch

Sticky context (`context set --project webshop`) is genuinely useful and genuinely dangerous: implicit state that silently changes results. Permitted only with all four of:

1. **Inspectable** — `context show` emits the active map *and* `configPath`.
2. **Overridable** — an explicit flag always wins (free from Click; §3.6).
3. **Bypassable** — `--no-context`.
4. **Self-documented as a hazard** — `AGENTS.md` and `guide context` both carry, verbatim: *"context is IMPLICIT state that changes command behaviour. If output looks wrongly scoped, run `context show` or add `--no-context`. Don't assume a fresh environment is context-free."*

### 1.7 Refuse rather than guess

**Ambiguity is an error.** `_match` returns a substring hit only if exactly one element matched. `--assignee ali` matching both `alice` and `alistair` errors out — a wrong-but-plausible assignment costs an agent far more than an error it can recover from by being more specific. Stakes scale with the domain: `--repo api` matching both `api` and `api-gateway` triggers CI on the wrong repository.

**Failure enumerates the alternatives.** The single biggest ergonomics win in the codebase:

```python
def _resolve_collection(client, collection, ref, fields, *, label, params=None) -> Json:
    if _is_id(ref):
        return client.get(f"{collection}/{ref}")           # cheap path: no listing
    elements = client.collect(collection, params=params, page_size=200)
    el = _match(elements, str(ref), fields)
    if el is None:
        names = ", ".join(sorted(str(e.get(fields[0])) for e in elements)[:25])
        raise NotFoundError(f"no {label} matching '{ref}'. Available: {names}")
    return el
```

`no status matching 'Done'. Available: Closed, In progress, New, On hold, Rejected` turns a dead end into a self-correcting turn. Sort for scannability; cap at 25 so a 500-project instance cannot flood the context window.

**An unanswerable question must not return empty.** `search values subject` returning `[]` is a lie: empty reads as *"no valid values exist"*, so the agent concludes the field is unusable. Raise, with the remedy in the message.

### 1.8 What agent-ready is *not*

- Not "has a `--json` flag." JSON is the default; `table` is the accommodation for humans.
- Not "has good docs." The docs ship inside the binary and point at live introspection.
- Not "never fails." It is "fails in exactly one machine-readable way, with the remedy in the message."
- Not "does everything." It is "the escape hatch is always one rung down" (`--where` → `--filters` → `raw`), so the curated layer stays small and opinionated.

---

## 2. Architecture

### 2.1 Three names, three collisions

| | opcli | pattern | why |
|---|---|---|---|
| Distribution | `agent-tool-openproject-cli` | `agent-tool-<x>-cli` | namespaced, unsquattable, self-describing, searchable |
| Import package | `opcli` | `<x>cli` | short, private, never typed by a user |
| Command | `openproject` | the unambiguous word | what lands on `PATH` |

**The refusal is the pattern.** Declare exactly one console script, and explain the name you did *not* take:

```toml
[project.scripts]
# Only `openproject` — the short name `op` is intentionally NOT claimed, to avoid colliding
# with other tools that use it. Add your own alias if you want a shorter command.
openproject = "opcli.cli:main"
```

The dev machine literally had another tool at `/usr/local/bin/op`. `pipx install` gives no warning when it wins a `PATH` fight. Cost of the long name: a user-side alias. Cost of the collision: you broke someone's unrelated workflow at install time.

**Drone raises the stakes — refuse the obvious name too.** `drone/drone-cli` already ships `drone` and most Drone users have it. Take `dronectl` (`*ctl` is legible and uncontested; `drone-agent` is worse than useless — "agent" already means the runner in Drone's vocabulary). Because dist name and command differ, the package description must reconcile them: end it with the literal sentence *"Installs the `<command>` command."*

### 2.2 Layout

```
agent-tool-<x>-cli/
├── pyproject.toml                  PARAMETERIZED  dynamic = ["version"] — §8
├── Makefile                        PARAMETERIZED  help/install/up/wait/token/seed/env/test/test-unit/down/clean
├── README.md · AGENTS.md           PARAMETERIZED  fixed skeleton, new terms
├── docker-compose.yml              FRESH
├── docker-compose.versiontest.yml  PARAMETERIZED  ${X_IMAGE}/${X_PORT}
├── rates.example.json              FRESH          the killer feature's config template, if it has one
├── packaging/<x>_launcher.py       VERBATIM       11 lines; §5.3
├── scripts/
│   ├── build_binary.py             VERBATIM       was .sh — §8
│   ├── gen_docs.py                 VERBATIM       2 strings change
│   └── seed_test_data.*            FRESH
├── docs/
│   ├── USAGE.md                    FRESH
│   ├── COMMANDS.md                 GENERATED      never hand-edit
│   └── API_NOTES.md                FRESH          fixed skeleton — §5.4
├── src/<x>cli/
│   ├── __init__.py                 VERBATIM       __version__ — the single source
│   ├── __main__.py                 VERBATIM       from .cli import main
│   ├── paths.py                    VERBATIM       ★ NEW in v2 — §8
│   ├── errors.py                   VERBATIM       93 LOC
│   ├── output.py                   VERBATIM       283 LOC
│   ├── duration.py                 PARAMETERIZED  ★ the wire↔human time-unit adapter
│   ├── hal.py                      PARAMETERIZED  ★ the wire-format adapter seam — or DELETE
│   ├── credentials.py              VERBATIM
│   ├── config.py                   PARAMETERIZED
│   ├── appctx.py                   VERBATIM       ★ RENAMED from context.py — §8
│   ├── client.py                   PARAMETERIZED
│   ├── cli.py                      PARAMETERIZED
│   ├── serialize.py                FRESH          but ALWAYS present
│   ├── resolve.py                  PARAMETERIZED  shape verbatim, resolvers fresh
│   ├── <domain>spec.py             PARAMETERIZED  registry / operators / dates / where-parser
│   ├── <domain>filters.py          FRESH
│   └── commands/
│       ├── _shared.py              SPLIT          generic helpers verbatim; wire helpers discarded
│       ├── auth.py                 PARAMETERIZED
│       ├── settings.py             VERBATIM
│       ├── context.py              VERBATIM       KNOWN_KEYS is fresh
│       ├── guide.py                PARAMETERIZED  structure verbatim, prose fresh
│       ├── install.py              VERBATIM       SKILL_MD is fresh
│       ├── raw.py                  PARAMETERIZED
│       └── <domain>*.py            FRESH
└── tests/
    ├── conftest.py                 VERBATIM       names change
    ├── support.py                  PARAMETERIZED  FakeClient verbatim, SAMPLE_* fresh
    ├── test_client_retry.py        VERBATIM       minus the locking arm
    ├── test_<chassis>_unit.py      VERBATIM
    └── test_<domain>*.py           FRESH
```

**Reusability ledger.**

| Layer | Verbatim | Parameterized | Fresh |
|---|---|---|---|
| Chassis (`paths`, `errors`, `output`, `appctx`, `credentials`) | ~100% | | |
| Wire adapters (`hal`, `duration`) | | both — or delete `hal` | |
| Transport (`client`) | retry, backoff, dry-run, URL norm, error funnel | auth, api_root, body parser, pagination | |
| CLI wiring (`cli`) | `_pop_globals`, `main()`, `_context_default_map`, `_maybe_offer_claude`, Typer hygiene flags | help text, group registration, app name | |
| Meta commands | `settings`, `context`, `install` (~95%) | `guide`, `auth`, `raw` | `KNOWN_KEYS`, `SKILL_MD`, guide prose |
| Domain (`serialize`, `resolve`, `<x>spec`, `commands/*`) | | resolver shape, registry shape | all content |
| Tests | `conftest`, `test_client_retry`, chassis units, the pty test | `support.py` | domain tests |
| CI/CD | `release.yml`, Trusted Publishing, PyInstaller flags, `gen_docs.py` | `ci.yml`, `compat.yml`, `Makefile` | seed scripts |

**60–70% of the code is chassis.** Port cost concentrates in `serialize.py`, `<x>spec.py`, the command modules and the live-integration harness.

**src-layout is not cosmetic.** `where = ["src"]` means tests cannot accidentally import the working tree instead of the installed package — which is what makes "install the wheel into a clean venv and run it" meaningful.

### 2.3 The layering rule

Six tiers. **A module may import only from strictly lower tiers.** No cycles.

```
0  paths.py   errors.py   duration.py          stdlib only — import NOTHING from the package
1  credentials.py (paths)   hal.py (nothing)   output.py (errors, lazily)
2  config.py (paths, errors)                   serialize.py (hal, duration)
3  client.py (errors, hal)                     appctx.py (config, credentials, output, client)
4  resolve.py · <x>spec.py · <x>filters.py     (client, errors)
5  commands/_shared.py  →  commands/*.py       (appctx, serialize, resolve, …)
6  cli.py                                      (imports commands at the BOTTOM)
```

**The seam that matters: `output.py` must never import `serialize` or `hal`.** It only ever sees plain dicts and lists. That single rule is what makes 283 lines of rendering chassis portable with a zero-line diff. If you are tempted to teach the emitter about your wire format, you have found the bug.

Two consequences, both needing a comment naming the reason — someone *will* tidy them: command groups are imported at the **bottom** of `cli.py` with `# noqa: E402` (`cli → commands.X → cli` is a cycle), and `print_error` imports `OpError` *inside the function* (`errors ↔ output` is the same cycle).

### 2.4 The modules that need explaining

**`paths.py`** — VERBATIM, new in v2. A stdlib-only leaf owning the env namespace and config location. It exists to kill opcli's one honest wart: `credentials.py` re-implements `config_dir()` verbatim to stay a leaf, so two identical six-line functions must be kept in sync forever.

```python
ENV_PREFIX, KEYRING_SERVICE, APP_DIR = "DRONECLI", "drone-cli", "drone-cli"   # per-tool; §7.1
def env(name: str) -> str:  return f"{ENV_PREFIX}_{name}"

def config_dir() -> Path:
    base = os.environ.get(env("CONFIG_DIR"))   # escape hatch: tests + CI
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CONFIG_HOME")    # be a good XDG citizen
    return (Path(xdg) if xdg else Path.home() / ".config") / APP_DIR
```

**`config_dir()` is a function, not a module-level constant — the most load-bearing structural decision in the config area.** Re-reading `os.environ` on every call is what makes `monkeypatch.setenv("<X>_CONFIG_DIR", tmp_path)` work from a fixture (the module was already imported by then). As an import-time constant the env var is a silent no-op and the unit suite reads *and clobbers* the developer's real `~/.config`. Every isolation fixture leans on this; there are zero module-level `Path` constants in `src/`. Keep it that way.

**`hal.py`** — PARAMETERIZED, **or deleted**. The wire-format adapter: everything that knows `_links`, `_embedded`, hrefs, `{"raw": …}` Formattables. 75 LOC. Against a flat-JSON API it is the largest single deletion in the port — but **the seam survives the deletion.** Name the module even if thin, so the wire format has exactly one home and `output.py` never learns it.

**`duration.py`** — PARAMETERIZED, 56 LOC, and it generalizes further than it looks: **the wire's representation of time is never what a human types.** OpenProject stores `PT2H30M` and rejects `2.5`; Drone stores epoch seconds and has *no duration field anywhere*; Jira uses ISO durations; GitLab uses `time_stats` seconds. Every tool needs this module and every tool needs a different body. Its output belongs in `serialize.py`, never in a renderer.

**`serialize.py`** — FRESH, but **always present**. One pure `doc -> dict` per resource, hand-listing output keys. Keep it even against flat JSON: it pins the CLI's output contract independently of the server's, gives `--fields` a stable vocabulary, and renames the wire's ugly names into consistent CLI names. It becomes projection + renaming instead of flattening — and it is where **derived fields** live (`duration_seconds` = `finished - started` is the question a CI CLI exists to answer and is held in no single field).

**`client.py`** — PARAMETERIZED. Auth scheme, `api_root`, error-body parser and pagination are per-tool; retry matrix, backoff, URL normalization, dry-run interception and the error→exception funnel are verbatim.

**`commands/_shared.py`** — SPLIT: generic helpers (`ctx_obj`, `parse_json_option`) copy verbatim; wire-format helpers (`set_link`, `apply_custom_fields`) are discarded with `hal.py`. `parse_json_option` is not a convenience wrapper but contract enforcement — it converts `JSONDecodeError` to `OpError` at the boundary, because a bare `JSONDecodeError` reaching `main()` bypasses the central handler and prints a traceback.

**Command modules** — FRESH, uniform shape. `app = typer.Typer(no_args_is_help=True)`, a module-level `_COLUMNS`, bodies strictly: `ctx_obj(ctx)` → resolve names → build body → `client.<verb>` → `emitter.emit(...)`. Commands never touch stdout, never format, never catch HTTP errors. A new command is ~15 lines and inherits every cross-cutting feature automatically.

---

## 3. The standard command surface

Every tool ships these regardless of domain. They are what an agent can rely on before it knows anything about the domain.

```
<tool> auth      login / logout / status / whoami
<tool> settings  show / path / set-format / get-format
<tool> context   set / show / unset / clear / save / use / list / rm
<tool> guide     [topic]
<tool> install   claude [--print|--uninstall|--force|--project|--memory]
<tool> raw       get / post / patch / delete        (escape hatch)
<tool> <domain>… the actual product
```

### 3.1 Globals are parsed anywhere on the line

Click binds group-level options to the group, so `<tool> wp list --format markdown` is a hard parse error — yet that is exactly what humans and LLMs type, because they think of `--format` as a property of the *invocation*. Declaring it on all ~60 subcommands is unmaintainable.

So `main()` hand-parses argv **before Typer exists**, strips the globals from any position, and hands them off via env vars (there is no `ctx.obj` yet). It honours `--` as a stop sentinel and accepts both `--format x` and `--format=x`.

```python
_FORMAT_FLAGS = ("--format", "-f", "--output", "-o")
_FIELDS_FLAGS = ("--fields", "--columns")
_BOOL_FLAGS   = ("--dry-run", "--stream", "--no-context")

def main() -> None:
    fmt, fields, bools, argv = _pop_globals(sys.argv[1:])
    # ... each popped global -> os.environ[env("CLI_FORMAT" | "CLI_FIELDS" | "DRY_RUN" | ...)]
    try:
        app(args=argv)
    except DryRun as dr:
        sys.stdout.write(_json.dumps({"dryRun": True, "request": dr.request}, indent=2, default=str) + "\n")
        sys.exit(0)                                              # a dry run succeeded
    except OpError as exc:
        print_error(exc, _ERROR_FORMAT); sys.exit(exc.exit_code)
    except KeyboardInterrupt:
        print_error(OpError("interrupted"), _ERROR_FORMAT); sys.exit(130)
```

Two properties to preserve. **The root options stay declared on `@app.callback()` even though they are always stripped** — they look like dead code and they are what makes the flags appear in `--help`, the discovery path for humans and agents alike. Their help strings document the duality. Do not "clean them up." And **`_ERROR_FORMAT` is a module global** seeded to `json`: errors can be raised before or outside any command (config load, auth resolution, the callback itself) where there is no `ctx.obj` to ask for an emitter, and defaulting to the machine-readable format when things fail early is the right bias.

### 3.2 The reserved namespace — pick it first, then assert it

**This one actually shipped in opcli.** Because `_pop_globals` pops `--output` unconditionally from any position, no subcommand may declare a colliding option. `commands/attachments.py` did:

```python
output: Path = typer.Option(None, "--output", "-O", help="Output path (default: original file name).")
#                                 ^^^^^^^^^^ eaten by _pop_globals. -O (not -o) is the tell:
#   someone hit the SHORT-flag collision, worked around it, left the long form colliding.
```

The chain: `_pop_globals` takes `/tmp/x.pdf` as the format → `OutputFormat.coerce` raises → `_resolve_format` swallows it with a bare `except ValueError: pass` → format degrades to json → `download()` receives `output=None` → the file lands in CWD under its original name → **exit 0**. A silent wrong-destination write. The only test used `-O`, so CI never saw it. *(Fixed 2026-07-16: renamed to `--out`, plus the collision test below. Found by reading; **confirmed by running** — `_pop_globals(["attach","download","1","--output","f.pdf"])` really does return `fmt='f.pdf'` and an argv with the option gone.)*

**The reserved set is every popped flag — and NOTHING else.** This is the subtle part, and getting it wrong costs you real time: it is tempting to also reserve the root options (`--version/-V`, `--profile/-p`, `--no-color`), but Click **does not** remove those from a subcommand's argv, so a subcommand may legitimately shadow them. Verified on opcli: `wp list --version "Sprint 3"` filters by version and `raw get x -p k=v` passes a param — both correct. A reservation test that included root options flagged **four working commands** as broken. Derive the set from the popper's own tuples so it widens automatically if someone pops more later:

```python
RESERVED = set(_FORMAT_FLAGS) | set(_FIELDS_FLAGS) | set(_BOOL_FLAGS)   # popped == reserved
```

**For reference, the popped and root sets are:**

```
--format  -f  --output  -o          popped
--fields  --columns                 popped
--dry-run  --stream  --no-context   popped
--profile  -p   --no-color          root options, position-sensitive
--version  -V                       root option, is_eager — silently missing from the drafts
```

`--version`/`-V` is a real eager global (`cli.py:69`). It is not popped, but a subcommand declaring `-V` shadows it. Any tool that widens the popped set (Drone should pop `--profile`/`--no-color` too, so there is one rule instead of an asymmetry) must widen the *reserved* set to match.

**(a) Freeze the set before writing a single command, and prove it with a test.** Free, and it kills the class permanently:

```python
_RESERVED = set(_FORMAT_FLAGS) | set(_FIELDS_FLAGS) | set(_BOOL_FLAGS) | _ROOT_OPTS

def test_no_command_shadows_a_global_flag():
    """The argv pre-parser strips these from ANY position, so a subcommand that declares one
    silently never receives its value (see `attach download --output`)."""
    for path, cmd in _walk(typer.main.get_command(app)):
        for p in cmd.params:
            clash = _RESERVED.intersection(set(p.opts) | set(p.secondary_opts))
            assert not clash, f"`{path}` declares {sorted(clash)}, reserved by _pop_globals"
```

Then grep your intended surface for the natural collisions *before* they are written. Drone collides harder: `log view --output build.txt` and `secret add --value @file` are the obvious shapes. **Standardise file destinations on `--to PATH`, everywhere.** If you want `-f` for `--follow` (the `tail -f`/`kubectl logs -f` precedent), drop it from `_FORMAT_FLAGS` and leave `-o` as the only short format alias — but decide *now*, not after 60 commands exist.

**(b) An unparseable *explicit* flag must hard-fail.** The lenient `except ValueError: pass` is correct for the env and saved-config rungs (a typo in someone's `.bashrc` must not brick every command) and **wrong** for the explicit-flag rung — when the user is telling you the format *right now*, silence hides their mistake. `raise OpError(str(exc))` there.

While you are there: `take_value()` silently no-ops on a trailing `--format` with no value. That is permissive by design and probably fine — but make it a decision, not an accident.

### 3.3 The precedence ladders

Write each as a single linear function **whose docstring is the spec**. Every layer a CLI grows (flag / env / file / prompt / default) is a chance for surprising precedence.

- **Format:** `--format` anywhere > `-o/--output` > `$<X>_FORMAT` > saved default > first-run prompt (interactive only) > **json**. Terminating at json, not `table`, is the agent-first bet: when nothing is specified and nobody is watching, emit the parseable thing. The function is total.
- **Profile:** `--profile` > `$<X>_PROFILE` > `config.current_profile` > `"default"`. `--profile` writes into `os.environ`, so `config.py` never needs a Typer context and stays a leaf importable from anywhere including tests. One channel, not two.
- **Token:** `$<X>_TOKEN` > OS keyring > 0600 fallback file.
- **Base URL:** `$<X>_BASE_URL` **overlaid on** the saved profile — not substituted:

```python
def resolve(self) -> Profile:
    name, env_url = self.active_profile_name(), os.environ.get(env("BASE_URL"))
    prof = self.profiles.get(name)
    if prof is None:
        if env_url:                                       # headless: NO config file needed.
            return Profile(name=name, base_url=env_url)   # <- the entire CI/agent story
        raise ConfigError(f"no profile '{name}' configured. Run `<tool> auth login` "
                          f"or set {env('BASE_URL')}.")
    if env_url:
        return Profile(name=prof.name, base_url=env_url, username=prof.username,
                       verify_ssl=prof.verify_ssl)        # keep the saved TLS choice!
    return prof
```

Two load-bearing details. The `prof is None and env_url` branch means **`<X>_BASE_URL` + `<X>_TOKEN` alone run the entire CLI with no config file and no login step** — that is why CI and the test harness work at all. And the overlay preserves `verify_ssl`: naively returning `Profile(name, base_url=env_url)` silently re-enables TLS verification for someone who deliberately disabled it for a self-signed staging box, triggered by an unrelated env var.

### 3.4 `auth` — login / logout / status / whoami

**Verify before you persist.** Round-trip the identity endpoint *before* writing anything. Persisting first leaves a broken profile on disk that every subsequent command trips over, and the user must guess whether to fix the URL or the token. Verifying first makes a failed login a no-op — and the probe *earns* data: `me.get("login")` backfills the username so the user never types it. The per-argument flag → env → prompt cascade (rather than one `if interactive:`) is what makes the same command work fully-flagged in CI, env-driven in a container, and fully-prompted for a human.

**Harden the probe per API.** A wrong base URL often returns a 200 HTML login page rather than a 401. Assert the response parsed as JSON *with an identity key*, not merely that the status was 2xx.

**Know your API's anonymous read surface.** Drone registers `acl.AuthorizeUser` on `/api/repos` only when `DRONE_SERVER_PRIVATE_MODE=true` **[src]**, so on a default server public repos are readable with **no token at all** — `repo info`, `build ls` and `log view` return 200 unauthenticated. Two consequences: only the identity endpoint is a valid token probe, and integration tests against a public fixture repo do not exercise the 403 paths they appear to. Say so in the auth guide topic.

**`auth status` degrades instead of failing — the deliberate inverse of every other command.** It is the command you run precisely when things are broken; a traceback and a non-zero exit make it useless. Catch `OpError` from profile resolution, emit `{"configured": false, "reason": …}`, exit 0. Report `profile`, `baseUrl`, `credentialBackend`, `hasToken` (presence — **never the value**), and `reachable` + `me` if a token exists.

**`backend_name()` is not decoration.** Secret storage that silently degrades is a support nightmare: the user believes the token is in the Keychain, it is in a plaintext file, and nobody finds out until an audit. Mirroring `get_token`'s precedence in a one-line string makes the degradation observable at any moment, not just at store time when the warning scrolls past.

### 3.5 `settings` — show / path / set-format / get-format

Small, load-bearing for one reason: **the config location is variable**, so "edit your config file" is not actionable without a way to ask *where*. Hence `settings path`, and hence `set-format` echoing `configPath` back on success. Note the two-audience split: `show` renders `defaultFormat` as `"(not set — defaults to json)"` for a human (distinguishing *never chosen* from *chosen json*); `get-format` returns the effective `"json"` for a script.

**Reload before save.** `Config.save()` serializes the whole document, and the instance on `ctx.obj` was loaded during the root callback — already stale, because the first-run prompt and the Claude offer write to that file in between, and another shell may have run `auth login`. `cfg = Config.load()` inside the command, mutate, save. **The standard hazard of any whole-document JSON config: every writer must reload first.**

### 3.6 `context` — set / show / unset / clear / save / use / list / rm

Storage: two `dict` fields on the existing `Config` dataclass — `context` (live map) and `contexts` (name → saved map). No separate state file; sticky state is non-secret and hand-editable, so it belongs with the profiles.

Application: **convert the active map into a Click `default_map`.** Do not reimplement precedence — use Click's, and an explicit flag beats a default automatically with zero code.

```python
# _context_default_map(): OPTIONS ONLY — Click's default_map also satisfies required positionals,
# so including arguments would make a bare `project delete -y` silently delete the context's project.
opt_names = {p.name for p in cmd.params if getattr(p, "param_type_name", "") == "option"}

# _root():
if os.environ.get(env("NO_CONTEXT")) != "1" and ctx.obj.config.context:
    dm = _context_default_map(ctx.command, ctx.obj.config.context,
                              skip={"context", "settings", "guide", "install"})
    ctx.default_map = {**(ctx.default_map or {}), **dm}
```

Three guards, each fixing a distinct failure:

1. **`param_type_name == "option"`** — the single most important line in the subsystem, verified experimentally: a group setting `default_map={'delete': {'project': 'webshop-PROD'}}` on a `@click.argument('project')` command invoked bare exits 0 and deletes. This repo has positionals named exactly like context keys (`project delete/archive`, `wp move <project>`, `wp assign <user>`). **Sticky state may fill an optional input; it must never satisfy a required one the user forgot.**
2. **The `skip` set** — the command that *edits* the context must not be *fed* by it, or the feature eats itself: a bare `context set` would silently re-set the existing project instead of raising `nothing to set`. Meta commands are excluded because they must not depend on the state they exist to explain.
3. **`--no-context`** — the one-invocation escape.

Behaviour: `set` merges, so `set --project X` then `set --assignee me` composes. `unset`/`rm` use `.pop(k, None)` and are idempotent. `save` on an empty context and `set` with no flags both **raise** — an agent that got exit 0 would believe state exists. `use` on an unknown name enumerates what exists, turning a failure into the discovery call the agent would otherwise make. Every mutation echoes the full resulting map, so state is confirmed in one call, not two.

**`KNOWN_KEYS` must become load-bearing.** In opcli it is defined at `commands/context.py:22` and imported *nowhere* — grep returns one hit, its own definition. The real contract is enforced dynamically by name-intersection, so drift is **silent**: rename `--assignee` to `--assigned-to` and the context entry stops applying. The `--set key=value` escape hatch makes it worse by accepting arbitrary keys that quietly no-op. Fix: drive `context set`'s options *from* the constant, and add `test_known_keys_match_real_options`. That test also catches a positional-vs-option mismatch at test time rather than via a sticky default that mysteriously does nothing.

**Type coercion becomes live the moment a key is not a string.** Every opcli key is a string, so the JSON round-trip is lossless. Click *does* cast `default_map` values through the param's type, but `--set key=value` stores strings, and an unparseable value fails at Click's converter pointing at the *flag*, not the context. Validate at `context set` time against the target option's type.

### 3.7 `guide`

Two module-level constants (`OVERVIEW`, `TOPICS: dict[str, str]`) and a ~15-line function. Unknown topic → print the available list **and the overview** (hand the lost agent the map, not just an error), then `raise typer.Exit(code=2)`.

**`guide` must run with zero config, zero auth, zero prompts.** It is exempted by name from all three ambient mechanisms (first-run format prompt, context `default_map`, Claude offer), and a subprocess test proves it: env stripped of `<X>_TOKEN`/`<X>_BASE_URL`, `<X>_CONFIG_DIR` at a nonexistent path, assert exit 0 and `"OUTPUT CONTRACT" in stdout`. If `guide` can fail with exit 3, the bootstrap story is dead on arrival: the agent cannot learn how to authenticate because the how-to-authenticate command demands authentication.

**The OVERVIEW section order is the design**, tuned for an LLM reading top-to-bottom:

```
WHAT IT IS
OUTPUT CONTRACT       <- FIRST. Parse-and-branch before feature discovery.
  - stdout is JSON by default — parse it.   (+ any non-JSON carve-out, e.g. logs)
  - Errors -> stderr as JSON, non-zero exit: {"error": "...", "status": 404}
  - Exit codes: 0 ok · 3 config · 4 auth · 5 not-found · 6 conflict · 7 validation · 1 other
  - Trim output: --fields id,subject,status,assignee.name  (dotted paths ok)
  - Large sets: --stream (NDJSON)
  - PREVIEW a write: --dry-run — prints the exact request and exits 0
AUTHENTICATE          <- env vars FIRST, labelled "best for agents/CI — never prompts"
USE NAMES, NOT IDS
FIND THINGS           <- "don't hand-write filter JSON"
DISCOVER COMMANDS
USE WITH CLAUDE CODE
KEY GOTCHAS           <- framed as "save yourself a round-trip"
TOPICS
```

Output contract before features, because an agent must know how to parse and branch on results before it knows what results exist. Auth leads with env vars because the keyring path can prompt. Every gotcha line is a failed API call the agent will not make.

### 3.8 `install claude`

The last mile: pip puts a binary on `PATH` but does nothing to make an agent aware of it. Writes `~/.claude/skills/<name>/SKILL.md` (or `./.claude/…` with `--project`). Flags: `--print`, `--uninstall`, `--force`, `--memory`.

**The frontmatter `description` is the entire matching surface** — Claude decides whether to load the skill from that string alone. Write **trigger phrases in the user's vocabulary**, not a summary of the software, and **anchor every trigger to the product noun**: for a CI tool, bare "build", "pipeline", "deploy" over-fire on every unrelated question; "a Drone build", "Drone secrets", "restarting a Drone build" do not.

Two structural moves: **the body points back at `guide` instead of restating the commands** (one source of truth; the skill cannot drift, and it stays under the tested `< 500` line budget); and **it is an f-string, so every literal brace must be doubled** — `{{"error": …, "status": 404}}` — a silent footgun that turns a JSON example into a `KeyError` at import time.

**Detection is layered best-effort; refusal is the default.** `shutil.which("claude")` alone produces false negatives (npm / `~/.local/bin` installs are invisible to a subprocess with a trimmed `PATH`); the `~/.claude` directory check is the load-bearing one. Refuse by default with a named `--force` override — that beats both guessing and hard-failing.

**Marker-fenced, idempotent edits to files you do not own.** The `~/.claude/CLAUDE.md` hint is the canonical shape for any tool that writes into a user's config: `<!-- <x>-cli:start -->` / `:end`, `if _MEM_START in existing: return` (no second copy), and an uninstall that partitions on the markers. `--uninstall` uses a bare `rmdir` in `try/except OSError`, so the directory goes only if empty — a user who dropped their own files next to `SKILL.md` does not lose them.

### 3.9 `raw`, and the escape-hatch ladder

`raw get|post|patch|delete <path>` against any endpoint, unserialized. With `--set '{...}'` on typed writes and `--raw/-r` on `get`-style commands, this is what lets the curated layer stay small and opinionated: **an unmodelled field never blocks a user.** Worth *more* against a thinly documented API, where `raw` is how an agent verifies an endpoint exists at all.

The ladder: **typed flags → `--where` → `--filters` (raw filter JSON, bypasses the builder entirely) → `raw`.** The comment on `--filters` says *"(overrides everything)"* and means it — no smart defaults injected. A caller supplying raw JSON has stated they want the API's semantics; helpfully modifying their hand-written array is a betrayal that is very hard to debug.

**One carve-out: `--raw` is refused on any endpoint carrying a credential.** `--raw` exists to bypass the serializer, which is precisely the redaction it would bypass.

### 3.10 Domain groups

Uniform, thin, boring. A module-level `_COLUMNS` of `(header, key | lambda row: …)` tuples is the trick that keeps a rich nested JSON default *and* a flat table: the lambda flattens at the presentation layer, so the JSON stays whole. `_accessor_value` swallows exceptions from callables and returns `None` — a column accessor that blows up on one malformed row degrades that *cell*, not the listing. A rendering bug must never lose the user's data.

**Docstring style — write once, read three times** (`--help`, `COMMANDS.md`, an LLM reading either): one-line imperative summary, blank line, `Example:` with a copy-pasteable invocation, then the one or two facts that save a failed round-trip. Choose the gotcha by asking *"what error will they hit on attempt #1?"* Option help names the accepted forms inline — `"Assignee (login/name/id or 'me')."` — so an agent learns `me` works without first calling a user-list endpoint.

**Sharing rule: the reporting command imports the list command's query builder.** That is why `cost report` and `time list` can never disagree about what "July" means.

### 3.11 First-run hooks — the most important agent-safety code in the tool

Two offers (choose a default format; install the Claude skill). Five rules:

1. **Gate on `stdin.isatty()` AND `stdout.isatty()`** — both. `<tool> list | jq` from a real terminal has a TTY on *stdin*, so an stdin-only check fires the prompt into a pipeline and hangs it forever on a `readline` nobody will answer. If stdout is redirected, no human is reading. This makes the prompt **structurally impossible** under Claude Code, CI, or any pipe.
2. **Prompt on stderr, read from stdin** — stdout stays parseable even in the pathological case.
3. **Exclude meta subcommands** — else `install` recurses and `settings set-format` interrogates you immediately before you set the format.
4. **Persist the "asked" flag *before* acting** — a decline, a crash, or a failed write all count as asked. Re-nagging every run is how a helpful prompt becomes a hostile one.
5. **Swallow every failure path** — an optional nicety must never fail the command the user actually ran.

**Inside CI, add belt-and-braces.** The isatty gate covers it, but the failure mode is worse than a hung shell: a `y/N` prompt inside a pipeline step hangs the build until the pipeline timeout. Also treat `CI=true` and the runner's own marker (`DRONE=true`) as definitively non-interactive.

---

## 4. Test strategy

### 4.0 The contributor promise — state this before the tiers, in the docs and here

> **`pip install -e '.[test]' && pytest` is green on a clean checkout with no Docker, no server, no tokens, no network.**

Every tool in this family has a heavyweight live tier (OpenProject: a 5-minute-seeding all-in-one image; Drone: Forgejo + Drone + Postgres + a runner). **If that stack is the first thing a contributor meets, they leave.** The tiering exists precisely so it isn't — but only if you *say so first*. Lead the README's Development section and the test plan with the two-line quickstart; mention compose afterwards, as the deeper tier for people touching the client↔server seam.

Verified on opcli with nothing running and no env vars: `pytest` → **144 passed, 103 skipped in ~2s**.

Three properties make it work. All three are load-bearing, and each prevents a specific failure:

1. **Absent config is a skip, not a failure.** The default developer state is "no server"; the default state must be green, or people learn to ignore red.
2. **The skip reason is actionable** — `live OpenProject not configured (set OPCLI_BASE_URL + OPCLI_TOKEN)`, not "skipped". With `addopts = "-ra"` the skip summary becomes a to-do list for anyone who wants the deeper tier.
3. **Configured-but-unreachable also skips**, via one real probe (`auth whoami`) computed **once at collection**. A half-booted stack must not yield 103 confusing failures.

**The marker is the source of truth — never a file list.** opcli's `make test-unit` ran `pytest tests/test_unit.py` (**30 tests**) while advertising "run only the pure-unit tests"; the real hermetic set is **144**. A contributor got 21% of the coverage and a green tick, and the target silently failed to grow when someone added a file. Ship `test-unit: pytest -m "not integration"`. *(Both this and the missing Contributing section were fixed in opcli on 2026-07-16 — the rule is why.)*

**Corollary: the heavier the live tier, the more weight Tier 1 must carry.** Contributors will rarely run tiers 2/3, so the hermetic tier is what actually guards a PR. Push everything that *can* be a pure function over a captured fixture into Tier 1 — and capture those fixtures from a real server during the spike (§7.2), never hand-write them.

### 4.1 The tiering

One suite, split by markers. opcli's shape: **233 test functions across 24 files, collecting as 247 cases** (parametrization expands them). On the collected basis the split is **144 hermetic / 103 live — 58% / 42%**. State the basis whenever you quote it: "233 functions" and "247 tests" are both true and are not the same number. Do not quote a count you have not counted (§8).

| Tier | Runs | Cost | Scope |
|---|---|---|---|
| **1 — hermetic unit** | every push/PR, py3.10/3.11/3.12 | seconds, no Docker | pure logic, transport policy, fakes |
| **2 — live integration** | every push/PR | minutes | the real binary against a real server |
| **3 — version / provider matrix** | weekly cron + `workflow_dispatch` | ~30 min | "which versions do we support?" as a CI fact, not a README guess |

```toml
[tool.pytest.ini_options]
addopts = "-ra -q"     # -ra: summarise every skip reason — this IS the version-skew report
markers = ["integration: talks to a live instance (requires <X>_BASE_URL + <X>_TOKEN)"]
timeout = 120          # pytest-timeout: no single test may wedge the job
```

`conftest.py` gates at collection time so the probe runs once per session, and **the probe dogfoods the CLI's own auth command** (`_run(["auth", "whoami"]).code == 0`) rather than a raw health ping — a `GET /health` can pass while the token is wrong, producing a hundred confusing failures instead of one honest skip. A bare `pytest` on a laptop with no Docker is green, with skip reasons naming the two env vars to set: the failure teaches the fix.

**Consider a third axis where the domain offers one.** Drone's control and execution planes are separable: a build created with **no runner attached** is a real DB row with real JSON, real error codes and real auth behaviour — it just sits `pending` forever. So repo/secret/cron/template/user CRUD, build create/list/info/restart/cancel and sign/encrypt get full server fidelity without the flakiest component (docker.sock, an image pull per step, timing waits). Split `integration` / `needs_runner`, run the runner nightly. The loss is bounded and honest: status transitions, logs, cancelling a *running* build.

### 4.2 Tier 1 — hermetic units, three flavours

**Pure logic** — duration parsing, date parsing, `--where` lexing, output rendering via `capsys`. `parse_where` returns a pure `(field, symbol, values)` tuple with no client and no network; it is the one function that survives a backend swap untouched.

**Transport policy via `httpx.MockTransport`** — in exactly one file (`test_client_retry.py`, 6 tests) and **only where the transport itself is under test**. It is never used to simulate API semantics, and that line is principled: a semantics mock asserts "my code matches my beliefs", and the whole reason `API_NOTES.md` exists is that the beliefs were wrong. A mock suite would have been 100% green while the CLI was broken against every real server.

```python
def test_post_5xx_not_retried():
    assert calls["n"] == 1     # a POST may have landed — retrying could double-create

def test_post_429_is_retried():
    assert calls["n"] == 2     # 429 means REJECTED, never processed — safe to replay
```

Pin all four corners. These tests are how you can tell the policy was reasoned about rather than stumbled into, and they are what stop someone "fixing" it wrongly.

**Fake collaborators** — a ~35-line hand-written `FakeClient` in `support.py`. No mock library, no autospec, no patching. Used by exactly **two** test files (`test_wpfilters_unit.py`, `test_searchspec_unit.py`); the large unit files test pure functions over data and need no collaborator at all. Its one trick is synthesising a plausible resource from the path (`{"id": …, "identifier": last, "name": last, "_links": …}`) so most tests need no seeding. This buys the whole query layer under test with no network, and **the test file doubles as the readable spec of the wire format** — exactly what the vendor's docs make hard to learn. It only works because `build()` returns *data*, not requests; the moment a builder performs its own I/O this style dies.

**Annotate the traps inline in the samples.** `SAMPLE_WP._links.customFields` carries `# the admin collection link — must be ignored`, and a test asserts `"customFields" not in cf`, pinning a real bug where the admin URL was hoovered into every record's custom-field map.

**Content-contract tests — prose as API.** The guide and `SKILL.md` are product surfaces consumed by a machine, but they are just strings; nothing else notices if a refactor drops the auth env-var name. Cheap, fast, no network: assert `"OUTPUT CONTRACT" in OVERVIEW`, the token env var is named, the discovery command is pointed at, every topic body is >40 chars, `SKILL_MD` starts with `---`, mentions `<tool> guide`, and is under 500 lines.

### 4.3 Tier 2 — live integration, driving the real binary

**Never `CliRunner`.** It tests the Python callback, not the product. Exit codes, the stdout contract, structured errors on stderr, and the fact that `-o` works *after* the subcommand all live at the process boundary an agent consumes.

One helper: `_run(args, *, stdin=None, output="json", token=None, env=None)` shells `[sys.executable, "-m", "<x>cli", "-o", output, *args]` with a copied environ, and returns a `Result(code, stdout, stderr)` carrying `.json` and `.ok()` — the latter asserting exit 0 **while surfacing stderr in the assertion message**, which is what turns a bare `assert code == 0` into a readable failure. Tests read `op(["wp", "create", …]).ok().json["id"]`.

**Fixtures: RUN_ID-namespaced session parent, function-scoped children, created *through the CLI*.** `RUN_ID = uuid.uuid4().hex[:8]` lets concurrent runs and a CI matrix share one server safely. Building fixtures through the CLI makes setup itself a smoke test: if `project create` breaks, everything errors loudly at setup rather than failing obscurely. The scope split is a cost decision — projects are slow (session), work packages cheap (per-test).

**Teardown is a per-tool decision, not a given.** Drone builds cannot be deleted at all, so the session fixture becomes "ensure a scratch repo exists and is activated" and the per-test fixture yields a triggered **build number** with no teardown — and every assertion must be against *the number you triggered*, never "the latest build", or parallel tests interfere.

**The second-actor token.** `_run(..., token=…)` overrides `<X>_TOKEN` for one invocation, keeping the subprocess model intact and making multi-actor tests read as plain command sequences. This is what makes notifications — the classic untestable-with-one-account feature — testable. **Name it in `paths.py`'s namespace (`<X>_SECOND_TOKEN`), wire it into the Makefile's `env` target and both CI workflows, and `pytest.skip` when unset.** It is not optional wherever the API has a system-admin bypass: Drone's `user.Admin` short-circuits *every* repo ACL check **[src]**, so an admin-token suite exercises essentially no authorization logic and the 403 paths ship untested.

**One env var makes both tiers hermetic.** Unit: autouse `monkeypatch.setenv("<X>_CONFIG_DIR", tmp_path)`. Integration: `env={"<X>_CONFIG_DIR": str(tmp_path)}` through `_run`. `tests/test_context.py` states the hazard in its own docstring: it writes *global* on-disk state, and without isolation `context set --project X` silently re-scopes every subsequent test — a leak that manifests as **wrong results in a passing-looking suite**, not as an error.

**Graceful skips for environment/version variance — never for CLI behaviour.** This is what let one suite pass unmodified across OpenProject 13–17. The skip must be **narrow** — a specific error string, or a discovery query returning nothing — never a blanket try/except, or real regressions hide inside skips. `addopts = "-ra"` surfaces every skip reason, making the summary the version-skew report.

**Regression tests carry the bug in the docstring:** *"Client.collect must return every item even when pageSize forces >1 page (regression: don't stop on a short page — the server may cap pageSize)."*

**The pty test — the only way to exercise TTY-gated code.** `subprocess.run(capture_output=True)` has no tty, so the guard returns early and the branch looks "covered" while never running. Use `pty.fork()` + `os.execve`. Two details that are bugs waiting to happen: the config must be **pre-seeded with `default_format`**, or the *other* interactive prompt fires first and eats the `y\n`; and the read loop must catch `OSError`, because a Linux pty raises EIO on child exit rather than returning `b""`. The second-run assertion (`"Claude Code detected" not in out2`) is what actually pins the once-only contract — the part users notice when it breaks.

### 4.4 The local stack — a solved recipe, not a risk

Stand up the real server non-interactively before the first domain command. `make up && make env && make test` is the same sequence CI runs, so a green laptop predicts a green CI.

For a self-contained app (OpenProject) that is one service plus a token-minting script. For anything depending on an SCM (Drone) it is three services and a seeding sequence — which reads as a risk until someone runs it. **It has been run.** The following drove a real pipeline to `success` and read its logs back, with no browser and no OAuth click-through **[live]**:

1. `gitea:1.22` + `drone/drone:2` + `drone/drone-runner-docker:1` on one compose network.
2. `gitea admin user create --admin --must-change-password=false`.
3. Gitea PAT via the API with basic auth: `POST /api/v1/users/{user}/tokens` → `.sha1`.
4. **`DRONE_USER_CREATE=username:droneadmin,admin:true,token:<known>`** seeds a Drone user with a known API token — no OAuth needed for API auth. Note `token:` with a **colon**: the parser splits on `:` and silently skips malformed entries, minting a random token instead **[src]**.
5. **The step nobody expects: the Drone API token is not the SCM token.** Anything touching the SCM (sync, activate, repo list) reads `users.user_oauth_token`, which no API can set. Without it, `POST /api/user/repos` returns **HTTP 500 with body `{"message":"Unauthorized"}`** **[live]** — a bad status mapping that is itself an argument for never blindly retrying 500. Inject the Gitea PAT directly: `docker cp drone:/data/database.sqlite`, patch with host `python3`'s `sqlite3` (the image has no sqlite3 binary), copy back, restart. Then sync/enable/build all work. **[live]**
6. **`DRONE_RUNNER_NETWORKS=<compose network>`** on the runner, or the clone step dies with `Could not resolve host: gitea` — build containers otherwise land on the default bridge. **[live]**

Then `POST /api/user/repos` (sync) → `POST /api/repos/{o}/{n}` (activate) → `POST /api/repos/{o}/{n}/builds?branch=main` → the runner executes → logs are readable.

Three generalizable lessons from that recipe:

- **The health endpoint lies.** Drone's `/healthz` is an unconditional 200 with no DB check **[live]**; OpenProject's `/health_checks/default` flips green before the app can service an admin-console invocation, so a single-shot token mint flakes intermittently and reproduces roughly never locally. **The real readiness probe is the first authenticated call** (`GET /api/user`), which proves server-up *and* bootstrap-ran *and* token-valid in one request. Find your liar and give it its own retry loop.
- **Assert behaviourally, never structurally.** The seed asserts "sync returned ≥1 repo", not "the UPDATE succeeded". End it in a `SEED_OK` sentinel the shell wrapper `grep`s for, because scripting hosts (`rails runner`, `psql`) exit 0 after printing a stack trace. Mint credentials fresh per run rather than reusing them — for OpenProject the plaintext is only recoverable at creation, so idempotency there means *reset*, not *reuse*.
- **The seed encodes every "the API refuses this unless the server is configured" lesson as executable setup** — modules not enabled → 422; custom fields absent → CF tests cannot run; an empty git repo has no commit to build.

---

## 5. CI/CD, packaging, docs

### 5.1 Three workflows, split by cost

**`ci.yml`** — every push + PR, `concurrency` with `cancel-in-progress` (a 25-minute integration job must not pile up). Three independent jobs: `unit` (the Python version matrix, `-m "not integration"`, seconds, no Docker), `integration` (boots the real server, full suite, `timeout-minutes: 25`), `build` (wheel + sdist + PyInstaller binary, each smoke-tested). **The Python matrix runs only on the hermetic job** — version skew is a pure-Python concern, and booting three servers to prove it costs 15 minutes for zero extra signal.

Boot choreography is **two independent waits**, because there are two independent readiness conditions and only one has an endpoint (§4.4). Credentials are minted fresh per run into `$GITHUB_ENV` — CI holds no standing secret for the test instance. `if: failure()` log dumps (`docker compose logs --tail=100`) are what make a red matrix job diagnosable without a re-run.

**`compat.yml`** — `workflow_dispatch` + weekly cron only, `fail-fast: false`. The structural move: `COMPOSE_FILE` is an env var Compose reads natively, so the same workflow body serves both the pinned dev instance and the matrix with **zero branching**. The versiontest compose interpolates `${X_IMAGE}`/`${X_PORT}`; run it under a distinct `COMPOSE_PROJECT_NAME` and several versions coexist locally (opcli documents this in the compose header but `compat.yml` doesn't set it — do set it). That compose file carries a scar worth keeping: it sets *both* `OPENPROJECT_SECRET_KEY_BASE` and plain `SECRET_KEY_BASE`, because the v17 image refuses to boot without the latter — found only by running the matrix.

**Choose the matrix axis per tool.** A version axis is only worth it if the vendor still ships versions that drift. Drone is in maintenance and its API is frozen, so a version matrix yields almost no signal — **the high-value axis there is the SCM provider** (gitea vs github), which changes payload shapes and auth flows. And because Drone boots in seconds rather than five minutes, that matrix belongs in `ci.yml`, not a weekly cron. A frozen API is a *feature* for a wrapper: no drift, no compat burden.

### 5.2 Release: tag-triggered, decentralized, no stored secrets

Tag `v*` (plus `workflow_dispatch`, which builds but never publishes). A `fail-fast: false` matrix over `ubuntu-latest` / `macos-latest` / `macos-13` / `windows-latest`, each renaming to `<tool>-<os>-<arch>` and **attaching its own asset the moment it finishes**. There is no fan-in job: GitHub's macOS-Intel queue can be tens of minutes, and an aggregate job holds the entire release hostage to the slowest runner. `softprops/action-gh-release@v2` is idempotent-additive against the same tag, which is what makes N-writers-one-release safe.

**PyPI via Trusted Publishing (OIDC) — no token exists anywhere.** Write the one-time setup into the comment; the five values must match PyPI's pending-publisher config *exactly*, and a mismatch fails only at publish time on a real tag:

```yaml
# One-time setup on PyPI: Your projects -> Publishing -> add a pending publisher
#   PyPI project name: agent-tool-openproject-cli   Owner: <owner>
#   Repo: agent-tool-openproject-cli   Workflow: release.yml   Environment: pypi
pypi:
  if: startsWith(github.ref, 'refs/tags/')
  environment: { name: pypi, url: https://pypi.org/p/agent-tool-openproject-cli }
  permissions: { id-token: write }        # the ONLY credential; no secrets.PYPI_TOKEN
  steps: [{ run: python -m build }, { uses: pypa/gh-action-pypi-publish@release/v1 }]
```

**Consider a container image as a fourth artifact when the tool runs inside CI.** `ghcr.io/<owner>/agent-tool-<x>-cli:<tag>`, `platforms: linux/amd64,linux/arm64`, `permissions: packages: write` (GITHUB_TOKEN, no stored secret), attached independently like every other job.

### 5.3 PyInstaller: the launcher and the keyring flags

`packaging/<x>_launcher.py` is 11 lines: a docstring, `from opcli.cli import main`, `if __name__ == "__main__": main()`. **PyInstaller freezes a *script*, not a package**, so the launcher does the absolute import PyInstaller needs. Point it at `src/<x>cli/__main__.py` instead and it executes as `__main__` with no package context → `ImportError: attempted relative import with no known parent package`, **at runtime, in the shipped binary**. The docstring says exactly that, or someone deletes the file as redundant.

```bash
# keyring discovers its OS backends via entry points/metadata, which frozen apps drop
# by default — collect them explicitly, and add the platform backend.
case "$(uname -s)" in
  Linux)  EXTRA+=(--collect-all secretstorage --collect-submodules jeepney
                  --hidden-import keyring.backends.SecretService) ;;
  Darwin) EXTRA+=(--hidden-import keyring.backends.macOS) ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT) EXTRA+=(--hidden-import keyring.backends.Windows) ;;
esac
pyinstaller --noconfirm --clean --onefile --name openproject \
  --paths src --collect-all keyring \
  --hidden-import keyring.backends.chainer --hidden-import keyring.backends.fail \
  "${EXTRA[@]}" packaging/op_launcher.py
```

**The highest-value scar in packaging.** Keyring finds backends through importlib entry points at runtime; PyInstaller's static analysis sees zero imports and strips every one. The binary builds fine, `--help` works fine, and `auth login` fails on a user's machine with `NoKeyringError` — and CI's `--help` smoke test never touches the keyring, so nothing catches it. `chainer`/`fail` are unconditional because keyring's backend *selection* imports them regardless of platform.

**Smoke-test the artifact you ship, not the source tree.** `pip install dist/*.whl` into a clean venv, then run the console script. `pip install -e .` proves nothing about the wheel: it shortcuts the packaging metadata entirely, so a broken `[project.scripts]` target, a package missing under `where = ["src"]`, or a dependency that only exists as a test extra all pass locally and fail for the first `pipx install` user. `--help` is deliberately minimal — it proves the interpreter, the import graph and the entry point resolve, and nothing more.

**Extras are the CI contract.** `test = [pytest, pytest-timeout]`, `build = [pyinstaller]`; every job installs `pip install -e '.[test]'` or `'.[build]'`. No `requirements-dev.txt`, no `tox.ini`, no parallel list. One source of truth means CI cannot drift from what a contributor gets, and it directly determines what PyInstaller can bundle.

### 5.4 Docs — five layers, split by *lifetime*

| File | Job | Lifetime |
|---|---|---|
| `README.md` | discovery/SEO, install, 60-second cheat sheet | hand-written, short enough to stay true |
| `docs/USAGE.md` | task-oriented walkthroughs | hand-written |
| `docs/COMMANDS.md` | exhaustive option reference | **generated — never hand-edit** |
| `docs/API_NOTES.md` | researched API truth + gotchas | append-only knowledge log |
| `AGENTS.md` + `<tool> guide` | the machine contract | in-repo + in-binary |

Splitting by lifetime is the insight: the hand-written layers stay short enough to keep accurate, while the layer that rots fastest (every option of every command) is generated. Each file states what it is *not* and links onward. Each surface points *down* to the next rather than restating it: SKILL.md (one screen, triggers + pointer) → `guide` (offline, in-binary, terse) → `AGENTS.md` (full contract) → `COMMANDS.md` (generated). `AGENTS.md` declares its audience in the first line — *"This page is the machine contract — how to call it reliably and cheaply"* — then hands off: *"**No context? Start here:** run `openproject guide`."*

**`gen_docs.py` introspects the live app** (`typer.main.get_command(app)` — the real CLI, not a description of it) so the docs cannot drift. Two details: skip params where `param_type_name == "argument"`, and **escape `|` and flatten newlines in help text** or the Markdown table silently corrupts — this CLI's own `--output` help says `json|table|markdown`.

**But nothing runs it.** Not CI, not the Makefile, and no test asserts freshness — so "docs never drift" is aspirational. Wire it into the `unit` job; it costs seconds: `python scripts/gen_docs.py && git diff --exit-code docs/`.

**README skeleton** (fixed order): H1 `name — tagline` → a blockquote stuffed with natural search terms → a five-badge row (PyPI / CI / Python versions / License / "Agent ready") → **Install:** before anything else → a paragraph naming **both the PyPI package and the installed command** (they differ; without both, users can't find either) → "### Why this X?" bullets written as **objections answered**, not a feature dump ("Never memorise filter JSON") → a **Docs:** link hub → an explicit **Keywords:** line mirrored into `pyproject.toml` → Compatibility → Quick start → per-group cheat sheet → "Output for agents" → Security notes → **Known limitations**.

**"Known limitations (API, not the CLI)" — blame correctly.** Separate *the upstream API cannot do this* from *we chose not to build this*, and repeat the limitation inline at the relevant command group. Every line is a support ticket pre-answered. *"Costs: no hourly rates or cost-report endpoints exist — hence the client-side rate table for invoicing"* stops both the bug report **and** the "why is this so hacky" question, in one line.

**`API_NOTES.md` — the hard-won-knowledge log**, and the highest-leverage artifact in the repo (1771 lines at opcli; nearly every non-obvious line of code traces to a line in it). One `# AREA:` per API domain, each with the same five headings:

```
# AREA: <domain>
**Auth:** permission gotchas for THIS area
## Endpoints        path + req/res notes
## Gotchas          the payload
## Examples         runnable curl
## Open questions (verify live)     <- claims explicitly marked unverified
...
# COMPLETENESS CRITIQUE   ## 1. Contradictions between areas  ## 2. Scope gaps
                          ## 3. Claims that MUST be verified against the live instance first
```

Two things make it work. **"Open questions" legitimises writing down "I am not sure"** — it turns research into a testable checklist instead of confident wrongness. And the critique section catches cross-area contradictions. It also records **negative results**, the most expensive thing to rediscover and the whole justification for the killer feature: *"HourlyRate is NOT in API v3. Confirmed by the community (topic 13912) and by the absence of any HourlyRate representer."*

**And then go and run the open questions.** The Drone research was excellent — dozens of source-level claims verified to the line number — and still wrong about the two facts that would have shaped the most code: the 501 surface, and an SPA-HTML-on-404 trap that does not exist (wrong `/api` paths return a plain-text `404 page not found` **[live]**). Reading source is not observation. A half-day spike settled both, plus a dozen endpoint facts no amount of reading would have produced.

---

## 6. Hard-won lessons

Bug-driven decisions. Each line is a defect that already shipped once, or a trap already sprung.

### Argv, flags, exit codes

- [ ] **Pick the global flag set before writing any command**, treat it as reserved across the whole tree, and add the collision test. *(`attach download --output` silently writes to CWD, exit 0.)*
- [ ] **The reserved set includes every root option**, not just the popped ones — `--version`/`-V`, `--profile`/`-p`, `--no-color`.
- [ ] **An unparseable *explicit* flag hard-fails.** Lenient is right for env and saved config, wrong for a flag the user just typed.
- [ ] **Keep the inert root option declarations.** They look dead; they are what puts the flags in `--help`.
- [ ] **Honour `--` as a stop sentinel**, or you mangle user data that looks like a flag.
- [ ] **Never renumber an exit code** (three published places = API), and never invent one for a condition you have not observed.
- [ ] **Leave 2 to Click.** `typer.Exit(code=2)`, not an `OpError`.
- [ ] **`pretty_exceptions_show_locals=False`.** *Security*, not cosmetics: Typer's rich traceback dumps locals, which here include the API token and full request bodies — into the terminal, into an agent's context, into CI logs.
- [ ] **Convert foreign exceptions at the boundary.** A bare `json.JSONDecodeError` is not an `OpError` and escapes `main()` as a traceback.
- [ ] **`_ERROR_FORMAT` module global**, seeded to json — errors raised before a command exists have no `ctx.obj` to ask.
- [ ] **`DryRun` must not inherit `OpError`**, or a dry run exits non-zero.
- [ ] **Do not leak observed status into the exit code** (a red build ≠ the CLI failed).

### HTTP

- [ ] **The retry matrix is three buckets, and the reasoning is *did the write land?*** Connection error → never reached the server → retry **any** method. **429 → actively rejected, never processed → retry any method, including POST** (most naive clients wrongly exclude POST here and needlessly fail). **502/503/504 → ambiguous; the write may have succeeded and only the response was lost → retry only GET/HEAD/PUT/DELETE** — retrying a POST double-creates, and for a CI tool double-triggers a pipeline.
- [ ] **Leave 500 out of the transient set.** Usually a real error, and sometimes a *misclassified* one: Drone returns 500 with a JSON `{"message":"Unauthorized"}` body when the SCM token is bad **[live]**. Retrying that is pure latency.
- [ ] **`Retry-After` is a floor, not an override** — `max(delay, retry_after)`. A server sending `Retry-After: 0` (OpenProject does, on some 429s) must not defeat exponential backoff into a hot spin.
- [ ] **Cap at 30s, add 0–250ms jitter.** The cap stops a hostile `Retry-After: 3600` hanging an invocation for an hour; the jitter stops a fleet of agents 429'd at the same instant retrying in lockstep.
- [ ] **`float(retry_after)` in a try/except** — it is legally an HTTP-date.
- [ ] **Never wrap a streaming read in the retry loop** — retrying a half-consumed stream replays lines the caller already saw.
- [ ] **Set the write Content-Type — except for multipart.** Body-less POSTs 406 without it; forcing it onto multipart destroys httpx's boundary, unrecoverably. Use `setdefault`, and keep the `if files is None` guard *with its comment* even in a tool that doesn't upload, or someone will "simplify" it back into a bug in a tool that does.
- [ ] **`resp.json()`-then-`resp.text` guard on every error body.** Ordinary hygiene: not every error path returns JSON. (The specific "SPA HTML on any wrong /api path" trap the drafts warned about **does not exist** — those return plain-text `404 page not found` **[live]** — but the guard costs one line and the class is real.)
- [ ] **Normalize URLs three ways** (full URL / absolute path / bare collection), so following a returned link is free.
- [ ] **Drop `None` params at the chokepoint** so callers can pass optionals unconditionally.
- [ ] **Configure auth once on the session object**, not per request. Version-stamp the User-Agent.
- [ ] **A generous default timeout is deliberate.** `ReadTimeout` is retryable, so a too-low default silently triples load on legitimately slow queries.

### Pagination — the silent-truncation class

- [ ] **Know which stop-rule your API gives you, and write the comment explaining why.** The sharpest porting risk in this document, because the rule *inverts*. **With an authoritative `total`:** stop on empty page or `seen >= total`; **do NOT stop on a short page** — the server may cap `pageSize` below what you asked (ask 100, get 20, conclude "done", silently lose 80%). **Without a `total`** (bare arrays, `?page=&per_page=`): a short/empty page is the *only* terminator, so you are forced onto exactly the heuristic the first rule forbids.
- [ ] **When forced onto the short-page heuristic, clamp `per_page` client-side to the server's real, empirically-verified maximum.** Drone's build list resets `per_page` to **25** when it is >100 **[src]** — ask for 200, get 25, the heuristic says "done", and you truncated at 12% with exit 0 and valid JSON. Clamp to 100 *first*; then, and only then, a short page is genuinely the last page. The cap is per-handler, not global (the admin repo list has the >100 check commented out **[src]**) — pin **both** with a >1-page fixture test.
- [ ] **Check `limit` inside the per-element loop** so it returns mid-page rather than over-fetching.
- [ ] **No server-side filtering means a page budget.** `--where status=failure --limit 10` against a healthy repo pages the entire history. Add `--max-pages` (~20) **and an explicit `{"truncated": true, "pagesScanned": N}`** — *stopped searching* must never render as *nothing found*. Same for `--count`: count within the budget **and report truncation, or the number is a lie**.

### Output

- [ ] **`output.py` imports nothing domain-specific.** The seam is the portability.
- [ ] **`emit()` must never raise.** A command that created a work package and then died in the renderer is the worst outcome available: the side effect happened, the user gets a traceback and no id. Hence `_jsonable` *and* `default=str` — belt and braces.
- [ ] **`_jsonable` is deliberately shallow** — `asdict` already recurses; `default=str` catches the rest.
- [ ] **`ensure_ascii=False` everywhere**, or umlauts and CJK become `\uXXXX`.
- [ ] **`highlight=False` on the Console.** Rich will colour numbers and URLs *inside* your JSON.
- [ ] **`console.print(text, markup=False)` for server-controlled text**, or bypass the Console with `sys.stdout.write`. `highlight=False` does *not* protect against this: Rich interprets `[...]` as markup, and CI logs are full of `[INFO]`, `[error]`, ANSI escapes. OpenProject never hit it because work-package subjects rarely open with a bracket; a CI tool hits it on day one.
- [ ] **`Emitter.message()` guards with an ALLOWLIST** (`== table`). opcli ships `!= json`, which would corrupt csv and markdown; unreachable today only because its single caller is already wrapped in an `== "table"` check.
- [ ] **The `lambda r, _f=f:` default-arg binding is load-bearing.** Without `_f=f`, late-binding closures make every projected column render the *last* field.
- [ ] **CSV header = union of keys across all rows**, insertion-ordered — not `rows[0].keys()`. Serializers legitimately emit ragged rows; if row 0 lacks a custom field, a first-row header silently drops it. That is the invoicing path: a missing column is a missing charge. Use a list with `in` (not a set) so column order is stable and exports diff cleanly.
- [ ] **Three cell coercers, one per grammar.** `yes/no` is right for a human table and wrong for CSV, which feeds pandas where `true/false` parses as boolean. CSV renders dicts as embedded JSON so the cell round-trips through `json.loads` instead of emitting Python repr with single quotes.
- [ ] **In `_md_cell`, escape backslash FIRST, then pipe.** Reverse the order and `|` → `\|` → `\\|` prints a literal backslash. Newlines → `<br>`, or the table shatters into garbage rows.
- [ ] **`stream_json` must `flush()` per line.** stdout is block-buffered when piped; without it the laziness is invisible downstream.
- [ ] **Renderers degrade, never refuse.** A columnless list in markdown becomes a fenced ```json block — still valid markdown, so the format contract holds even where the ideal rendering doesn't exist.

### Config and credentials

- [ ] **`config_dir()` is a function.** As an import-time constant, test isolation silently no-ops and the suite clobbers the developer's real `~/.config`.
- [ ] **Env-URL is overlaid, not substituted** — preserve `verify_ssl`, or you silently re-enable TLS verification for a self-signed staging box.
- [ ] **A profile can be synthesized from env with no config file.** This is the entire CI/agent story.
- [ ] **`_keyring_available()` must isinstance-check `keyring.backends.fail.Keyring`.** Keyring does *not* raise on a headless box — it silently installs a "fail" backend that only explodes at `set_password` time. A try/except-on-import reports success and blows up mid-login.
- [ ] **`os.open(..., O_CREAT, 0o600)`, not write-then-chmod** — the plaintext token is never briefly world-readable. (0o600 has no group/other bits, so umask cannot widen it; the redundant `chmod` afterwards narrows a *pre-existing* wider file.)
- [ ] **`delete_token` purges every backend**, not just the active one, or a token left in the fallback file resurrects after the user fixes their Secret Service and `logout` silently lied.
- [ ] **Import `keyring` lazily inside each function**, so an import-time failure on an exotic host can't break `--version`.
- [ ] **Report the backend** (`backend_name()`) — silent degradation is an audit finding.
- [ ] **Absent config is not an error.** Every dataclass field has a default; `default_format: str | None = None` where `None` means *not yet chosen* (distinct from *chosen json*) — that tri-state drives the first-run prompt exactly once.
- [ ] **`raw.get("context") or {}`, never `.get("context", {})`** — a literal `"context": null` in a hand-edited file must become `{}`, or every downstream dict access explodes.
- [ ] **Malformed config → typed error naming the path.** Catch `(ValueError, KeyError, TypeError, AttributeError)`. The path is variable, so "malformed config" without it is unactionable.
- [ ] **Reload before every read-modify-write** of the whole-document config.
- [ ] **Lazy, memoized client.** Constructing it in `__init__` makes `--help`, `settings path` and `guide` demand credentials they don't need.

### Packaging

- [ ] **One source of truth for the version** (`dynamic = ["version"]`). Two copies means shipping a wheel that misreports itself in its own User-Agent and in the skill it writes to users' machines.
- [ ] **Force-collect keyring's backends** in PyInstaller, or `auth login` fails only on a user's machine.
- [ ] **Freeze a launcher script, not the package** — a relative `__main__` import dies at runtime in the shipped binary.
- [ ] **Smoke-test the wheel in a clean venv and the binary as built** — `pip install -e .` proves nothing about either.
- [ ] **Refuse colliding command names**, and announce the refusal with its reason in the README.
- [ ] **Register command groups at the bottom of `cli.py` with a comment naming the cycle**, or someone tidies the imports to the top and breaks the build.

### Interactivity

- [ ] **Gate on `stdin.isatty()` AND `stdout.isatty()`.** stdin-only hangs `| jq` forever.
- [ ] **Prompt on stderr. Set the "asked" flag before acting. Exclude meta subcommands. Swallow every failure in a nicety.**
- [ ] **Inside CI, add a second signal** (`CI=true`, the runner's own marker). A blocked prompt hangs a build until the pipeline timeout.

### Correctness traps that cost money

- [ ] **Accumulate unrounded; round once, at output.** Rounding 200 entries to cents before summing makes the invoice total disagree with the sum of its own printed rows. An accountant notices; no test does.
- [ ] **Ordered candidate keys for config lookups, most-specific first.** A rate table with both `"jane.doe": 100` and `"Jane Doe": 120` bills a different amount depending on dict iteration order otherwise — silent, money-losing nondeterminism.
- [ ] **`amount: null`, not `0`, when a rate is missing.** Never silently invoice work as free. Pair with `"billable": false` so a downstream agent knows the numbers are hours, not currency.
- [ ] **Deep-copy a caller-supplied patch body per retry attempt.** `loads(dumps(patch))` looks like paranoia and is the subtlest bug-fix in `client.py`: the merge uses `body.setdefault("lockVersion", lv)`, so attempt 1 stamps the stale version into the caller's dict and on attempt 2 `setdefault` is a **no-op** — the retry resends the stale value and 409s forever, surfacing a conflict that was actually resolvable. **Any retry loop that mutates a caller's dict has this bug**, lockVersion or not.
- [ ] **Scoped-then-global resolution is a correctness fix, not an optimization.** Resolving an assignee against the global user list can return someone who then 422s on assignment. The CI analogue is secrets: repo-scoped, *then* org-scoped.
- [ ] **Diff request against response when the API is known to lie.** Two Drone endpoints return **200 having changed nothing** **[src]**: `repo update` silently drops `trusted/timeout/throttle/counter` for non-system-admins, and `cron update` silently discards `name`/`expr` (its update struct is `{branch,target,disabled}` only) *and* ignores JSON decode errors entirely. A CLI that reports success there is lying on the server's behalf.
- [ ] **Guard the vendor's silent semantic traps.** Drone uses robfig/cron **v1, which is seconds-first**: a standard crontab `"0 3 * * *"` parses fine and means `second=0 minute=3 hour=*` → **fires hourly at :03**, not daily at 03:00. Silent, invisible, 24× wrong. Detect the 5-field form, warn, offer the seconds-prefixed fix, and **always print the next N fire times before creating** — something the API cannot do at all, since `next` is only computed after persist.

### Secrets (a surface opcli never had)

- [ ] **Redact before `--dry-run` prints the body**, or the one safety feature becomes the leak.
- [ ] **`--fields` needs a denylist** — `_dotted_get` walks to any key present, so `--fields data` would resurrect a value the serializer omitted.
- [ ] **`--raw` is refused on secret endpoints.**
- [ ] **Do not depend on the server blanking the value** — omit it in the serializer. (Drone genuinely never returns `data`: a secret GET returns `{id, repo_id, name}` **[live]**, and the published docs showing `"data": "octocat"` in a response are simply wrong.)
- [ ] **Write-only values surface as `{"data": null, "note": "values are write-only"}`**, never `""` — an agent must not conclude the secret is blank.
- [ ] **Never take a secret value as argv by default** — `ps` and shell history. `--from-stdin`/`--from-file`/`--from-env` and a no-echo prompt are the defaults.
- [ ] **Put `redact: bool` in the registry**, not in ad-hoc call-site checks, so it cannot be forgotten when a new command reuses the layer.

### The known gap: caching

`resolve.py` has **zero** caching. `wp create --project webshop --type Bug --status New --priority High --assignee me --responsible alex` paginates the projects collection **three times** in one command. It survives because each invocation is a short-lived process, but it is latency an agent pays on every write — and the ad-hoc caches that grew inside command modules instead — `user_cache`/`project_cache` in `costs.py`, `name_cache` in `comments.py` — prove the need was felt; the fix just never got hoisted. **Build it in from day one.** The Client is constructed once per process and is the natural home:

```python
def collect_cached(self, path, **kw):
    key = (path, tuple(sorted((kw.get("params") or {}).items())))
    if key not in self._coll_cache:
        self._coll_cache[key] = self.collect(path, **kw)
    return self._coll_cache[key]
```

Six lines, and it matters more the larger the enumerable collections are (`GET /api/user/repos` returns every repo the user can see — hundreds — versus OpenProject's ~20 statuses).

---

## 7. Per-tool checklist

### 7.1 The decision table — settle these before writing a line

| # | Decision | How to get it right |
|---|---|---|
| 1 | **Command name & PATH collisions** | Run `which <candidate>`. Refuse the vendor's own binary name (Drone ships `drone` → take `dronectl`). Document the refusal in `pyproject.toml` **and** the README; suggest an alias. Ship exactly one console script. |
| 2 | **Distribution / import names** | `agent-tool-<x>-cli` / `<x>cli`. End the description with *"Installs the `<command>` command."* Check the import name is free as a top-level package. |
| 3 | **Env prefix — a decision, not a rename** | Split it in two. **Adopt the ecosystem's names for auth** (`DRONE_SERVER`, `DRONE_TOKEN` — what the official CLI reads and every tutorial exports). **Namespace everything you invent** (`DRONECLI_CONFIG_DIR`, `DRONECLI_FORMAT`, `DRONECLI_DRY_RUN`…). Critical when the tool runs *inside* the product: Drone injects `DRONE_REPO`, `DRONE_BUILD_NUMBER`, `DRONE_BRANCH` into every pipeline step, so a naive `OPCLI_*`→`DRONE_*` translation lets a pipeline-injected variable silently steer the CLI. |
| 4 | **The reserved global flag set** | Freeze it now: popped flags **+ every root option** (`--version`/`-V` included). Grep the intended surface for collisions. Write the assertion test first. Standardise file destinations on `--to PATH`. |
| 5 | **Config dir / keyring service / app dir** | `<X>_CONFIG_DIR` > `XDG_CONFIG_HOME` > `~/.config`, then `/<x>-cli/`. All three live in `paths.py`. `config_dir()` is a **function**. |
| 6 | **Exit-code extensions** | Keep 6 for real 409s, or leave it unallocated. Never renumber 0–7/130. Extensions ≥8 only for an **observed** condition. If the domain has outcomes worth gating on, put them behind `--exit-code` in a non-colliding band (20–29). |
| 7 | **Context keys** | Must match **option** names, not positionals. If the vendor's convention is positional (`drone build ls octocat/hello-world`), declare an option *plus* an optional leading positional that falls back to it. Do not relax the option-filter. Add the `KNOWN_KEYS`↔options test. Decide what is deliberately **not** sticky (a pinned build number ages into a footgun; a sticky `--status failure` hides passing builds). |
| 8 | **Wire-format adapter (`hal.py`)** | What does it hide? HAL links/embeds/Formattables → keep it. Plain JSON → delete it; `serialize.py` becomes projection + renaming (but still exists, and grows derived fields). Either way `output.py` must never learn the wire format. |
| 9 | **Time units (`duration.py`)** | Epoch ints, ISO durations, or seconds? Normalize **in the serializer**, never in a renderer. Add the derived field the API refuses to hold (`duration_seconds`). |
| 10 | **Pagination invariant** | Authoritative `total`, or the short-page heuristic? Verify against a live server, not the docs. Find the real per-page cap, **per handler**. Write the comment. Write the >1-page test. |
| 11 | **Auth scheme & the anonymous surface** | `BasicAuth(literal, token)` vs `Authorization: Bearer` — one line in `Client.__init__`. Then the second question: **is there an unauthenticated read path?** If yes (Drone without `DRONE_SERVER_PRIVATE_MODE`), only the identity endpoint is a valid token probe, and your 403 tests need a second, non-admin actor. |
| 12 | **Credential shape** | `credentials.py` stores exactly **one opaque string**. Jira Cloud needs `email:api_token`; Nextcloud uses app-passwords; GitLab has PAT *and* OAuth-with-refresh. If the credential is multi-part or **expires**, that is net-new design in a module tagged VERBATIM — budget it. |
| 13 | **Write shape** | PATCH vs PUT vs full-replace; ops-arrays (Jira's `{"fields":…}` + `{"update":[…]}`); idempotency keys. Optimistic locking (keep exit 6 and `update_locked`) or last-write-wins (delete both, keep the deep-copy-per-attempt lesson). |
| 14 | **Rate limits & concurrency budget** | Does the vendor throttle (Jira Cloud, GitLab: aggressively)? Is there a fan-out command (`build ls --stages` is one GET per row)? Then you need a bounded pool and progress, not a naive loop — and `collect_cached` from day one. |
| 15 | **Discovery surface** | Live schema endpoint (left-join a static registry onto it), or static-only? If static-only, the registry is the **sole** discoverability source: ship the `fields`/`operators`/`values` trio anyway, reading from the registry, and guard it with tests. |
| 16 | **Streaming shape** | NDJSON records, raw text lines, or both? Is there a *follow* (tail-until-terminal) use case? Net-new design, not a port. |
| 17 | **Redaction** | Does any resource carry a credential? If yes, work the Secrets checklist in §6 — none of it has an analogue in opcli. |
| 18 | **The killer feature** | See §7.3. Decide it *from* `API_NOTES.md`'s negative results, before Phase 1. |

### 7.2 Ordered stand-up

1. **Research first, code second.** Write `docs/API_NOTES.md` before any source: one `# AREA:` per domain, the five headings, liberal **"Open questions (verify live)"**, then the `# COMPLETENESS CRITIQUE`. Record **negative results** especially — they are the most expensive to rediscover and usually where the killer feature hides.
2. **Spike the open questions against a real server.** Half a day. Not a gate to argue about — a step. Reading source is not observation; the Drone research was source-accurate to the line number and still wrong about the two facts that would have shaped the most code.
3. **Make the §7.1 decisions.** Write them into the README's Compatibility + Known Limitations sections *now*, while they're fresh.
4. **Scaffold the chassis.** `pyproject.toml` (`dynamic = ["version"]`), `paths.py`, `errors.py`, `output.py`, `duration.py`, `credentials.py`, `config.py`, `appctx.py`. All VERBATIM modulo names. Do not touch the domain yet.
5. **Stand up `cli.py`** with `_pop_globals`, `main()`, the error funnel, the Typer hygiene flags, and **the reserved-namespace test**. Add `guide` as a stub with a real OVERVIEW skeleton, and `install claude` — they cost an hour and shape everything after.
6. **`client.py`.** Copy the retry matrix + backoff + dry-run + error funnel verbatim; write the auth line, `api_root`, the error-body parser, the pagination loop and `collect_cached`. **Port `test_client_retry.py` in the same commit** — the four-corner matrix is the spec.
7. **`docker-compose.yml` + seed + token bootstrap + `make up && make env && make test`.** Before the first domain command. Everything after is faster when a real server is one command away.
8. **First domain slice, end to end:** `serialize.py` for one resource → `resolve.py` for its refs → one `commands/<thing>.py` with `list`/`get`/`create` → live integration tests. Prove the vertical before widening.
9. **`context` + `settings` + `auth`.** VERBATIM. Add the `KNOWN_KEYS`↔options test.
10. **The killer feature.** Early — it is the product, and it tells you what the chassis is missing.
11. **Widen the domain.** Each new group is ~15 lines per command and inherits `--dry-run`/`--fields`/`--stream`/`--format`/context for free.
12. **The discovery trio** (`fields`/`operators`/`values`) and the `<x>spec.py` registry, with guard tests.
13. **Fill in `guide` prose + `SKILL.md`.** Both now have something real to describe. Add the content-contract tests.
14. **`gen_docs.py` + the `git diff --exit-code` CI step + README/USAGE/AGENTS.**
15. **`release.yml` + Trusted Publishing + PyInstaller matrix + the compat/provider matrix.**
16. **Tag `v0.1.0`.** Watch the binary jobs attach independently.

### 7.3 The killer feature: **the Refused Number**

The most important idea in this document.

> **Find the number — or the answer — the API refuses to give, and derive it from the primitives the API does expose.** Take the missing dimension from local config if you must, aggregate client-side, and be conspicuously honest about the seams.

Every API has one. It is the thing the vendor's own product cannot tell you, which is why it is worth building and why nobody has built it. **You find it in `API_NOTES.md`'s negative results**, by asking: *"what does the user actually want that this API refuses to compute?"*

**OpenProject: no rates, no cost-report endpoints → `cost report`.** The reason opcli exists commercially. API v3 exposes **no** hourly rates and **no** cost-report endpoints, so the only reliable per-person figures come from summing `time_entries` client-side. The command pulls the bookings for a period, groups by person and project, sums hours, and — if you supply a rate table — multiplies. The honesty is structural: `"billable": rate_table is not None`, `amount: null` when a rate is missing, and a docstring citing the research so a future maintainer does not "fix" it by hunting for the cost endpoint that does not exist. Its `--detailed` export goes further — one row per entry with the resource's custom fields as columns, discovered from a schema endpoint the vendor's own UI ignores — **doing what the vendor's product cannot**. Three defensive touches earn their keep: probe the schema from an actual entry's project (there is no global list); `except OpError: return {}` so a permissions hiccup costs you the CF columns, not the invoice; and filter columns to names present in ≥1 row, so a spreadsheet never gets a header with nothing under it.

**Drone: no completion signal, no durations → `build wait` and `build stats`.** Two refusals, two features, same move.

- *No completion signal.* `POST /builds` returns `{"status":"pending"}`; there is no webhook a CLI can subscribe to and no long-poll. Without a wait primitive an agent cannot use Drone at all. Two **real, undocumented** SSE streams exist **[live]**: `GET /api/stream` (a global feed — `: ping`, then `data: {repo json}` on state changes) and `GET /api/stream/{o}/{n}/{b}/{stage}/{step}` (a live log tail, terminating `event: error` / `data: eof` — **that is the NORMAL end-of-stream marker**, and a naive SSE client reports failure on every successful stream; pin it in a unit test). A **running** step replays its buffered history to a new subscriber, so a REST-backfill + SSE-follow design must dedupe on `pos` — the overlap is guaranteed, not incidental; only a *finished* step yields an immediate eof **[src]**. Keep a poll fallback for proxies that buffer SSE.
- *No duration field anywhere.* Everything is derived from `started`/`finished` epochs: per-build wall time, queue latency (`started - created`), per-step durations, p50/p95, success rate. Flake detection falls out free — `restart` mints a **new build number** (restarting build 1 produced build 2 **[live]**) against the same commit SHA, so one commit with builds of differing status **is** a flake, computable client-side and rankable by step. Nothing upstream does this.

Two rules for the terminal predicate, because this is where the feature gets subtly wrong:

- **Copy the vendor's `IsDone()` and then deliberately deviate.** Drone's is "not done while `waiting_on_dependencies|pending|running|blocked`" **[src]** — copy it *exactly* and `wait` hangs until timeout on every approval-gated pipeline, which is precisely the bug the feature exists to prevent. Terminal = `IsDone() || status == "blocked"`, with `blocked` reported as its own outcome (*"build 42 is blocked on stage 2 awaiting approval — run `build approve 42 --stage 2`"*).
- **A bounded, named failure beats an unbounded wait.** `--timeout 30m` → a distinct exit in the `--exit-code` band, so a runner-less or stuck build is diagnosable rather than mysterious.

**The second half is always the export.** `cost report --detailed -o csv` (one row per entry) and its Drone twin `build report --detailed -o csv` (one row per step, with the duration nobody else computes) are what make the CSV renderer's hard-won lessons — union-of-keys header, the coercer emitting `true/false` and embedded JSON so pandas parses it — pay for themselves. Ship it, or those lessons travel as dead prose.

### 7.4 What "done" looks like

- `pytest` on a laptop with no Docker: green, integration skipped with a reason naming the two env vars.
- `<tool> guide` with **no** env vars and a nonexistent config dir: exit 0.
- `<tool> <group> list | jq` from a fresh install, first run, in a terminal: valid JSON.
- `<tool> <group> create … --dry-run`: prints a request with **real resolved ids**, exits 0, creates nothing.
- `<tool> <group> get 99999999`: JSON on stderr, exit 5.
- `<tool> <group> list --format markdown` (flag **after** the subcommand): works.
- The reserved-namespace test, the `KNOWN_KEYS` test, and `git diff --exit-code docs/` all pass.
- `pipx install` the wheel from PyPI, then `auth login`: the token reaches the OS keyring.

---

## 8. Appendix: fixes to make in v2

Where `agent-tool-openproject-cli` is scar tissue. Heal these; do not copy them.

| # | Defect in opcli | Fix |
|---|---|---|
| 1 | `attach download --output` was eaten by `_pop_globals` → format degraded silently → the file landed in CWD, exit 0. `-O` was the half-fix scar. **Fixed 2026-07-16**: renamed `--out`, + `tests/test_globals_unit.py` asserts the whole tree. | Reserve the global set first; add the command-tree collision test (§3.2). Standardise on `--to PATH`. Note the test must reserve **only the popped flags** — including root options flags four working commands. |
| 2 | `_resolve_format` (`context.py:39-44`) swallows a bad **explicit** `--format` with `except ValueError: pass` and degrades to json. | `raise OpError(str(exc))` on the explicit rung; stay lenient on env/config. |
| 3 | `KNOWN_KEYS` (`commands/context.py:22`) is defined once and imported **nowhere** — grep returns one hit, its own definition. Drift is silent. | Drive `context set`'s options from it; add the KNOWN_KEYS↔options test. |
| 4 | `Emitter.message()` (`output.py:124`) guards with a denylist (`!= json`) — it would corrupt csv and markdown; unreachable only by luck. | Allowlist: `== OutputFormat.table`. |
| 5 | `resolve.py` has zero caching; the projects collection is fetched 3× in one `wp create`. | `Client.collect_cached()`, six lines, from day one. |
| 6 | Version hardcoded in **both** `pyproject.toml` and `__init__.py`; the test can only catch the half that cannot break. Ship 0.5.0 to PyPI that reports 0.4.0 in its User-Agent and in the skill it writes to users' machines. | `dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr = "<x>cli.__version__"}`. Keep the auth-stripped `--version` subprocess test — the auth-stripping is the valuable part. |
| 7 | `gen_docs.py` runs in neither CI nor the Makefile; "docs never drift" is aspirational. | `python scripts/gen_docs.py && git diff --exit-code docs/` in the `unit` job. |
| 8 | Three unrelated "context" meanings collide (`typer.Context`, the `AppContext` DI container in `context.py`, the sticky context in `commands/context.py`); `cli.py` imports two side by side, one forced to alias. | Rename the DI container to **`appctx.py`**. Keep `commands/context.py` for the user feature. Cheap at scaffold time, annoying later. |
| 9 | `credentials.py` duplicates `config_dir()` verbatim to stay a leaf; two identical functions that will silently diverge. | Extract **`paths.py`** (stdlib-only leaf), imported by both. |
| 10 | PyInstaller flags exist twice — `build_binary.sh` and inline PowerShell in `release.yml`'s Windows job, hand-synced, because a bash script won't run on `windows-latest`. | Move the flag list into `scripts/build_binary.py` (or a `.spec`) that both platforms invoke. |
| 11 | README claimed "56 tests"; there were 229. **Fixed 2026-07-16** — the Contributing rewrite dropped the bare count. | Don't put counts in prose, or generate them. **Never state a count you have not counted, and always name the basis.** The instructive part is how the drafts of *this* document got it wrong: "243 tests" and "140 tests" were both real numbers straight out of pytest (243 collected; 140 under `-m "not integration"`) — the error was the **label**, not the arithmetic. "140 FakeClient tests" was wrong because `FakeClient` is imported by exactly two files (~33 tests); those 140 were simply the hermetic cases. A number with the wrong noun attached is worse than no number: it survives review precisely because it *is* verifiable. **And counts rot the moment you touch the suite**: adding one test file moved every number in this document (229→233 functions, 243→247 collected, 140→144 hermetic). If a count must appear, generate it or omit it. |
| 12 | **POST used as a read** — `/form` endpoints. The dry-run write gate intercepts them, so `--dry-run` aborts at resolution instead of printing its request. Not one site but **five**: `resolve.py`, `costs.py`, `workpackages.py` (`wp schema`), `custom_fields.py`, `search.py`. Real, unexercised. | If your API has form/validate endpoints, either allowlist them in the dry-run gate or route them through a method the gate ignores. Audit *every* call site — the pattern spreads quietly, because a form-POST reads like a read at the call site. |
| 13 | `--profile`/`--no-color` are position-sensitive while the popped globals are not — an undocumented asymmetry. | Pop them too (one rule: "globals work anywhere"), and widen the reserved set to match. |
| 14 | The `Makefile` is tagged VERBATIM in the drafts and is not: its `env`, `token` and `seed` targets are entirely OpenProject-specific (`get_admin_token.sh` twice, `OPCLI_SECOND_TOKEN`), and `test-unit` runs `pytest tests/test_unit.py` rather than `-m "not integration"`. | Retag PARAMETERIZED; fix `test-unit` to select by marker. A ledger that mislabels the one file every implementer opens first makes the whole taxonomy untrustworthy. |
