"""Generate daily hourly usage pattern report."""

from datetime import datetime, timedelta
from pathlib import Path

from .. import db


def get_hourly_data_by_day(db_path: Path | None = None, days: int = 30) -> list[dict]:
    """Get hourly studio and house consumption for each day.

    Returns a list of dicts with:
        - day: date string (YYYY-MM-DD)
        - day_of_week: 0=Monday, 6=Sunday
        - hours: list of hour numbers with data
        - studio: list of kWh values for studio circuit
        - house: list of kWh values for house (EON - studio)
    """
    with db.get_connection(db_path) as conn:
        # Get the date range
        row = conn.execute(
            "SELECT MAX(DATE(interval_start)) as latest FROM electricity_readings WHERE source = 'eon'"
        ).fetchone()

        if not row["latest"]:
            return []

        end_date = row["latest"]
        # Go back 'days' days, but we need complete days so check what's available
        start_date = (datetime.fromisoformat(end_date) - timedelta(days=days)).strftime("%Y-%m-%d")

        # Get hourly data grouped by day
        rows = conn.execute("""
            WITH hourly AS (
                SELECT
                    DATE(e.interval_start) as day,
                    CAST(STRFTIME('%H', e.interval_start) AS INTEGER) as hour,
                    ROUND(SUM(COALESCE(s.consumption_kwh, 0)), 2) as studio,
                    ROUND(SUM(e.consumption_kwh - COALESCE(s.consumption_kwh, 0)), 2) as house
                FROM electricity_readings e
                LEFT JOIN electricity_readings s ON
                    e.interval_start = s.interval_start
                    AND s.source = 'shelly_studio_phase'
                WHERE e.source = 'eon'
                GROUP BY DATE(e.interval_start), CAST(STRFTIME('%H', e.interval_start) AS INTEGER)
            )
            SELECT
                day,
                GROUP_CONCAT(hour, ',') as hours,
                GROUP_CONCAT(studio, ',') as studio_data,
                GROUP_CONCAT(house, ',') as house_data
            FROM hourly
            WHERE day >= ? AND day <= ?
            GROUP BY day
            ORDER BY day
        """, (start_date, end_date)).fetchall()

        result = []
        for row in rows:
            day_dt = datetime.fromisoformat(row["day"])
            result.append({
                "day": row["day"],
                "day_of_week": day_dt.weekday(),
                "hours": [int(h) for h in row["hours"].split(",")],
                "studio": [float(v) for v in row["studio_data"].split(",")],
                "house": [float(v) for v in row["house_data"].split(",")],
            })

        return result


def get_sauna_days(db_path: Path | None = None, days: int = 30) -> set[str]:
    """Get set of dates that have sauna sessions."""
    with db.get_connection(db_path) as conn:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        rows = conn.execute("""
            SELECT DISTINCT DATE(start_time) as day
            FROM sauna_sessions
            WHERE DATE(start_time) >= ? AND DATE(start_time) <= ?
        """, (start_date, end_date)).fetchall()

        return {row["day"] for row in rows}


def format_day_label(day_str: str, day_of_week: int, is_sauna: bool) -> str:
    """Format a day label like 'Jan 1 (Wed) - Sauna'."""
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dt = datetime.fromisoformat(day_str)

    month_day = dt.strftime("%b %-d")
    label = f"{month_day} ({day_names[day_of_week]})"

    if is_sauna:
        label += " - Sauna"

    return label


