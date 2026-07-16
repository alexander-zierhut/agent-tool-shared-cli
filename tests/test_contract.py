"""The agent contract itself.

These are the tests that must never be allowed to "just" change. An agent learns
this contract from one tool and applies it to every other, so a drift here is a
silent, cross-tool behaviour change — not a local one.
"""

from __future__ import annotations

import json

import pytest

from agentcli import (
    ApiError,
    AuthError,
    ConfigError,
    ConflictError,
    DryRun,
    Emitter,
    NotFoundError,
    OpError,
    OutputFormat,
    ValidationError,
)


# ---- exit codes are published API ------------------------------------

@pytest.mark.parametrize(
    "exc,code",
    [
        (OpError("x"), 1),
        (ConfigError("x"), 3),
        (AuthError("x"), 4),
        (NotFoundError("x"), 5),
        (ConflictError("x"), 6),
        (ValidationError("x"), 7),
    ],
)
def test_exit_codes_are_stable(exc, code):
    """You may leave a code unallocated or repurpose one deliberately.

    You may NEVER renumber one — these are documented in three places (README,
    the in-binary guide, and the Claude skill) and agents branch on them.
    """
    assert exc.exit_code == code


def test_exit_code_2_is_never_allocated():
    """2 belongs to Click/Typer (usage error). Claiming it makes a usage error
    indistinguishable from an application error."""
    codes = {e.exit_code for e in (OpError("x"), ConfigError("x"), AuthError("x"),
                                   NotFoundError("x"), ConflictError("x"), ValidationError("x"))}
    assert 2 not in codes


def test_dry_run_does_not_inherit_operror():
    """DryRun is control flow, not failure.

    If it inherited OpError the central error funnel would catch it and a dry run
    would exit non-zero — turning a preview into an error.
    """
    assert not issubclass(DryRun, OpError)


def test_error_serializes_to_the_documented_shape():
    assert OpError("boom").to_dict() == {"error": "boom"}
    assert OpError("boom", detail={"k": 1}).to_dict() == {"error": "boom", "detail": {"k": 1}}


def test_api_error_carries_status():
    assert ApiError("nope", status=404).to_dict()["status"] == 404


def test_validation_error_surfaces_field_errors():
    """The highest-value line in errors.py: a 422 as an opaque blob ends an
    agent's turn; as fieldErrors it gets fixed on the next call."""
    e = ValidationError("invalid", field_errors=["Subject can't be blank"])
    assert e.to_dict()["fieldErrors"] == ["Subject can't be blank"]


# ---- stdout is a machine channel -------------------------------------

def test_json_is_the_default_and_parses(capsys):
    Emitter(OutputFormat.json).emit({"a": 1})
    assert json.loads(capsys.readouterr().out) == {"a": 1}


def test_errors_go_to_stderr_not_stdout(capsys):
    from agentcli import print_error

    print_error(NotFoundError("gone"), OutputFormat.json)
    cap = capsys.readouterr()
    assert cap.out == "", "stdout must stay parseable — errors belong on stderr"
    assert json.loads(cap.err)["error"] == "gone"


@pytest.mark.parametrize("fmt", [OutputFormat.json, OutputFormat.csv, OutputFormat.markdown])
def test_message_never_pollutes_a_machine_format(capsys, fmt):
    """Allowlist, not denylist.

    opcli shipped `if self.fmt != OutputFormat.json`, which let prose print into
    csv and markdown and corrupt both. Only the one human format may carry it.
    """
    Emitter(fmt).message("TOTAL: 42h")
    assert capsys.readouterr().out == ""


def test_message_prints_in_table_mode(capsys):
    Emitter(OutputFormat.table).message("TOTAL: 42h")
    assert "TOTAL" in capsys.readouterr().out


def test_coerce_accepts_the_documented_aliases():
    assert OutputFormat.coerce("md") is OutputFormat.markdown
    assert OutputFormat.coerce("json") is OutputFormat.json
    with pytest.raises(ValueError):
        OutputFormat.coerce("/tmp/some/path.pdf")  # the --output collision, from v0.4.1


# ---- --fields projection ---------------------------------------------

def test_fields_projection_supports_dotted_paths(capsys):
    Emitter(OutputFormat.json, fields=["id", "assignee.name"]).emit(
        [{"id": 1, "subject": "x", "assignee": {"name": "jane", "id": 9}}]
    )
    assert json.loads(capsys.readouterr().out) == [{"id": 1, "assignee.name": "jane"}]


def test_stream_emits_ndjson(capsys):
    n = Emitter(OutputFormat.json, stream=True).stream_json([{"a": 1}, {"a": 2}])
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert n == 2
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"a": 2}]
