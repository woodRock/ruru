"""JSON Schema for everything `--json` prints: the machine-readable form of docs/json.md.

`ruru schema` prints this document. Keys are only ever added to these objects, so every schema
allows extra properties; `required` lists what is always present. Tests validate real command output
against these definitions, so the schema and the commands cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from ruru import __version__, model
from ruru.errors import LOCKED, NO_MATCH, OK, PARTIAL, USAGE
from ruru.inbox import DECISIONS

SCHEMA_VERSION = 1
DIALECT = "https://json-schema.org/draft/2020-12/schema"

Schema = dict[str, Any]

STR: Schema = {"type": "string"}
INT: Schema = {"type": "integer"}
NUM: Schema = {"type": "number"}
BOOL: Schema = {"type": "boolean"}
DATE: Schema = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
# A datetime, or a date for items filed before datetimes were recorded.
DATETIME: Schema = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}.*)?$"}
STRS: Schema = {"type": "array", "items": STR}


def nullable(s: Schema) -> Schema:
    return {"anyOf": [s, {"type": "null"}]}


def ref(name: str) -> Schema:
    return {"$ref": f"#/$defs/{name}"}


def array(of: Schema) -> Schema:
    return {"type": "array", "items": of}


def enum(*values: str) -> Schema:
    return {"enum": list(values)}


def obj(props: dict[str, Schema], required: list[str] | None = None, description: str = "") -> Schema:
    out: Schema = {"type": "object", "properties": props,
                   "required": list(props) if required is None else required}
    if description:
        out["description"] = description
    return out


def extend(base: str, props: dict[str, Schema], required: list[str] | None = None) -> Schema:
    return {"allOf": [ref(base), obj(props, required)]}


_BIBLIOGRAPHIC: dict[str, Schema] = {
    "title": STR, "authors": STRS, "year": nullable(INT), "venue": nullable(STR), "type": enum(*model.TYPES),
    "volume": nullable(STR), "issue": nullable(STR), "pages": nullable(STR), "publisher": nullable(STR),
    "ids": {"type": "object", "additionalProperties": nullable(STR)}, "url": nullable(STR),
    "pdf_url": nullable(STR), "abstract": nullable(STR),
}

DEFS: dict[str, Schema] = {
    "paper": obj({
        "key": STR, **_BIBLIOGRAPHIC, "status": enum(*model.STATUSES), "tags": STRS, "projects": STRS,
        "added": nullable(DATE), "read_on": nullable(DATE), "source": STR,
        "path": STR, "has_pdf": BOOL,
    }),
    "paper_full": extend("paper", {"notes": STR, "agent_notes": STR}),
    "search_hit": extend("paper", {"score": INT, "in_pdf": BOOL, "snippet": nullable(STR)}),
    "similar_paper": obj({"key": STR, "similarity": NUM}),
    "inbox_item": obj({
        "id": STR, **_BIBLIOGRAPHIC, "tags": STRS, "source": STR, "published": nullable(DATE),
        "fetched": DATETIME,
        "suggestion": nullable(obj({"action": enum(*DECISIONS), "reason": STR, "by": STR, "date": DATE},
                                   ["action", "reason"])),
        "path": nullable(STR), "viewed": BOOL,
        "relevance": nullable(NUM), "similar_to": array(ref("similar_paper")),
        "possible_duplicate_of": nullable(STR),
    }),
    "related_hit": obj({
        "id": STR, "kind": enum("library", "inbox"), "similarity": NUM, "title": STR, "authors": STRS,
        "year": nullable(INT), "venue": nullable(STR), "status": nullable(enum(*model.STATUSES)),
    }),
    "dupe_pair": obj({"a": STR, "b": STR, "similarity": NUM, "same": BOOL,
                      "a_title": nullable(STR), "b_title": nullable(STR)}),
    "review": obj({"id": STR, "title": STR, "papers": STRS, "tags": STRS, "status": enum("new", "kept"),
                   "created": DATETIME, "by": nullable(STR), "path": nullable(STR)}),
    "seen_record": obj({"id": STR, "decision": enum(*DECISIONS, "remove"), "date": DATE, "key": nullable(STR),
                        "ids": {"type": "object"}, "title": STR, "year": nullable(INT)},
                       ["id", "decision", "date"]),
    "doctor_check": obj({"name": STR, "status": enum("ok", "warn", "fail"), "detail": STR}),
    "project": obj({"name": STR, "path": STR, "bib": STR, "tex": STRS, "bib_encoding": nullable(STR)}),
    "feed": obj({"name": STR, "kind": enum("arxiv", "openalex", "crossref", "rss"), "last_fetch": nullable(DATE)},
                ["name", "kind", "last_fetch"]),
    "fetch_result": obj({
        "feeds": array(obj({"name": STR, "new": INT, "seen": INT, "error": nullable(STR)})),
        "new": STRS,
    }),
    "verdict": obj({
        "claim": STR, "key": STR,
        "verdict": enum("supported", "partial", "unsupported", "not-found", "background"),
        "source": STR, "page": nullable(STR), "quote": nullable(STR), "note": nullable(STR), "by": nullable(STR),
        "date": STR, "text": STR, "version": nullable(STR),
    }),
    "pdf_missing": obj({"key": STR, "title": STR, "link": nullable(STR), "save_as": STR}),
    "pdf_text": obj({"key": STR, "pages": INT, "page": nullable(INT), "text": STR}),
    "enrich_result": obj({"key": STR, "filled": {"type": "object"}, "error": nullable(STR)}),
    "refresh_result": obj({"key": STR, "changes": {"type": "object"}, "error": nullable(STR)}),
    "index_status": obj({"path": STR, "exists": BOOL, "bytes": INT, "papers": INT, "inbox": INT, "pdfs": INT,
                         "pdfs_failed": INT, "full_text_search": BOOL}),
    "index_update": obj({"papers": INT, "inbox": INT, "pdfs": nullable(obj({
        "extracted": STRS, "failed": {"type": "object", "additionalProperties": STR}, "current": INT}))}),
    "undo_result": obj({"sha": STR, "date": STR, "message": STR, "files": STRS, "reverted_as": nullable(STR)}),
    "batch_result": obj({"argv": STRS, "code": INT, "stdout": STR, "stderr": STR, "data": {}}),
    "check_report": obj({
        "project": nullable(STR), "tex": STRS, "bib": nullable(STR), "cited": INT, "issues": {"type": "object"},
        "issue_count": INT, "warnings": STRS, "fixed": {"type": "object"},
    }),
    "claims_document": obj({"project": STR, "claims": array({"type": "object"}),
                            "stale": array({"type": "object"})}),
    "own_entry": obj({"key": STR, "in_library": BOOL, "kind": enum("paper", "draft", "software", "unknown")}),
}

# command -> what `--json` prints. "of" names a definition in $defs; "no_match" is the exit code for an
# empty answer.
COMMANDS: dict[str, dict[str, Any]] = {
    "list": {"output": "array", "of": "paper", "no_match": NO_MATCH},
    "search": {"output": "array", "of": "search_hit", "no_match": NO_MATCH},
    "show": {"output": "object", "of": "paper_full", "note": "an inbox id gives an inbox_item instead"},
    "inbox": {"output": "array", "of": "inbox_item", "no_match": NO_MATCH},
    "related": {"output": "array", "of": "related_hit", "no_match": NO_MATCH},
    "dupes": {"output": "array", "of": "dupe_pair", "no_match": NO_MATCH},
    "seen": {"output": "array", "of": "seen_record", "no_match": NO_MATCH},
    "fetch": {"output": "object", "of": "fetch_result"},
    "feeds": {"output": "array", "of": "feed", "no_match": NO_MATCH},
    "project": {"output": "array", "of": "project", "no_match": NO_MATCH},
    "review": {"output": "array", "of": "review", "no_match": NO_MATCH},
    "check": {"output": "object", "of": "check_report", "no_match": NO_MATCH},
    "claims": {"output": "object", "of": "claims_document", "no_match": NO_MATCH},
    "own": {"output": "array", "of": "own_entry", "no_match": NO_MATCH},
    "doctor": {"output": "array", "of": "doctor_check"},
    "enrich": {"output": "array", "of": "enrich_result"},
    "refresh": {"output": "array", "of": "refresh_result"},
    "pdf missing": {"output": "array", "of": "pdf_missing", "no_match": NO_MATCH},
    "pdf text": {"output": "object", "of": "pdf_text"},
    "index": {"output": "object", "of": "index_status"},
    "index update": {"output": "object", "of": "index_update"},
    "undo": {"output": "array", "of": "undo_result", "no_match": NO_MATCH},
    "batch": {"output": "array", "of": "batch_result"},
}

EXIT_CODES = {
    str(OK): "ok",
    str(USAGE): "usage error, or an unknown key or id",
    str(NO_MATCH): "nothing matched (or `check` found issues)",
    str(PARTIAL): "some items or feeds failed",
    str(LOCKED): "the library lock is held by another ruru process",
}


def document() -> Schema:
    return {
        "$schema": DIALECT,
        "title": "ruru --json output",
        "schema_version": SCHEMA_VERSION,
        "ruru_version": __version__,
        "exit_codes": EXIT_CODES,
        "commands": COMMANDS,
        "$defs": DEFS,
    }


def names() -> list[str]:
    return sorted(DEFS)


def definition(name: str) -> Schema:
    """One definition as a self-contained schema (a validator can use it directly)."""
    if name not in DEFS:
        raise KeyError(name)
    return {"$schema": DIALECT, "$ref": f"#/$defs/{name}", "$defs": DEFS}


def for_command(command: str) -> Schema:
    """The schema of what `command --json` prints (an array of the definition, or the object itself)."""
    spec = COMMANDS[command]
    item = ref(spec["of"])
    top = array(item) if spec["output"] == "array" else item
    return {"$schema": DIALECT, **top, "$defs": DEFS}
