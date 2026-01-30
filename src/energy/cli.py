"""Command-line interface for home energy analysis."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.table import Table

from . import db
from .analysis import sessions, summary
from .collectors import airbnb, eon, huum, open_meteo, shelly, shelly_csv, shelly_local
from .tariffs import load_tariffs_from_yaml, save_tariffs_to_db, update_costs_for_readings

console = Console()


@click.group()
@click.option("--db-path", type=click.Path(), help="Path to SQLite database")
@click.pass_context
def cli(ctx, db_path):
    """Home energy analysis - track and analyze electricity usage."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = Path(db_path) if db_path else None


# Database commands
@cli.group()
def database():
    """Database management commands."""
    pass


@database.command("init")
@click.pass_context
def db_init(ctx):
    """Initialize the database schema."""
    db.init_db(ctx.obj["db_path"])
    console.print("[green]Database initialized successfully[/green]")

    # Offer to load tariffs
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "tariffs.yaml"
    if config_path.exists():
        tariffs = load_tariffs_from_yaml(config_path)
        count = save_tariffs_to_db(tariffs, ctx.obj["db_path"])
        console.print(f"[green]Loaded {count} tariff(s) from config[/green]")


@database.command("stats")
@click.pass_context
def db_stats(ctx):
    """Show database statistics."""
    stats = db.get_stats(ctx.obj["db_path"])

    table = Table(title="Database Statistics")
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Range")

    elec = stats["electricity_readings"]
    table.add_row(
        "Electricity readings",
        str(elec["count"]),
        f"{elec['earliest'] or 'N/A'} → {elec['latest'] or 'N/A'}",
    )

    for source, count in stats.get("electricity_by_source", {}).items():
        table.add_row(f"  └ {source}", str(count), "")

    temp = stats["temperature_readings"]
    table.add_row(
        "Temperature readings",
        str(temp["count"]),
        f"{temp['earliest'] or 'N/A'} → {temp['latest'] or 'N/A'}",
    )

    table.add_row("Sauna sessions", str(stats["sauna_sessions"]["count"]), "")
    
    airbnb = stats.get("airbnb", {"count": 0})
    table.add_row(
        "Airbnb reservations", 
        str(airbnb["count"]), 
        f"{airbnb.get('earliest') or 'N/A'} → {airbnb.get('latest') or 'N/A'}"
    )

    table.add_row("Tariffs", str(stats["tariffs"]["count"]), "")

    console.print(table)


# Import commands
@cli.group("import")
def import_cmd():
    """Import data from various sources."""
    pass


@import_cmd.command("eon")
@click.option("--csv", "csv_path", type=click.Path(exists=True), help="Path to eonapi CSV export")
@click.pass_context
def import_eon(ctx, csv_path):
    """Import EON electricity data from CSV."""
    if not csv_path:
        console.print("[red]Please specify --csv path[/red]")
        return

    result = eon.import_from_csv(Path(csv_path), ctx.obj["db_path"])
    console.print(f"[green]Imported {result['imported']} readings[/green]")
    if result["skipped"]:
        console.print(f"[yellow]Skipped {result['skipped']} duplicates[/yellow]")


@import_cmd.command("huum")
@click.option("--file", "file_path", type=click.Path(exists=True), help="Path to huum-cli export")
@click.pass_context
def import_huum(ctx, file_path):
    """Import Huum sauna temperature data."""
    if not file_path:
        console.print("[red]Please specify --file path[/red]")
        return

    result = huum.import_from_file(Path(file_path), ctx.obj["db_path"])
    console.print(f"[green]Imported {result['imported']} readings[/green]")
    if result["skipped"]:
        console.print(f"[yellow]Skipped {result['skipped']} duplicates[/yellow]")


@import_cmd.command("shelly")
@click.option("--days", default=7, help="Number of days to fetch (default: 7)")
@click.option("--from-date", help="Start date (YYYY-MM-DD)")
@click.option("--to-date", help="End date (YYYY-MM-DD)")
@click.pass_context
def import_shelly(ctx, days, from_date, to_date):
    """Import Shelly power monitoring data from Shelly Cloud.

    Requires SHELLY_AUTH_KEY and SHELLY_DEVICE_ID environment variables.
    Optionally SHELLY_SERVER_ID (default: 103).
    """
    try:
        # Determine date range
        if from_date and to_date:
            start = datetime.fromisoformat(from_date)
            end = datetime.fromisoformat(to_date)
        elif from_date:
            start = datetime.fromisoformat(from_date)
            end = datetime.now()
        else:
            # Fetch last N days, or since last reading
            end = datetime.now()
            latest = shelly.get_latest_reading(ctx.obj["db_path"])
            if latest:
                start = latest
                console.print(f"[cyan]Fetching since last reading: {latest.date()}[/cyan]")
            else:
                start = end - timedelta(days=days)
                console.print(f"[cyan]Fetching last {days} days[/cyan]")

        result = shelly.fetch_and_import(start, end, ctx.obj["db_path"])
        console.print(f"[green]Imported {result['imported']} readings (30-min intervals)[/green]")
        if result["skipped"]:
            console.print(f"[yellow]Skipped {result['skipped']} duplicates[/yellow]")

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Failed to import Shelly data: {e}[/red]")
        raise


