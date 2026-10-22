"""
Command-line ingestion and scoring, run from the project root:

  flask --app run ingest-live                 last 30 days from Open-Meteo, then score them
  flask --app run replay --start 2024-04-01 --end 2024-05-31 --delay 2
  flask --app run score                       score any readings not scored yet
  flask --app run reset-readings              wipe readings (e.g. before a demo)

Ingestion commands score new readings and raise alerts straight away;
add --no-score to only load data.
"""
import click

from app import ingestion, scoring


def report(results: list[dict]) -> None:
    for r in results:
        if r["alert"]:
            click.echo(f"    ALERT {r['severity'].upper():8s} {r['region']:16s} {r['date']}  score {r['anomaly_score']:.3f}")


def summary(results: list[dict]) -> str:
    alerts = sum(r["alert"] for r in results)
    return f"{len(results)} readings scored, {alerts} alerts raised"


def register_cli(app):
    @app.cli.command("ingest-live")
    @click.option("--region", "regions", multiple=True, help="Region slug; repeat for several. Default: all.")
    @click.option("--days", default=30, show_default=True, type=click.IntRange(min=1, max=92))
    @click.option("--no-score", is_flag=True, help="Only load readings, do not score them.")
    def ingest_live_command(regions, days, no_score):
        """Pull the last N days from Open-Meteo into SENSOR_READING."""
        try:
            slugs = list(regions) or ingestion.all_region_slugs()
            results = ingestion.ingest_live(slugs, days, echo=click.echo)
        except (ValueError, RuntimeError) as exc:
            raise click.ClickException(str(exc))
        message = f"Done: {sum(results.values())} new readings."
        if not no_score:
            scored = scoring.score_pending(slugs)
            report(scored)
            message += f" {summary(scored)}."
        click.echo(message)

    @app.cli.command("replay")
    @click.option("--start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
    @click.option("--end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
    @click.option("--region", "regions", multiple=True, help="Region slug; repeat for several. Default: all.")
    @click.option("--history-days", default=30, show_default=True, type=click.IntRange(min=0))
    @click.option("--delay", default=0.0, show_default=True, type=click.FloatRange(min=0),
                  help="Seconds to wait between days, e.g. 2 for a live demo.")
    @click.option("--no-score", is_flag=True, help="Only load readings, do not score them.")
    def replay_command(start, end, regions, history_days, delay, no_score):
        """Feed historical days from master_dataset.csv into SENSOR_READING, one day at a time."""
        scored = []

        def score_new():
            new = scoring.score_pending(slugs)
            report(new)
            scored.extend(new)

        try:
            slugs = list(regions) or ingestion.all_region_slugs()
            total = ingestion.replay(slugs, start, end, history_days, delay, echo=click.echo,
                                     after_day=None if no_score else score_new)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        message = f"Done: {total} new readings."
        if not no_score:
            message += f" {summary(scored)}."
        click.echo(message)

    @app.cli.command("score")
    @click.option("--region", "regions", multiple=True, help="Region slug; repeat for several. Default: all.")
    def score_command(regions):
        """Score every reading that has not been scored yet and raise alerts."""
        try:
            scored = scoring.score_pending(list(regions) or None)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        report(scored)
        click.echo(f"Done: {summary(scored)}.")

    @app.cli.command("reset-readings")
    @click.confirmation_option(prompt="This deletes ALL readings, anomalies, alerts and feedback. Continue?")
    def reset_readings_command():
        """Delete all readings and the anomalies, alerts and feedback that depend on them."""
        for table, n in ingestion.reset_readings().items():
            click.echo(f"  {table}: {n} rows deleted")
