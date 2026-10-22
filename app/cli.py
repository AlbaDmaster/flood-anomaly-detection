"""
Command-line ingestion, run from the project root:

  flask --app run ingest-live                 last 30 days from Open-Meteo, all regions
  flask --app run replay --start 2024-04-01 --end 2024-05-31 --delay 2
  flask --app run reset-readings              wipe readings (e.g. before a demo)
"""
import click

from app import ingestion


def register_cli(app):
    @app.cli.command("ingest-live")
    @click.option("--region", "regions", multiple=True, help="Region slug; repeat for several. Default: all.")
    @click.option("--days", default=30, show_default=True, type=click.IntRange(min=1, max=92))
    def ingest_live_command(regions, days):
        """Pull the last N days from Open-Meteo into SENSOR_READING."""
        try:
            slugs = list(regions) or ingestion.all_region_slugs()
            results = ingestion.ingest_live(slugs, days, echo=click.echo)
        except (ValueError, RuntimeError) as exc:
            raise click.ClickException(str(exc))
        click.echo(f"Done: {sum(results.values())} new readings.")

    @app.cli.command("replay")
    @click.option("--start", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
    @click.option("--end", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
    @click.option("--region", "regions", multiple=True, help="Region slug; repeat for several. Default: all.")
    @click.option("--history-days", default=30, show_default=True, type=click.IntRange(min=0))
    @click.option("--delay", default=0.0, show_default=True, type=click.FloatRange(min=0),
                  help="Seconds to wait between days, e.g. 2 for a live demo.")
    def replay_command(start, end, regions, history_days, delay):
        """Feed historical days from master_dataset.csv into SENSOR_READING, one day at a time."""
        try:
            slugs = list(regions) or ingestion.all_region_slugs()
            total = ingestion.replay(slugs, start, end, history_days, delay, echo=click.echo)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"Done: {total} new readings.")

    @app.cli.command("reset-readings")
    @click.confirmation_option(prompt="This deletes ALL readings, anomalies, alerts and feedback. Continue?")
    def reset_readings_command():
        """Delete all readings and the anomalies, alerts and feedback that depend on them."""
        for table, n in ingestion.reset_readings().items():
            click.echo(f"  {table}: {n} rows deleted")
