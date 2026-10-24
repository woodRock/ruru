"""`ruru pdf`: show, fetch, file and list the missing PDFs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ruru import desktop, model, ops, pdftext
from ruru.cmdkit import Builder, norm_tag, open_library, rel_path
from ruru.errors import NO_MATCH, OK, PARTIAL, USAGE, NothingMatched, UsageError
from ruru.output import emit_json, style, warn
from ruru.search import in_project, sort_papers

PDF_ACTIONS = ("show", "add", "missing", "text")


def cmd_pdf_add(args: argparse.Namespace) -> int:
    lib = open_library(args)
    paths = [Path(p).expanduser() for p in args.paths] or [desktop.downloads_dir()]
    filed, refused = ops.add_pdfs(lib, paths, key=args.key, force=args.force)
    for key, src in filed:
        print(key if args.quiet else f"{key}: {src} -> {rel_path(lib, key).replace('paper.md', 'paper.pdf')}")
    for src, problem in refused:
        warn(f"{src}: {problem}")
    if not filed and not refused:
        print(f"no PDFs named after a library key in {', '.join(map(str, paths))} (save them as KEY.pdf)",
              file=sys.stderr)
        return NO_MATCH
    return OK if not refused else (USAGE if not filed else PARTIAL)


def cmd_pdf_missing(args: argparse.Namespace) -> int:
    lib = open_library(args)
    if args.cited and args.keys:
        raise UsageError("give keys or --cited PROJECT, not both")
    if args.cited:
        papers, unknown = ops.cited_papers(lib, args.cited)
        if unknown:
            warn(f"cited but not in the library: {', '.join(unknown)} (`ruru check {args.cited}`)")
    elif args.keys:
        papers = [lib.get(k) for k in args.keys]
    else:
        papers = [p for p in lib.papers() if (args.all or p.status in model.UNREAD)
                  and (not args.project or in_project(p, args.project))
                  and all(norm_tag(t) in p.tags for t in args.tag or [])]
    missing = [p for p in sort_papers(papers, "key") if not lib.has_pdf(p.key)]
    if args.fetch:
        missing = ops.fetch_open_access(lib, missing)
    if args.json:
        emit_json([{"key": p.key, "title": p.title, "link": ops.paper_link(p), "save_as": f"{p.key}.pdf"}
                   for p in missing])
    elif args.quiet:
        for p in missing:
            print(p.key)
    else:
        for p in missing:
            print(f"{style(f'{p.key:<28}', 'bold')} {ops.paper_link(p) or style('(no link)', 'dim')}  {p.title}")
    if args.open:
        openable = desktop.open_links(missing)
        if len(missing) > len(openable):
            warn(f"opened {len(openable)} of {len(missing)}; run it again after `ruru pdf add`")
    if missing and not args.json and not args.quiet:
        print(f"save each as KEY.pdf in {desktop.downloads_dir()}, then `ruru pdf add`", file=sys.stderr)
    if not missing:
        if not args.json:
            print("every paper here has its PDF", file=sys.stderr)
        return NO_MATCH
    return OK


def cmd_pdf(args: argparse.Namespace) -> int:
    lib = open_library(args)
    p = lib.get(args.key)
    path = lib.paper_dir(p.key) / "paper.pdf"
    if args.browser:
        link = desktop.browse_for_pdf(p)
        if not link:
            raise NothingMatched(f"{p.key}: no DOI or URL to open")
        print(link)
        print(f"save it as {p.key}.pdf (on the clipboard) in {desktop.downloads_dir()}, then `ruru pdf add`",
              file=sys.stderr)
        return OK
    if args.fetch and (args.force or not path.exists()):
        with ops.client_for(lib) as client:
            ops.fetch_pdf(lib, client, p.key, force=args.force)
    if not path.exists():
        raise NothingMatched(f"{p.key}: no PDF; `ruru pdf {p.key} --fetch` (open access) or `--browser` "
                             "(the publisher's page, to download it yourself)")
    if args.open:
        subprocess.run([desktop.opener, str(path)], check=False)
    print(path)
    return OK


def cmd_pdf_text(args: argparse.Namespace) -> int:
    lib = open_library(args)
    p = lib.get(args.key)
    if not lib.has_pdf(p.key):
        raise NothingMatched(f"{p.key}: no PDF; `ruru pdf {p.key} --fetch` or `ruru pdf {p.key} --browser`")
    text = ops.pdf_text(lib, p.key, progress=lambda key: print(f"ruru: reading the PDF of {key}", file=sys.stderr))
    if text is None or not text.strip(pdftext.PAGE_BREAK + " \n"):
        raise NothingMatched(f"{p.key}: the PDF has no text layer (a scan?)")
    pages = pdftext.pages(text)
    if args.page is not None and not 1 <= args.page <= len(pages):
        raise UsageError(f"{p.key}: the PDF has {len(pages)} pages; --page {args.page} is out of range")
    shown = pages[args.page - 1] if args.page is not None else text.replace(pdftext.PAGE_BREAK, "\n\n")
    if args.json:
        emit_json({"key": p.key, "pages": len(pages), "page": args.page, "text": shown})
    else:
        sys.stdout.write(shown if shown.endswith("\n") else shown + "\n")
    return OK


def register(b: Builder) -> None:
    p = b.command("pdf", cmd_pdf, "a paper's PDF: show/fetch/open it, file downloaded ones, list the missing",
                ["ruru pdf guillevic2021automated              (short for `ruru pdf show KEY`)",
                 "ruru pdf missing --open                       reading-list papers without a PDF",
                 "ruru pdf add                                  file ~/Downloads/KEY.pdf downloads"])
    grp = b.group(p, "pdf_command")

    pp = grp.command("show", cmd_pdf, "print the PDF's path; fetch (open access only) or open it",
                     ["ruru pdf show guillevic2021automated --fetch --open",
                      "ruru pdf guillevic2021automated --browser     publisher page; KEY.pdf on the clipboard"],
                     [b.write])
    pp.add_argument("key")
    pp.add_argument("--fetch", action="store_true", help="download the open-access PDF to paper.pdf")
    pp.add_argument("--force", action="store_true", help="with --fetch: download even if paper.pdf exists")
    pp.add_argument("--open", action="store_true", help="open it in the system viewer")
    pp.add_argument("--browser", action="store_true",
                    help="open the DOI page to download it yourself (KEY.pdf goes on the clipboard)")
    pp = grp.command("add", cmd_pdf_add, "move downloaded PDFs named KEY.pdf into the library",
                     ["ruru pdf add                       every KEY.pdf in ~/Downloads (or `downloads` in config)",
                      "ruru pdf add ~/Desktop/papers      a folder of KEY.pdf files",
                      "ruru pdf add 1-s2.0-S0021-main.pdf --key icoshift2011"],
                     [b.write, b.quiet])
    pp.add_argument("paths", nargs="*", metavar="PATH", help="PDFs or folders (default: the downloads folder)")
    pp.add_argument("--key", help="the paper a single, differently named file belongs to")
    pp.add_argument("--force", action="store_true", help="replace a PDF the paper already has")
    pp = grp.command("text", cmd_pdf_text, "print the text of a paper's PDF (new PDFs are read once, then cached)",
                     ["ruru pdf text huber1964robust", "ruru pdf text huber1964robust --page 3",
                      "ruru pdf text huber1964robust --json"],
                     [b.read])
    pp.add_argument("key")
    pp.add_argument("--page", type=int, metavar="N", help="only page N of the PDF (from 1)")
    pp = grp.command("missing", cmd_pdf_missing, "papers without a PDF, with the link to get each",
                     ["ruru pdf missing                   the reading list",
                      "ruru pdf missing -t robust-estimation",
                      "ruru pdf missing key1 key2 --open  open their publisher pages",
                      "ruru pdf missing --cited joint-align --fetch --open",
                      "ruru pdf missing --all --json"],
                     [b.write, b.read])
    pp.add_argument("keys", nargs="*", metavar="KEY", help="these papers (default: the reading list)")
    pp.add_argument("-p", "--project", metavar="PROJECT", help="only this project ('none': in no project)")
    pp.add_argument("-t", "--tag", action="append", metavar="TAG", help="must have this tag (repeatable)")
    pp.add_argument("--all", action="store_true", help="the whole library, not just the reading list")
    pp.add_argument("--cited", metavar="PROJECT", help="the papers PROJECT's .tex cites (any status)")
    pp.add_argument("--fetch", action="store_true", help="first download the open-access ones")
    pp.add_argument("--open", action="store_true",
                    help=f"open each link in the browser (at most {desktop.MAX_TABS})")
