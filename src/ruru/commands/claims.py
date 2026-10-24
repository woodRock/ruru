"""Claims and what they cite: `claims`, `verdict`, `own`, `cited`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ruru import cited as citedmod
from ruru import claims as claimsmod
from ruru import ops
from ruru import projects as projmod
from ruru.bibtex import ENCODINGS
from ruru.cmdkit import Builder, keys_line, open_library
from ruru.errors import NO_MATCH, OK, UsageError
from ruru.library import Library
from ruru.output import emit_json, style


def cmd_claims(args: argparse.Namespace) -> int:
    lib = open_library(args)
    project = ops.get_project(lib, args.project)
    found = ops.claims(lib, args.project)
    verdicts = claimsmod.load(lib.root, args.project)
    current = {(c.id, k) for c in found for k in c.keys}
    stale = [v for k, v in sorted(verdicts.items()) if k not in current]
    own = ops.own_keys(lib)
    entries = ops.cited_entries(lib)
    versions = ops.cited_versions(lib, list({k for c in found for k in c.keys}))
    wanted = set(args.verdict or []) | ({"unchecked", "outdated"} if args.unchecked else set())
    selected = [c for c in found
                if (not args.key or set(args.key) & set(c.keys))
                and (not wanted or claimsmod.matches(c, wanted, verdicts, versions, own))]

    def kind(k: str) -> str | None:
        return "paper" if lib.exists(k) else entries[k].kind if k in entries else None

    def has_pdf(k: str) -> bool:
        return lib.exists(k) and lib.has_pdf(k)

    if args.summary:
        _print_claims_summary(lib, args, project, selected, verdicts, own, stale, versions, set(entries))
    elif args.json:
        sources = {k: [str(f) for f in ops.cited_sources(lib, k)] for k in {k for c in selected for k in c.keys}}
        rows = [{"id": c.id, "file": claimsmod.where(c, project.tex_paths), "line": c.line, "keys": c.keys,
                 "text": c.text,
                 "checks": [{"key": k, "title": lib.get(k).title if lib.exists(k) else None,
                             "in_library": lib.exists(k), "has_pdf": has_pdf(k), "own": k in own,
                             "kind": kind(k), "read": sources[k], "version": versions.get(k),
                             "state": claimsmod.state(c, k, verdicts, versions),
                             "verdict": v.to_json() if (v := verdicts.get((c.id, k))) else None}
                            for k in c.keys]}
                for c in selected]
        emit_json({"project": args.project, "claims": rows, "stale": [v.to_json() for v in stale]})
    elif args.quiet:
        for c in selected:
            print(c.id)
    else:
        for c in selected:
            marks = []
            for k in c.keys:
                state = claimsmod.state(c, k, verdicts, versions)
                mine = style(f" {claimsmod.OWN}", "dim") if k in own else ""
                if state == "outdated":
                    marks.append(style(f"{claimsmod.OUTDATED} {k}", "yellow") + mine
                                 + style(f" ({kind(k)} changed)", "dim"))
                elif state != "unchecked":
                    sign, colour = claimsmod.MARKS[state]
                    marks.append(style(f"{sign} {k}", colour) + mine)
                else:
                    why = ("" if has_pdf(k) else " (no PDF)") if lib.exists(k) else \
                        f" ({kind(k)})" if kind(k) else " (not in library)"
                    marks.append(f"  {k}{mine}{style(why, 'dim')}")
            place = f"{claimsmod.where(c, project.tex_paths)}:{c.line}"
            print(f"{style(c.id, 'bold')} {place}  {'  '.join(marks)}")
            print(f"    {c.text}")
        if stale:
            print(f"{len(stale)} verdict(s) on sentences no longer in the tex: "
                  f"`ruru claims {args.project} --json` lists them under stale", file=sys.stderr)
    if not selected:
        if not args.json:
            print("no claims match", file=sys.stderr)
        return NO_MATCH
    return OK


def _print_claims_summary(lib: Library, args: argparse.Namespace, project: projmod.Project,
                          claims: list[claimsmod.Claim], verdicts: dict[tuple[str, str], claimsmod.Verdict],
                          own: set[str], stale: list[claimsmod.Verdict], versions: dict[str, str | None],
                          cited: set[str]) -> None:
    summary = claimsmod.summarise(claims, verdicts, own, lib.exists, lambda k: lib.has_pdf(k), versions, cited)
    problems = [{"claim": c.id, "key": v.key, "file": claimsmod.where(c, project.tex_paths), "line": c.line,
                 "text": c.text, "verdict": v.to_json()} for c, v in summary.problems]
    if args.json:
        emit_json({"project": args.project, "claims": summary.claims, "keys": len(summary.keys),
                   "counts": summary.counts, "problems": problems, "no_pdf": summary.no_pdf,
                   "not_in_library": summary.not_in_library, "stale": len(stale)})
        return
    if args.quiet:  # the claims that need attention
        for claim_id in dict.fromkeys(p["claim"] for p in problems):
            print(claim_id)
        return
    print(f"{args.project}: {summary.claims} claims citing {len(summary.keys)} keys")
    signs = {**claimsmod.MARKS, "unchecked": (claimsmod.UNCHECKED, "bold"),
             "outdated": (claimsmod.OUTDATED, "yellow"), "own": (claimsmod.OWN, "dim")}
    for name in (*claimsmod.STATES, "own"):
        if summary.counts[name]:
            label = "own work" if name == "own" else name
            print(f"  {style(signs[name][0], signs[name][1])} {label:<12} {summary.counts[name]}")
    for c, v in summary.problems:
        sign, colour = claimsmod.MARKS[v.verdict]
        detail = " ".join(x for x in (f"\u201c{v.quote}\u201d" if v.quote else "", v.note or "") if x)
        print(f"{style(sign + ' ' + v.verdict, colour)} {v.key} {claimsmod.where(c, project.tex_paths)}:{c.line} "
              f"({c.id})" + (f"\n    {detail}" if detail else ""))
    if summary.no_pdf:
        print(keys_line("no PDF", summary.no_pdf, f"ruru pdf missing --cited {args.project} --fetch --open"))
    if summary.not_in_library:
        print(keys_line("not in the library", summary.not_in_library,
                         "ruru add ID; a draft or software: ruru cited add KEY --draft PROJECT | --software PATH"))
    if stale:
        print(f"{len(stale)} stale verdict(s): `ruru claims {args.project} --json` lists them", file=sys.stderr)


def cmd_verdict(args: argparse.Namespace) -> int:
    lib = open_library(args)
    if args.drop:
        if not args.claim or args.verdict:
            raise UsageError("usage: ruru verdict PROJECT CLAIM [KEY] --drop")
        gone = ops.drop_verdicts(lib, args.project, args.claim, [args.key] if args.key else [])
        for v in gone:
            print(v.claim if args.quiet else f"dropped {v.claim} {v.key} ({v.verdict})")
        return OK
    if args.file:
        if args.claim:
            raise UsageError("give CLAIM KEY VERDICT or -F FILE, not both")
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        try:
            data = json.loads(text) if text.lstrip().startswith("[") else [
                json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as e:
            raise UsageError(f"{args.file}: not JSON or JSON lines: {e}") from None
        records = list(data)
    else:
        if not (args.claim and args.key and args.verdict):
            raise UsageError("usage: ruru verdict PROJECT CLAIM KEY VERDICT [--quote …] (or -F FILE)")
        records = [{"claim": args.claim, "key": args.key, "verdict": args.verdict, "from": args.source,
                    "page": args.page, "quote": args.quote, "note": args.note}]
    made = ops.record_verdicts(lib, args.project, records, by=args.by, verify=not args.no_verify)
    for v in made:
        print(v.claim if args.quiet else f"{v.claim} {v.key}: {v.verdict}")
    return OK


def cmd_own_list(args: argparse.Namespace) -> int:
    lib = open_library(args)
    own = sorted(ops.own_keys(lib))
    entries = ops.cited_entries(lib)

    def kind(k: str) -> str:
        return "paper" if lib.exists(k) else entries[k].kind if k in entries else "unknown"
    if args.json:
        emit_json([{"key": k, "in_library": lib.exists(k), "kind": kind(k)} for k in own])
    elif args.quiet:
        for k in own:
            print(k)
    else:
        for k in own:
            where = {"paper": f"library, tagged {ops.OWN_TAG}", "unknown": "ruru.toml `own` (old form): "
                     f"`ruru cited add {k} --draft PROJECT | --software PATH`"}.get(kind(k), f"cited {kind(k)}")
            print(f"{style(f'{k:<28}', 'bold')} {where}")
    if not own:
        if not args.json:
            print("nothing marked as your own work: `ruru own add KEY`, `ruru cited add KEY …`", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_own_add(args: argparse.Namespace) -> int:
    lib = open_library(args)
    ops.set_own(lib, add=args.keys)
    for k in args.keys:
        print(k if args.quiet else f"own: {k} (tagged {ops.OWN_TAG})")
    return OK


def cmd_own_remove(args: argparse.Namespace) -> int:
    lib = open_library(args)
    cited = [k for k in args.keys if k in lib.settings.cited]
    if cited:
        raise UsageError(f"{', '.join(cited)}: a cited draft or software; `ruru cited remove KEY`")
    unknown = [k for k in args.keys if k not in ops.own_keys(lib)]
    if unknown:
        raise UsageError(f"not marked as your own work: {', '.join(unknown)}")
    ops.set_own(lib, remove=args.keys)
    for k in args.keys:
        print(k if args.quiet else f"no longer own: {k}")
    return OK


def cmd_cited_list(args: argparse.Namespace) -> int:
    lib = open_library(args)
    entries = ops.cited_entries(lib)
    versions = ops.cited_versions(lib)
    if args.json:
        emit_json([{"key": k, **e.to_dict(lib.settings.cited[k].get("path")), "link": e.link,
                    "version": versions.get(k), "read": [str(f) for f in ops.cited_sources(lib, k)]}
                   for k, e in sorted(entries.items())])
    elif args.quiet:
        for k in sorted(entries):
            print(k)
    else:
        for k, e in sorted(entries.items()):
            where = (f"project {e.project}" if e.kind == "draft"
                     else "  ".join(x for x in (str(e.path or ""), e.link or "") if x))
            print(f"{style(f'{k:<20}', 'bold')} {e.kind:<9} {where}  {style(versions.get(k) or '-', 'dim')}")
    if not entries:
        if not args.json:
            print("no cited drafts or software: `ruru cited add KEY --draft PROJECT | --software PATH`",
                  file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_cited_add(args: argparse.Namespace) -> int:
    lib = open_library(args)
    if bool(args.draft) == bool(args.software or args.url):
        raise UsageError("give --draft PROJECT, or --software PATH (and/or --url URL)")
    kind = "draft" if args.draft else "software"
    entry = ops.add_cited(lib, args.key, kind, project=args.draft, path=args.software, url=args.url)
    print(args.key if args.quiet else f"cited {kind}: {args.key}"
          + (f" (project {entry.project})" if entry.project else f" ({entry.path or entry.link})"))
    return OK


def cmd_cited_remove(args: argparse.Namespace) -> int:
    lib = open_library(args)
    ops.remove_cited(lib, args.key)
    print(args.key if args.quiet else f"removed [cited.{args.key}] (verdicts on it are kept)")
    return OK


def cmd_cited_bib(args: argparse.Namespace) -> int:
    lib = open_library(args)
    entries = ops.cited_entries(lib)
    if missing := [k for k in args.keys if k not in entries]:
        raise UsageError(f"not a cited draft or software: {', '.join(missing)}")
    encoding = args.bib_encoding or lib.settings.bib_encoding
    sys.stdout.write("\n".join(citedmod.bibtex(entries[k], encoding) for k in args.keys))
    return OK


def register(b: Builder) -> None:
    p = b.command("claims", cmd_claims,
                "the sentences a project's .tex cites papers in, with any verdicts on them",
                ["ruru claims joint-align", "ruru claims joint-align --unchecked --json",
                 "ruru claims joint-align -k huber", "ruru claims joint-align --verdict unsupported partial",
                 "ruru claims joint-align --verdict own", "ruru claims joint-align --summary --json"],
                [b.read])
    p.add_argument("project")
    p.add_argument("-k", "--key", action="append", metavar="KEY",
                   help="only claims citing this key (repeatable)")
    p.add_argument("--unchecked", action="store_true",
                   help="only claims needing a verdict: a key unchecked, or outdated (its draft or "
                        "software has changed)")
    p.add_argument("--verdict", nargs="+", choices=claimsmod.FILTERS, metavar="STATE",
                   help=f"only claims with a cited key in one of these states: {' | '.join(claimsmod.FILTERS)} "
                        "(own: your own work)")
    p.add_argument("--summary", action="store_true",
                   help="counts per verdict, the unsupported/partial/not-found claims, what lacks a PDF "
                        "(-q: the ids of claims needing attention)")

    p = b.command("own", cmd_own_list,
                "your own work: library papers tagged own-work, plus every cited draft and software (a label; "
                "claims citing them are checked like any other)",
                ["ruru own", "ruru own add wood2022", "ruru own remove wood2022"],
                [b.read])
    grp = b.group(p, "own_command")
    for name, func, what in (("list", cmd_own_list, "list your own work"),
                             ("add", cmd_own_add, "tag library papers own-work (drafts and software: "
                                                  "`ruru cited add`)"),
                             ("remove", cmd_own_remove, "untag them")):
        op = grp.command(name, func, what, [f"ruru own {name}" + (" warpalign" if name != "list" else "")],
                         [b.read] if name == "list" else [b.write, b.quiet])
        if name != "list":
            op.add_argument("keys", nargs="+", metavar="KEY")

    p = b.command("cited", cmd_cited_list,
                "drafts and software a paper cites that aren't in the library ([cited.KEY] in ruru.toml)",
                ["ruru cited", "ruru cited add chromadcnn --draft chroma-dcnn",
                 "ruru cited add warpalign --software ~/Desktop/warp-align",
                 "ruru cited bib warpalign alignbench       BibTeX @misc from each repo's CITATION.cff"],
                [b.read])
    grp = b.group(p, "cited_command")

    grp.command("list", cmd_cited_list, "list cited drafts and software, with their current version",
                  ["ruru cited list --json"], [b.read])
    cp = grp.command("add", cmd_cited_add, "cite a draft (a ruru project) or software (a repo) by key",
                       ["ruru cited add chromadcnn --draft chroma-dcnn",
                        "ruru cited add warpalign --software ~/Desktop/warp-align",
                        "ruru cited add tool --url https://github.com/someone/tool   (no local copy)"],
                       [b.write, b.quiet])
    cp.add_argument("key", help="the key the .tex cites it by")
    cp.add_argument("--draft", metavar="PROJECT", help="a draft: the ruru project whose .tex it is")
    cp.add_argument("--software", metavar="PATH", help="software: its local git repo (README, CITATION.cff)")
    cp.add_argument("--url", help="software: its public URL (default: CITATION.cff's repository-code)")
    cp = grp.command("remove", cmd_cited_remove, "forget a cited draft or software (verdicts are kept)",
                       ["ruru cited remove alignbench"], [b.write, b.quiet])
    cp.add_argument("key")
    cp = grp.command("bib", cmd_cited_bib,
                       "print BibTeX @misc entries for cited software, from each repo's CITATION.cff",
                       ["ruru cited bib warpalign alignbench", "ruru cited bib warpalign --bib-encoding utf8"])
    cp.add_argument("keys", nargs="+", metavar="KEY")
    cp.add_argument("--bib-encoding", choices=ENCODINGS, help="override ruru.toml's bib_encoding")

    p = b.command("verdict", cmd_verdict,
                "record whether a cited paper supports a claim (see `ruru claims`); one commit",
                ["ruru verdict joint-align 3fa9c2e1b0 huber supported -p 75 --quote \"…\" --by claude",
                 "ruru verdict joint-align 3fa9c2e1b0 cow partial --quote \"…\" --note \"one wavelength only\"",
                 "ruru verdict joint-align 3fa9c2e1b0 kovats background --from other",
                 "ruru verdict joint-align -F verdicts.jsonl --by claude",
                 "    (JSON lines: claim, key, verdict, and optionally from, page, quote, note)",
                 "ruru verdict joint-align 3fa9c2e1b0 huber --drop"],
                [b.write, b.quiet])
    p.add_argument("project")
    p.add_argument("claim", nargs="?", metavar="CLAIM", help="a claim id from `ruru claims`")
    p.add_argument("key", nargs="?", metavar="KEY")
    p.add_argument("verdict", nargs="?", metavar="VERDICT", help=" | ".join(claimsmod.VERDICTS))
    p.add_argument("-p", "--page", help="where in the paper (the PDF's printed page number)")
    p.add_argument("--quote", help="the paper's own words (needed for supported and partial)")
    p.add_argument("--note", help="what the paper does say (needed for partial and unsupported)")
    p.add_argument("--from", dest="source", choices=claimsmod.SOURCES,
                   help="what the verdict rests on (default: pdf for a library paper, which must have one; "
                        "draft or software for a [cited] key, recording its version)")
    p.add_argument("-F", "--file", metavar="FILE", help="many verdicts as JSON lines or an array (- for stdin)")
    p.add_argument("--by", default=os.environ.get("RURU_AGENT") or None, help="who judged (default: $RURU_AGENT)")
    p.add_argument("--drop", action="store_true", help="forget the verdicts on CLAIM (KEY's only, if given)")
    p.add_argument("--no-verify", action="store_true",
                   help="don't look for the quote in the PDF's text (by default it must be there)")
