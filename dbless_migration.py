import io
import time
from typing import Any

from alembic import command
from rich.console import Console

import alembic.config
import click
import docker
import sqlalchemy.exc
import typer
from pathlib import Path

from pydantic import BaseModel, ValidationError, validator
from rich.progress import (
    Progress,
    DownloadColumn,
    BarColumn,
    TextColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
)
from sqlalchemy import create_engine

CONTAINER_NAME = "dbless"
INI_SECTION_NAME = "dbless"

console = Console()


app = typer.Typer()


class Config(BaseModel):
    image_name: str
    container_env: dict[str, str]
    port_mapping: dict[str, int]
    engine_url: str

    @validator("container_env", "port_mapping", pre=True)
    def parse_as_dict(cls, value: Any) -> dict[str, str]:
        if isinstance(value, dict):
            return value

        if not isinstance(value, str):
            raise ValueError("Input must be a string")

        return {k: v for k, v in [v.split("=") for v in value.split(";") if v]}


def show_pull_progress(tasks, line, progress):
    if line["status"] == "Downloading":
        task_id = f'[red][Download {line["id"]}]'
    elif line["status"] == "Extracting":
        task_id = f'[green][Extract  {line["id"]}]'
    else:
        # skip other statuses
        return

    if task_id not in tasks.keys():
        tasks[task_id] = progress.add_task(f"{task_id}", total=line["progressDetail"]["total"])
    else:
        progress.update(tasks[task_id], completed=line["progressDetail"]["current"])


def image_pull(image_name):
    tasks = {}

    console.print(f"[blue]Pulling image: {image_name}")

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        DownloadColumn(),
    ) as progress:
        client = docker.from_env()
        resp = client.api.pull(image_name, stream=True, decode=True)
        for line in resp:
            show_pull_progress(tasks, line, progress)


def ensure_image_exists(image_name: str) -> None:
    client = docker.from_env()

    # Check if image exists
    if image_name not in [i.tags[0] for i in client.images.list() if i.tags]:
        image_pull(image_name)


def run_container(image_name: str, env: dict[str, str], ports: dict[str, int]) -> None:
    client = docker.from_env()

    if any(CONTAINER_NAME == container.name for container in client.containers.list()):
        stop_container()

    console.print(f"[blue]Running container from image: {image_name}")

    client.containers.run(
        image_name,
        name=CONTAINER_NAME,
        detach=True,
        ports=ports,
        environment=env,
    )


def stop_container():
    client = docker.from_env()

    if any(CONTAINER_NAME == container.name for container in client.containers.list()):
        console.print("[blue]Stopping container")
        container = client.containers.get(CONTAINER_NAME)
        container.stop()
        container.remove()
    else:
        console.print("[yellow]No container to stop")


def wait_for_db_connection(engine_url: str, timeout: int):
    console.print("[blue]Waiting for db connection")
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            engine = create_engine(engine_url)
            engine.connect()
        except sqlalchemy.exc.OperationalError:
            console.print("[yellow]Trying to connect again...")
            time.sleep(1)
            continue
        else:
            console.print("[blue]Connected")
            engine.dispose()
            break


def upgrade_database(alembic_cfg: alembic.config.Config):
    console.print("[blue]Running migrations")

    command.current(alembic_cfg)
    value = alembic_cfg.stdout.readline()
    if value:
        console.print("[red]WARNING! Alembic already ran on this database, will not run!")
        raise typer.Abort()

    command.upgrade(alembic_cfg, "head")


def create_migration(alembic_cfg: alembic.config.Config, message: str):
    console.print("[blue]creating migration")

    command.revision(alembic_cfg, autogenerate=True, message=message)


@app.callback()
def initialize_alembic_cfg(
    ctx: typer.Context, alembic_ini: Path = typer.Option(exists=True, default="alembic.ini")
) -> None:
    ctx.ensure_object(dict)

    alembic_cfg = alembic.config.Config(alembic_ini)

    if not alembic_cfg.file_config.has_section(INI_SECTION_NAME):
        raise click.ClickException(
            f"Section `{INI_SECTION_NAME}` does not exist in {alembic_ini.as_posix()}"
        )

    try:
        ctx.obj["config"] = Config.parse_obj(alembic_cfg.get_section(INI_SECTION_NAME))
    except ValidationError as e:
        raise click.ClickException(
            f"Section `{INI_SECTION_NAME}` does not pass validation: {str(e)}"
        )

    alembic_cfg.set_main_option("sqlalchemy.url", ctx.obj["config"].engine_url)

    stdout_buffer = io.StringIO()
    alembic_cfg.stdout = stdout_buffer

    ctx.obj["alembic_cfg"] = alembic_cfg


@app.command()
def start(ctx: typer.Context) -> None:
    ensure_image_exists(ctx.obj["config"].image_name)

    run_container(
        ctx.obj["config"].image_name,
        ctx.obj["config"].container_env,
        ctx.obj["config"].port_mapping,
    )

    wait_for_db_connection(ctx.obj["config"].engine_url, timeout=60)

    upgrade_database(ctx.obj["alembic_cfg"])


@app.command()
def stop() -> None:
    stop_container()


@app.command()
def auto(ctx: typer.Context, message: str) -> None:
    try:
        start(ctx)

        alembic_cfg = ctx.obj["alembic_cfg"]

        create_migration(alembic_cfg, message)

        console.print("[green]Great success!")

    finally:
        stop()


if __name__ == "__main__":
    app()