def generate_daily_hourly_report(db_path: Path | None = None, days: int = 30) -> str:
    """Generate HTML report with daily hourly usage charts.

    Args:
        db_path: Path to database (uses default if None)
        days: Number of days to include in report

    Returns:
        Complete HTML document as a string
    """
    daily_data = get_hourly_data_by_day(db_path, days)
    sauna_days = get_sauna_days(db_path, days)

    if not daily_data:
        return "<html><body><p>No data available</p></body></html>"

    # Build JavaScript data array
    js_data_items = []
    for data in daily_data:
        is_sauna = data["day"] in sauna_days
        label = format_day_label(data["day"], data["day_of_week"], is_sauna)

        # Format arrays for JavaScript
        hours_js = repr(data["hours"])
        studio_js = repr(data["studio"])
        house_js = repr(data["house"])

        js_data_items.append(f"""            {{
                day: '{data["day"]}',
                label: '{label}',
                hours: {hours_js},
                studio: {studio_js},
                house: {house_js}
            }}""")

    js_data = ",\n".join(js_data_items)

    # Get date range for header
    first_day = datetime.fromisoformat(daily_data[0]["day"]).strftime("%B %-d, %Y")
    last_day = datetime.fromisoformat(daily_data[-1]["day"]).strftime("%B %-d, %Y")
    generated_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Daily Hourly Usage Patterns</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        .gradient-bg {{
            background: linear-gradient(135deg, #1e3a5f 0%, #0d1b2a 100%);
        }}
        .card {{
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
        }}
    </style>
</head>
<body class="gradient-bg min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <header class="text-center mb-8">
            <h1 class="text-4xl font-bold text-white mb-2">Daily Hourly Usage Patterns</h1>
            <p class="text-blue-200">{first_day} - {last_day}</p>
            <p class="text-blue-300 text-sm mt-2">Studio (amber) vs House Only (blue) - kWh per hour</p>
        </header>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        </div>

        <footer class="text-center text-blue-200 text-sm py-8">
            <p>Generated {generated_date}</p>
        </footer>
    </div>

    <script>
        const dailyData = [
{js_data}
        ];

        const gridContainer = document.querySelector('.grid');

        dailyData.forEach((day, index) => {{
            const card = document.createElement('div');
            card.className = 'card rounded-xl p-4 shadow-lg';

            const studioTotal = day.studio.reduce((a, b) => a + b, 0).toFixed(1);
            const houseTotal = day.house.reduce((a, b) => a + b, 0).toFixed(1);
            const total = (parseFloat(studioTotal) + parseFloat(houseTotal)).toFixed(1);

            card.innerHTML = `
                <div class="flex justify-between items-center mb-2">
                    <h3 class="font-bold text-gray-800">${{day.label}}</h3>
                    <span class="text-xs text-gray-500">${{total}} kWh</span>
                </div>
                <div class="text-xs text-gray-400 mb-2">
                    <span class="text-amber-600">Studio: ${{studioTotal}}</span> |
                    <span class="text-blue-600">House: ${{houseTotal}}</span>
                </div>
                <div class="h-40">
                    <canvas id="chart-${{index}}"></canvas>
                </div>
            `;

            gridContainer.appendChild(card);

            const ctx = document.getElementById(`chart-${{index}}`).getContext('2d');
            new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: day.hours.map(h => `${{h}}:00`),
                    datasets: [{{
                        label: 'Studio',
                        data: day.studio,
                        borderColor: 'rgb(245, 158, 11)',
                        backgroundColor: 'rgba(245, 158, 11, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 0,
                        borderWidth: 1.5
                    }}, {{
                        label: 'House',
                        data: day.house,
                        borderColor: 'rgb(59, 130, 246)',
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 0,
                        borderWidth: 1.5
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{
                        intersect: false,
                        mode: 'index'
                    }},
                    plugins: {{
                        legend: {{ display: false }}
                    }},
                    scales: {{
                        x: {{
                            display: true,
                            ticks: {{
                                maxTicksLimit: 6,
                                font: {{ size: 9 }},
                                color: '#9ca3af'
                            }},
                            grid: {{ display: false }}
                        }},
                        y: {{
                            display: true,
                            min: 0,
                            max: 14,
                            ticks: {{
                                stepSize: 4,
                                font: {{ size: 9 }},
                                color: '#9ca3af'
                            }},
                            grid: {{
                                color: 'rgba(0,0,0,0.05)'
                            }}
                        }}
                    }}
                }}
            }});
        }});
    </script>
</body>
</html>'''

    return html
