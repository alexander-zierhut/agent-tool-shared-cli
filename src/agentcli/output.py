"""Rendering of command results.

The CLI is *agent-first*, so JSON is the default output: stable, complete, and
trivial to parse. A ``table`` mode (Rich) exists for humans. Commands hand the
formatter the raw data plus an optional column spec; the formatter decides how
to present it based on the active ``--output`` mode.
"""

from __future__ import annotations

import csv as csvlib
import dataclasses
import enum
import json as jsonlib
import sys
from typing import Any, Callable, Iterable, Sequence

from rich.console import Console
from rich.table import Table

# A column is (header, accessor) — accessor is a dict key or a callable(row)->value —
# or just a bare string, which means "use this key, and title-case nothing".
#
# Bare strings are accepted because they are what everyone reaches for, and the
# type alias alone did not stop them: three of Drone's command modules pass
# `columns=["number", "status", …]`, and every one of them raised
# `ValueError: too many values to unpack` under `-o table`, `-o csv` and
# `-o markdown` while working fine under the default json. Nothing caught it —
# json is the default, so the crash only ever reached a human who asked for a
# table, and the tests only exercised the argv layer above this one.
#
# The lesson is the same as the reserved-flags bug: an API that is easy to hold
# wrong will be held wrong. Widening the input here fixes every caller in the
# family at once and cannot break a correct one.
Column = "str | tuple[str, str | Callable[[dict], Any]]"


def _normalise_columns(columns: Sequence[Any] | None) -> list[tuple[str, Any]] | None:
    """Accept bare strings alongside (header, accessor) pairs.

    Done once, at the entry point, rather than at each of the three unpack sites:
    a normaliser per format is three chances to miss one, and the one missed is
    whichever format nobody tried.
    """
    if columns is None:
        return None
    out: list[tuple[str, Any]] = []
    for col in columns:
        if isinstance(col, str):
            out.append((col, col))
        else:
            header, accessor = col  # a 2-tuple; anything else is a real bug worth raising on
            out.append((header, accessor))
    return out

_err_console = Console(stderr=True)


class OutputFormat(str, enum.Enum):
    json = "json"
    table = "table"
    markdown = "markdown"
    csv = "csv"

    @classmethod
    def coerce(cls, value: "str | OutputFormat | None") -> "OutputFormat | None":
        """Parse a loose string (accepts 'md' for markdown). None passes through."""
        if value is None or isinstance(value, cls):
            return value
        v = str(value).strip().lower()
        if v in ("md", "markdown"):
            return cls.markdown
        if v in ("json", "j"):
            return cls.json
        if v in ("table", "tbl", "t"):
            return cls.table
        if v in ("csv",):
            return cls.csv
        raise ValueError(f"unknown output format '{value}' (choose json, table, markdown, or csv)")


def _accessor_value(row: dict, accessor: "str | Callable[[dict], Any]") -> Any:
    if callable(accessor):
        try:
            return accessor(row)
        except Exception:
            return None
    return row.get(accessor)


def _fmt_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


