"""cab CLI: bootstrap, scan, run, generate, status, retry (PLAN.md 4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .calibre.client import CalibreClient
from .calibre.columns import bootstrap_columns
from .calibre.scanner import select_candidates
from .config import Config, load_config
from .errors import CabError, CalibreBlockedError
from .logging_setup import setup_logging
from .state import Ledger

app = typer.Typer(
    name="cab",
    help="CalibreAudioBridge: Calibre books to audiobooks and audio summaries.",
    no_args_is_help=True,
)
console = Console()


def _root(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config TOML path.")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj |= {"config_path": config, "verbose": verbose}


app.callback()(_root)


def _cfg(ctx: typer.Context) -> Config:
    return load_config(ctx.obj.get("config_path"))


def _logging(ctx: typer.Context, cfg: Config) -> None:
    setup_logging(
        verbose=bool(ctx.obj.get("verbose")),
        log_file=cfg.pipeline.work_dir / "logs" / "cab.log",
    )


def _fail(error: CabError) -> typer.Exit:
    console.print(f"[red]{error}[/red]")
    code = 3 if isinstance(error, CalibreBlockedError) else 2
    return typer.Exit(code)


@app.command()
def status(ctx: typer.Context) -> None:
    """Show last runs, job states, and calibre access mode."""
    try:
        cfg = _cfg(ctx)
    except CabError as error:
        raise _fail(error) from error
    _logging(ctx, cfg)
    mode = "unprobed"
    try:
        mode = CalibreClient(cfg.calibre).resolve_mode()
    except CabError as error:
        mode = f"unavailable ({error})"
    with Ledger(cfg.pipeline.work_dir / "ledger.db") as ledger:
        runs = ledger.last_runs()
        counts: dict[str, int] = {}
        for job in ledger.jobs():
            counts[job.status.value] = counts.get(job.status.value, 0) + 1
    console.print(f"work dir: {cfg.pipeline.work_dir}")
    console.print(f"calibre mode: {mode}")
    console.print(f"jobs: {counts or '{}'}")
    table = Table(title="last runs")
    for column in ("id", "started", "finished", "exit", "done", "failed"):
        table.add_column(column)
    for run in runs:
        table.add_row(
            str(run.id), run.started, run.finished or "-", str(run.exit_code),
            str(run.books_done), str(run.books_failed),
        )
    console.print(table)


@app.command()
def scan(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List candidate books (read-only dry run)."""
    try:
        cfg = _cfg(ctx)
        client = CalibreClient(cfg.calibre)
        books = client.list_books()
    except CabError as error:
        raise _fail(error) from error
    candidates = select_candidates(books, restrict_tag=cfg.calibre.restrict_tag)
    if as_json:
        payload = [
            {
                "id": c.book.id,
                "title": c.book.title,
                "authors": list(c.book.authors),
                "source_format": c.source_format,
                "needs_audiobook": c.needs_audiobook,
                "needs_summary": c.needs_summary,
                "pdf_only": c.pdf_only,
            }
            for c in candidates
        ]
        console.print_json(json.dumps(payload))
        return
    table = Table(title=f"{len(candidates)} candidate book(s)")
    for column in ("id", "title", "author", "source", "needs", "flags"):
        table.add_column(column)
    for c in candidates:
        needs = "+".join(
            name for name, wants in (("A", c.needs_audiobook), ("S", c.needs_summary)) if wants
        )
        flags = "pdf-only" if c.pdf_only else ""
        table.add_row(
            str(c.book.id), c.book.title[:40], ", ".join(c.book.authors)[:24],
            c.source_format, needs, flags,
        )
    console.print(table)


@app.command()
def bootstrap(ctx: typer.Context) -> None:
    """Create the #cab_* custom columns (run with calibre closed)."""
    try:
        cfg = _cfg(ctx)
        client = CalibreClient(cfg.calibre)
        report = bootstrap_columns(client)
    except CabError as error:
        raise _fail(error) from error
    console.print(f"created: {list(report.created) or '-'}")
    console.print(f"existing: {list(report.existing) or '-'}")


def _not_yet(milestone: str) -> None:
    console.print(f"[yellow]not implemented yet (milestone {milestone})[/yellow]")
    raise typer.Exit(2)


@app.command()
def run(ctx: typer.Context) -> None:
    """Cron entrypoint: process candidate books (PLAN.md 9.2)."""
    del ctx
    _not_yet("M5")


@app.command()
def generate(ctx: typer.Context, book_id: int) -> None:
    """Generate variants for a single book."""
    del ctx, book_id
    _not_yet("M4")


@app.command()
def retry(ctx: typer.Context) -> None:
    """Requeue failed jobs."""
    del ctx
    _not_yet("M5")


def main() -> None:
    """Console-script entrypoint."""
    app()


if __name__ == "__main__":
    main()