@import_cmd.command("shelly-csv")
@click.option("--ip", default="192.168.6.124", help="Device IP address")
@click.option("--channel", default=2, help="EM1Data channel (default: 2)")
@click.option("--days", default=30, help="Number of days to fetch (default: 30)")
@click.pass_context
def import_shelly_csv(ctx, ip, channel, days):
    """Import Shelly power data from local device CSV endpoint.

    Fetches per-minute data from the Shelly Pro 3EM's local HTTP API,
    aggregates to 30-minute intervals, and imports to the database.

    Duplicates are automatically skipped based on interval_start.
    """
    try:
        console.print(f"[cyan]Fetching last {days} days from {ip} channel {channel}...[/cyan]")
        console.print("[dim]This may take a few minutes for large date ranges[/dim]")

        result = shelly_csv.fetch_and_import(
            ip=ip,
            channel=channel,
            days=days,
            db_path=ctx.obj["db_path"],
        )

        console.print(
            f"[green]Imported {result['imported']} intervals "
            f"(from {result['raw_rows']} minute readings)[/green]"
        )
        if result["skipped"]:
            console.print(f"[yellow]Skipped {result['skipped']} duplicates[/yellow]")

    except httpx.ConnectError:
        console.print(f"[red]Could not connect to {ip} - check IP and network[/red]")
    except httpx.TimeoutException:
        console.print(f"[red]Request timed out - try reducing --days[/red]")
    except Exception as e:
        console.print(f"[red]Failed to import Shelly CSV data: {e}[/red]")
        raise


@import_cmd.command("weather")
@click.option("--days", default=30, help="Number of days to fetch (default: 30)")
@click.option("--latitude", default=51.989, help="Location latitude")
@click.option("--longitude", default=-1.497, help="Location longitude")
@click.pass_context
def import_weather(ctx, days, latitude, longitude):
    """Import outside temperature data from Open-Meteo.

    Fetches historical hourly temperature data for correlation with
    energy consumption patterns. Data is stored with sensor_id='outside_temperature'.

    Note: The Archive API has a 5-7 day delay.
    """
    try:
        console.print(f"[cyan]Fetching {days} days of weather data for ({latitude}, {longitude})...[/cyan]")

        result = open_meteo.import_weather_data(
            days=days,
            latitude=latitude,
            longitude=longitude,
            db_path=ctx.obj["db_path"],
        )

        console.print(f"[green]Imported {result['imported']} temperature readings[/green]")
        if result["skipped"]:
            console.print(f"[yellow]Skipped {result['skipped']} duplicates[/yellow]")

    except httpx.HTTPStatusError as e:
        console.print(f"[red]API error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Failed to import weather data: {e}[/red]")
        raise


@import_cmd.command("airbnb")
@click.pass_context
def import_airbnb(ctx):
    """Fetch future reservations from Airbnb iCal."""
    # TODO: Move URL to config/env if it changes, but user provided a specific one.
    url = "https://www.airbnb.co.uk/calendar/ical/52727898.ics?t=628704b831d841ec8ea05b0b203ec621"
    console.print(f"[cyan]Fetching Airbnb calendar...[/cyan]")
    try:
        stats = airbnb.fetch_from_ical(url, ctx.obj["db_path"])
        console.print(f"[green]Imported {stats['imported']} reservations[/green]")
        if stats["skipped"]:
            console.print(f"[yellow]Skipped {stats['skipped']} existing[/yellow]")
    except Exception as e:
        console.print(f"[red]Failed to fetch Airbnb calendar: {e}[/red]")


