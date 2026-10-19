#!/usr/bin/env python3
"""End-to-end check for the NPS Orchestrator.

One command, real code paths, explicit pass/fail. Anything that needs live cloud
credentials is reported as a SKIP with the reason rather than silently omitted -
an honest "not covered" is more useful than a green run that proves less than it
appears to.

    python bin/e2e.py

Exit code 0 only if every check passed.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = [], [], []


def check(name):
    """Register a check. The function returns a detail string, or raises."""
    def wrap(fn):
        try:
            detail = fn()
        except _Skip as exc:
            SKIP.append((name, str(exc)))
        except Exception as exc:  # noqa: BLE001 - the harness reports, never crashes
            FAIL.append((name, f"{type(exc).__name__}: {exc}", traceback.format_exc()))
        else:
            PASS.append((name, detail or ""))
        return fn
    return wrap


# Binary files cannot be grepped, so each one listed here has been opened
# and looked at. Adding a path to this list asserts that someone did.
REVIEWED_BINARIES = [
]

class _Skip(Exception):
    pass


# --------------------------------------------------------------------------
# 1. Dependencies
# --------------------------------------------------------------------------

@check("dependencies importable")
def _deps():
    missing = []
    for mod in ("requests", "google.adk", "google.cloud.secretmanager"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise RuntimeError(
            f"missing: {', '.join(missing)} - run: pip install -r requirements.txt"
        )
    return "requests, google.adk, google.cloud.secretmanager"


if FAIL:
    print("Cannot continue: dependencies are not installed.")
    for name, msg, _ in FAIL:
        print(f"  FAIL  {name}: {msg}")
    sys.exit(1)


# --------------------------------------------------------------------------
# 2. ADK agent contract
# --------------------------------------------------------------------------

@check("deployed agent satisfies the ADK contract")
def _agent_contract():
    from app import agent_direct

    agent = agent_direct.root_agent
    assert agent is not None, "root_agent is None"
    assert agent.name == "nps_orchestrator", f"unexpected agent name: {agent.name}"
    tools = list(agent_direct.tools)
    assert len(tools) >= 20, f"expected >=20 tools, found {len(tools)}"
    names = [getattr(t, "name", None) or t.func.__name__ for t in tools]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate tool names: {dupes}"
    assert "batch_search_sentry_errors" in names, "batch search tool is not registered"
    return f"{len(tools)} tools, all uniquely named"


@check("disabled Sentry MCP search tools stay unregistered")
def _disabled_tools():
    from app import agent_direct

    names = [
        getattr(t, "name", None) or t.func.__name__ for t in agent_direct.tools
    ]
    for banned in ("search_issues", "search_events"):
        assert banned not in names, f"{banned} is registered but returns 401 upstream"
    return "search_issues and search_events absent, as intended"


# --------------------------------------------------------------------------
# 3. SQL safety - tool arguments come from an LLM, so they are untrusted
# --------------------------------------------------------------------------

@check("LIMIT is clamped, never interpolated raw")
def _clamp():
    from app.databricks_client import clamp_row_limit

    cases = {-5: 1, 0: 1, 50: 50, 10**9: 1000, None: 100, "abc": 100, "25": 25}
    for given, want in cases.items():
        got = clamp_row_limit(given)
        assert got == want, f"clamp_row_limit({given!r}) == {got}, expected {want}"
    return f"{len(cases)} boundary cases"


@check("identifiers are bound as parameters, not interpolated")
def _sql_binding():
    from app import databricks_tools

    captured = {}

    class FakeClient:
        def query(self, sql, parameters=None, **kw):
            captured["sql"] = sql
            captured["parameters"] = parameters
            return []

    databricks_tools._databricks_client = FakeClient()  # module-level singleton
    hostile = "1' OR '1'='1"
    out = json.loads(databricks_tools.query_nps_by_user(account_id=hostile, limit=10))
    assert out["success"] is True, out

    sql, params = captured["sql"], captured["parameters"]
    assert hostile not in sql, "hostile value was interpolated into the SQL text"
    assert ":account_id" in sql, "named marker missing from SQL"
    assert params == {"account_id": hostile}, f"unexpected bound params: {params}"
    return "injection payload stayed in the parameter map"


@check("date and score filters are bound too")
def _client_binding():
    from app.databricks_client import DatabricksClient

    captured = {}

    def fake_query(self, sql, timeout_seconds=300, row_limit=None, parameters=None):
        captured["sql"] = sql
        captured["parameters"] = parameters
        return []

    original = DatabricksClient.query
    DatabricksClient.query = fake_query
    try:
        client = DatabricksClient.__new__(DatabricksClient)  # no network, no secrets
        client.query_nps_responses(start_date="2026-01-01", score_filter=3, limit=5)
    finally:
        DatabricksClient.query = original

    sql, params = captured["sql"], captured["parameters"]
    assert ":start_date" in sql and ":score_filter" in sql, sql
    assert "2026-01-01" not in sql, "date was interpolated instead of bound"
    assert params == {"start_date": "2026-01-01", "score_filter": 3}, params
    assert "LIMIT 5" in sql, "clamped limit missing"
    return "start_date and score_filter bound; LIMIT clamped inline"


# --------------------------------------------------------------------------
# 4. The batch search - the fix this project exists to demonstrate
# --------------------------------------------------------------------------

@check("batch search collapses N lookups into one request")
def _batch():
    from app import agent_direct

    calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [
                {
                    "id": "E1",
                    "title": "boom",
                    "culprit": "x",
                    "level": "error",
                    "count": 3,
                    "userCount": 1,
                    "firstSeen": "2026-01-01T00:00:00Z",
                    "lastSeen": "2026-01-02T00:00:00Z",
                    "permalink": "https://sentry.io/x",
                    "tags": [{"key": "account-id", "value": "456"}],
                }
            ]

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return FakeResponse()

    original = agent_direct.requests.get
    agent_direct.requests.get = fake_get
    try:
        raw = agent_direct.batch_search_sentry_errors(
            account_ids=["123", "456", "789"], stats_period="7d"
        )
    finally:
        agent_direct.requests.get = original

    out = json.loads(raw)
    assert len(calls) == 1, f"expected 1 HTTP request for 3 IDs, made {len(calls)}"
    query = calls[0]["params"]["query"]
    for acct in ("123", "456", "789"):
        assert f"account-id:{acct}" in query, f"{acct} missing from OR query: {query}"
    assert query.count(" OR ") == 2, f"IDs not OR-joined: {query}"
    assert out["summary"]["total_ids_searched"] == 3, out["summary"]
    grouped = {g["identifier"]["value"] for g in out["results_by_identifier"]}
    assert "456" in grouped, f"returned issue was not regrouped by its tag: {grouped}"
    return "3 IDs -> 1 request; results regrouped client-side"


@check("single-ID search refuses start/end together with stats_period")
def _window():
    from app import agent_direct

    calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        return FakeResponse()

    original = agent_direct.requests.get
    agent_direct.requests.get = fake_get
    try:
        agent_direct.search_sentry_errors(
            account_id="123", start_date="2026-01-01", end_date="2026-01-31"
        )
    finally:
        agent_direct.requests.get = original

    params = calls[0]
    assert "statsPeriod" not in params, (
        "statsPeriod sent alongside explicit start/end - Sentry rejects both at once"
    )
    return "explicit window wins over statsPeriod"


# --------------------------------------------------------------------------
# 5. Local-dev agent still loads (it is what `adk web` serves)
# --------------------------------------------------------------------------

@check("local-dev agent module imports")
def _local_agent():
    try:
        from app import agent
    except Exception as exc:  # noqa: BLE001
        raise _Skip(
            f"app/agent.py needs ADK MCP extras and a reachable MCP server ({exc})"
        ) from exc
    assert agent.root_agent is not None
    return f"root_agent = {agent.root_agent.name}"


# --------------------------------------------------------------------------
# Not covered here - stated explicitly rather than omitted
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Pre-publication leak gate (shared; see bin/leakcheck.py)
# --------------------------------------------------------------------------

@check("no employer identifiers in the working tree")
def _leak_tree():
    sys.path.insert(0, str(ROOT / "bin"))
    import leakcheck

    hits = leakcheck.scan_tree(ROOT, self_path=Path(leakcheck.__file__))
    assert not hits, "\n  " + "\n  ".join(hits[:12])
    return "clean, lockfiles included"


@check("no employer identifiers anywhere in git history")
def _leak_history():
    sys.path.insert(0, str(ROOT / "bin"))
    import leakcheck

    if not (ROOT / ".git").exists():
        raise _Skip("not a git repository")
    hits = leakcheck.scan_history(ROOT)
    assert not hits, (
        "a fresh history is only clean if nothing was committed and then removed:\n  "
        + "\n  ".join(hits[:12])
    )
    return "clean across all commits"


@check("every binary file has been reviewed by eye")
def _binaries():
    sys.path.insert(0, str(ROOT / "bin"))
    import leakcheck

    found = set(leakcheck.binaries(ROOT))
    allowed = set(REVIEWED_BINARIES)
    unreviewed = sorted(found - allowed)
    stale = sorted(allowed - found)
    assert not unreviewed, (
        f"these cannot be grepped and are not on the reviewed list, so nobody has "
        f"confirmed what they show: {unreviewed}"
    )
    assert not stale, f"REVIEWED_BINARIES lists files that no longer exist: {stale}"
    return f"{len(found)} binaries, all reviewed"


UNCOVERED = [
    ("Databricks SQL round trip", "needs warehouse URL, warehouse ID and OAuth client creds in Secret Manager"),
    ("Secret Manager credential fetch", "needs GCP ADC and secretmanager.versions.access"),
    ("Sentry REST round trip", "needs a real SENTRY_AUTH_TOKEN and org/project slugs"),
    ("Datadog + Sentry MCP servers", "needs the two Cloud Run services reachable"),
    ("Agent Engine deploy", "needs aiplatform, a staging bucket, and mints a billable engine"),
    ("Model behaviour (does it pick the batch tool?)", "needs a live Gemini call; assert via an eval set, not here"),
]


def main() -> int:
    for name, reason in UNCOVERED:
        SKIP.append((name, reason))

    width = max(len(n) for n, *_ in PASS + FAIL + SKIP)
    for name, detail in PASS:
        print(f"PASS  {name:<{width}}  {detail}")
    for name, reason in SKIP:
        print(f"SKIP  {name:<{width}}  {reason}")
    for name, msg, tb in FAIL:
        print(f"FAIL  {name:<{width}}  {msg}")
        print("".join("        " + line for line in tb.splitlines(keepends=True)))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} skipped")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