class Emitter:
    def __init__(
        self,
        fmt: OutputFormat = OutputFormat.json,
        *,
        color: bool = True,
        fields: Sequence[str] | None = None,
        stream: bool = False,
    ):
        self.fmt = fmt
        self.console = Console(no_color=not color, highlight=False)
        # user-selected fields (dotted paths ok, e.g. "assignee.name"); None = command defaults
        self.fields = [f.strip() for f in fields if f.strip()] if fields else None
        self.stream = stream

    # ---- main entry --------------------------------------------------
    def emit(
        self,
        data: Any,
        *,
        columns: Sequence[Column] | None = None,
        title: str | None = None,
        empty: str = "(no results)",
    ) -> None:
        columns = _normalise_columns(columns)

        if self.fields:
            # --fields overrides both the JSON shape and the table/markdown columns
            if self.fmt == OutputFormat.json:
                self._emit_json(_project(data, self.fields))
                return
            columns = [(f, (lambda r, _f=f: _dotted_get(r, _f))) for f in self.fields]

        if self.fmt == OutputFormat.json:
            self._emit_json(data)
            return
        if self.fmt == OutputFormat.csv:
            self._emit_csv(data, columns=columns)
            return
        if self.fmt == OutputFormat.markdown:
            self._emit_markdown(data, columns=columns, title=title, empty=empty)
            return
        self._emit_table(data, columns=columns, title=title, empty=empty)

    def stream_json(self, items: Iterable[Any]) -> int:
        """Emit an iterable as NDJSON — one JSON object per line, flushed as it
        arrives. Honours --fields. Returns the number of items written."""
        n = 0
        for it in items:
            obj = {f: _dotted_get(it, f) for f in self.fields} if (self.fields and isinstance(it, dict)) else it
            sys.stdout.write(jsonlib.dumps(_jsonable(obj), ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()
            n += 1
        return n

    def message(self, text: str) -> None:
        """A human status line (table mode only; suppressed in every machine format).

        Allowlist, not denylist: `!= json` would also let this print into csv and
        markdown, corrupting both. stdout is a machine channel (see AGENTS.md) —
        only the one human-facing format may carry prose.
        """
        if self.fmt == OutputFormat.table:
            self.console.print(text)

    # ---- json --------------------------------------------------------
    def _emit_json(self, data: Any) -> None:
        sys.stdout.write(jsonlib.dumps(_jsonable(data), indent=2, ensure_ascii=False, default=str))
        sys.stdout.write("\n")

    # ---- table -------------------------------------------------------
    def _emit_table(
        self,
        data: Any,
        *,
        columns: Sequence[Column] | None,
        title: str | None,
        empty: str,
    ) -> None:
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else None
        if rows is None:
            self.console.print(str(data))
            return
        if not rows:
            self.console.print(f"[dim]{empty}[/dim]")
            return

        if columns is None:
            # key/value table for a single object, else fall back to JSON.
            if len(rows) == 1 and isinstance(rows[0], dict):
                self._kv_table(rows[0], title=title)
                return
            self._emit_json(data)
            return

        table = Table(title=title, show_lines=False, header_style="bold")
        for header, _ in columns:
            table.add_column(header)
        for row in rows:
            table.add_row(*[_fmt_cell(_accessor_value(row, acc)) for _, acc in columns])
        self.console.print(table)

    # ---- csv ---------------------------------------------------------
    def _emit_csv(self, data: Any, *, columns) -> None:
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else None
        if rows is None:
            sys.stdout.write(str(data) + "\n")
            return
        writer = csvlib.writer(sys.stdout)
        if columns:
            writer.writerow([h for h, _ in columns])
            for r in rows:
                writer.writerow([_csv_cell(_accessor_value(r, acc)) for _, acc in columns])
        elif rows and isinstance(rows[0], dict):
            # union of keys preserves columns even if some rows omit a field
            keys: list[str] = []
            for r in rows:
                for k in r:
                    if k not in keys:
                        keys.append(k)
            writer.writerow(keys)
            for r in rows:
                writer.writerow([_csv_cell(r.get(k)) for k in keys])

    # ---- markdown ----------------------------------------------------
    def _emit_markdown(self, data: Any, *, columns, title, empty) -> None:
        out = sys.stdout
        if title:
            out.write(f"### {title}\n\n")
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else None
        if rows is None:
            out.write(f"{data}\n")
            return
        if not rows:
            out.write(f"_{empty}_\n")
            return

        if columns is not None:
            headers = [h for h, _ in columns]
            cells = [[_md_cell(_accessor_value(r, acc)) for _, acc in columns] for r in rows]
            out.write(_md_table(headers, cells))
            return
        if len(rows) == 1 and isinstance(rows[0], dict):
            # single object -> Field/Value table
            cells = [[_md_cell(k), _md_cell(v)] for k, v in rows[0].items()]
            out.write(_md_table(["Field", "Value"], cells))
            return
        # list without a column spec -> fenced JSON (still valid markdown)
        out.write("```json\n")
        out.write(jsonlib.dumps(_jsonable(data), indent=2, ensure_ascii=False, default=str))
        out.write("\n```\n")

    def _kv_table(self, obj: dict, *, title: str | None) -> None:
        table = Table(title=title, show_header=False, box=None)
        table.add_column("field", style="bold cyan")
        table.add_column("value")
        for key, value in obj.items():
            table.add_row(key, _fmt_cell(value))
        self.console.print(table)


def print_error(err: Any, fmt: OutputFormat) -> None:
    """Render an error to stderr in the active format."""
    if fmt == OutputFormat.json:
        from .errors import OpError

        payload = err.to_dict() if isinstance(err, OpError) else {"error": str(err)}
        _err_console.file.write(jsonlib.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    else:
        _err_console.print(f"[red]error:[/red] {err}")


def _dotted_get(row: Any, path: str) -> Any:
    """Fetch a possibly-nested value by dotted path, e.g. ``assignee.name``."""
    cur = row
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _project(data: Any, fields: Sequence[str]) -> Any:
    """Keep only the selected fields (dotted paths allowed). The dotted string is
    used as the output key so the projection is flat and predictable."""
    if isinstance(data, list):
        return [{f: _dotted_get(r, f) for f in fields} if isinstance(r, dict) else r for r in data]
    if isinstance(data, dict):
        return {f: _dotted_get(data, f) for f in fields}
    return data


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return jsonlib.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _md_cell(value: Any) -> str:
    text = _fmt_cell(value)
    # keep the table one row per record: escape pipes and flatten newlines
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    return obj