@import_cmd.command("airbnb-csv")
@click.option("--file", "file_path", type=click.Path(exists=True), required=True, help="Path to CSV export")
@click.pass_context
def import_airbnb_csv(ctx, file_path):
    """Import historical reservations from Airbnb CSV."""
    console.print(f"[cyan]Importing from {file_path}...[/cyan]")
    try:
        stats = airbnb.import_from_csv(Path(file_path), ctx.obj["db_path"])
        console.print(f"[green]Imported {stats['imported']} reservations[/green]")
        if stats["skipped"]:
            console.print(f"[yellow]Skipped {stats['skipped']} existing[/yellow]")
    except Exception as e:
        console.print(f"[red]Failed to import CSV: {e}[/red]")


@cli.group()
def shelly_cmd():
    """Shelly device management commands."""
    pass


@shelly_cmd.command("info")
@click.option("--ip", help="Device IP address (or set SHELLY_LOCAL_IP)")
def shelly_info(ip):
    """Get information about a local Shelly device."""
    try:
        device_ip = ip or os.environ.get("SHELLY_LOCAL_IP")
        if not device_ip:
            console.print("[red]Please provide --ip or set SHELLY_LOCAL_IP[/red]")
            return

        console.print(f"[cyan]Connecting to {device_ip}...[/cyan]")
        info = shelly_local.get_device_info(device_ip)

        table = Table(title=f"Shelly Device @ {device_ip}")
        table.add_column("Property", style="cyan")
        table.add_column("Value")

        table.add_row("Generation", info["generation"])
        table.add_row("Model", info["model"])
        table.add_row("MAC Address", info["mac"])
        table.add_row("Firmware", info["fw_version"])

        console.print(table)

        # Show current status
        channel = int(os.environ.get("SHELLY_CHANNEL", "0"))
        status = shelly_local.fetch_current_status(device_ip, channel)

        console.print(f"\n[cyan]Current Status (Channel {channel}):[/cyan]")
        console.print(f"  Power: {status['power']:.2f} W")
        console.print(f"  Total: {status['total'] / 1000:.2f} kWh")

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Failed to get device info: {e}[/red]")
        raise


@shelly_cmd.command("collect")
@click.option("--ip", help="Device IP address (or set SHELLY_LOCAL_IP)")
@click.option("--channel", type=int, help="Channel number (default: 0, or set SHELLY_CHANNEL)")
@click.pass_context
def shelly_collect(ctx, ip, channel):
    """Collect a 30-minute reading from local Shelly device.

    This should be run periodically (e.g., every 30 minutes via cron).
    It calculates consumption based on the device's total energy counter.

    Requires SHELLY_LOCAL_IP environment variable.
    """
    try:
        device_ip = ip or os.environ.get("SHELLY_LOCAL_IP")
        if not device_ip:
            console.print("[red]Please provide --ip or set SHELLY_LOCAL_IP[/red]")
            return

        device_channel = channel if channel is not None else int(os.environ.get("SHELLY_CHANNEL", "0"))

        result = shelly_local.collect_readings_from_polling(
            device_ip, device_channel, interval_minutes=30, db_path=ctx.obj["db_path"]
        )

        if "message" in result:
            console.print(f"[yellow]{result['message']}[/yellow]")
        elif result["imported"]:
            console.print(f"[green]Imported {result['imported']} readings[/green]")
        elif result["skipped"]:
            console.print(f"[yellow]Skipped {result['skipped']} duplicates[/yellow]")
        else:
            console.print("[yellow]No new data collected[/yellow]")

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Failed to collect data: {e}[/red]")
        raise


@shelly_cmd.command("list-devices")
@click.option("--auth-key", help="Shelly Cloud auth key (or set SHELLY_AUTH_KEY)")
@click.option("--server-id", help="Shelly server ID (default: 103)")
@click.option("--debug", is_flag=True, help="Show raw API response for debugging")
def shelly_list(auth_key, server_id, debug):
    """List all Shelly devices on your account."""
    try:
        if debug:
            # Get raw API response for debugging
            import httpx
            from dotenv import load_dotenv
            import os

            load_dotenv()
            auth_key = auth_key or os.environ.get("SHELLY_AUTH_KEY")
            server_id = server_id or os.environ.get("SHELLY_SERVER_ID", "103")

            if not auth_key:
                console.print("[red]SHELLY_AUTH_KEY not set[/red]")
                return

            url = f"https://shelly-{server_id}-eu.shelly.cloud/device/all_status"
            with httpx.Client() as client:
                response = client.post(url, json={"auth_key": auth_key}, timeout=30.0)
                console.print(json.dumps(response.json(), indent=2))
            return

        devices = shelly.list_devices(auth_key, server_id)

        if not devices:
            console.print("[yellow]No devices found[/yellow]")
            return

        table = Table(title="Shelly Devices")
        table.add_column("Device ID", style="cyan")
        table.add_column("MAC Address", style="dim")
        table.add_column("Name")
        table.add_column("Type/Model")
        table.add_column("IP Address", style="dim")
        table.add_column("Has Meter", justify="center")
        table.add_column("Status")

        for device in devices:
            status = "[green]Online[/green]" if device["online"] else "[red]Offline[/red]"
            has_meter = "[green]✓[/green]" if device["has_meter"] else "[yellow]?[/yellow]"
            table.add_row(
                device["id"],
                device["mac"],
                device["name"],
                device["type"],
                device["ip"],
                has_meter,
                status,
            )

        console.print(table)
        console.print("\n[cyan]To use a device, set:[/cyan]")
        console.print("export SHELLY_DEVICE_ID='<device-id-from-above>'")

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Failed to list devices: {e}[/red]")
        raise


