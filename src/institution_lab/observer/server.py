from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .catalog import Catalog
from .files import (
    configuration_file,
    configuration_files,
    file_content,
    files,
    run_configuration,
    systems,
)
from .history import event_page
from .view import ReadView

ASSETS = Path(__file__).with_name("assets")


def create_app(
    run: Path,
    *,
    runs_root: Path | None = None,
    config_root: Path | None = None,
) -> FastAPI:
    catalog = Catalog(run, runs_root)
    app = FastAPI(title="eusociality", docs_url=None, redoc_url=None)
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

    @app.get("/")
    def index():
        return FileResponse(ASSETS / "index.html")

    @app.get("/api/view")
    def snapshot(at: int | None = Query(default=None, ge=0), run: str | None = None):
        return get_view(run).snapshot(at)

    def get_view(identifier: str | None) -> ReadView:
        try:
            return catalog.view(identifier)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/runs")
    def runs():
        return {"runs": catalog.runs(), "selected": catalog.default}

    @app.get("/api/event/{sequence}")
    def detail(sequence: int, run: str | None = None):
        result = get_view(run).detail(sequence)
        if result is None:
            raise HTTPException(status_code=404, detail="unknown_event")
        return result

    @app.get("/api/history")
    def history(
        run: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        actor: str | None = None,
        all_events: bool = False,
        around: int | None = Query(default=None, ge=1),
    ):
        return event_page(
            get_view(run),
            offset=offset,
            limit=limit,
            actor=actor,
            all_events=all_events,
            around=around,
        )

    @app.get("/api/agent")
    def agent(
        actor: str,
        run: str | None = None,
        at: int | None = Query(default=None, ge=0),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        try:
            return get_view(run).agent(actor, at, offset, limit)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/communications")
    def communications(
        run: str | None = None,
        actor: str | None = None,
        at: int | None = Query(default=None, ge=0),
    ):
        return get_view(run).communications(at, actor)

    @app.get("/api/files")
    def file_list(
        source: str = "project",
        run: str | None = None,
        proposal: str | None = None,
        at: int | None = Query(default=None, ge=0),
        actor: str | None = None,
        snapshot: str | None = None,
    ):
        try:
            return files(get_view(run), source, proposal, at, actor, snapshot)
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/file")
    def file(
        path: str,
        source: str = "project",
        run: str | None = None,
        proposal: str | None = None,
        at: int | None = Query(default=None, ge=0),
        actor: str | None = None,
        snapshot: str | None = None,
    ):
        try:
            return file_content(get_view(run), source, path, proposal, at, actor, snapshot)
        except (FileNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/config")
    def configuration(run: str | None = None):
        try:
            return run_configuration(get_view(run))
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/configs")
    def configs():
        return {"files": configuration_files(config_root)}

    @app.get("/api/system")
    def system(run: str | None = None):
        try:
            return systems(get_view(run))
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/config-file")
    def config_file(path: str):
        if config_root is None:
            raise HTTPException(status_code=404, detail="configuration_catalog_not_available")
        try:
            return configuration_file(config_root, path)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return app