# Tariff commands
@cli.group()
def tariff():
    """Tariff management commands."""
    pass


@tariff.command("load")
@click.option("--config", type=click.Path(exists=True), help="Path to tariffs.yaml")
@click.pass_context
def tariff_load(ctx, config):
    """Load tariffs from YAML config."""
    config_path = Path(config) if config else None
    tariffs = load_tariffs_from_yaml(config_path)
    count = save_tariffs_to_db(tariffs, ctx.obj["db_path"])
    console.print(f"[green]Loaded {count} tariff(s)[/green]")


@tariff.command("update-costs")
@click.pass_context
def tariff_update_costs(ctx):
    """Calculate costs for readings that don't have them."""
    count = update_costs_for_readings(ctx.obj["db_path"])
    console.print(f"[green]Updated {count} readings with cost data[/green]")


# Analysis commands
@cli.group()
def sessions_cmd():
    """Sauna session commands."""
    pass


@sessions_cmd.command("detect")
@click.pass_context
def sessions_detect(ctx):
    """Detect sauna sessions from temperature data."""
    result = sessions.refresh_sessions(ctx.obj["db_path"])
    console.print(f"[green]Detected {result['imported']} sessions[/green]")
    if result["skipped"]:
        console.print(f"[yellow]Skipped {result['skipped']} existing sessions[/yellow]")


@sessions_cmd.command("list")
@click.option("--days", default=30, help="Number of days to show")
@click.pass_context
def sessions_list(ctx, days):
    """List recent sauna sessions."""
    end = datetime.now()
    start = end - timedelta(days=days)
    session_list = sessions.get_sessions_for_period(start, end, ctx.obj["db_path"])

    if not session_list:
        console.print("[yellow]No sessions found[/yellow]")
        return

    table = Table(title=f"Sauna Sessions (last {days} days)")
    table.add_column("Date", style="cyan")
    table.add_column("Time")
    table.add_column("Duration", justify="right")
    table.add_column("Peak Temp", justify="right")

    for s in session_list:
        table.add_row(
            s.start_time.strftime("%Y-%m-%d"),
            f"{s.start_time.strftime('%H:%M')} - {s.end_time.strftime('%H:%M')}",
            f"{s.duration_minutes} min",
            f"{s.peak_temperature_c}°C",
        )

    console.print(table)


# Summary commands
@cli.command()
@click.option("--date", help="Date to summarize (YYYY-MM-DD), defaults to yesterday")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def summary_cmd(ctx, date, as_json):
    """Generate a daily energy summary."""
    if date:
        target_date = datetime.fromisoformat(date)
    else:
        target_date = datetime.now() - timedelta(days=1)

    data = summary.get_daily_summary(target_date, ctx.obj["db_path"])

    if as_json:
        console.print(json.dumps(data, indent=2))
    else:
        console.print(summary.format_daily_summary_text(data))


@cli.command()
@click.option("--days", default=7, help="Number of days to include")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def report(ctx, days, as_json):
    """Generate a period summary report."""
    end = datetime.now()
    start = end - timedelta(days=days)

    data = summary.get_period_summary(start, end, ctx.obj["db_path"])

    if as_json:
        console.print(json.dumps(data, indent=2))
    else:
        console.print(summary.format_period_summary_text(data))


@cli.command()
def prompt():
    """Output the agent prompt for LLM analysis.

    The prompt is dynamically generated from config/tariffs.yaml,
    so it always reflects the current tariff rates.
    """
    from .generate_prompt import generate_prompt
    print(generate_prompt())


# Alias for summary command
cli.add_command(summary_cmd, name="summary")

# Register shelly command group
cli.add_command(shelly_cmd, name="shelly")


if __name__ == "__main__":
    cli()
