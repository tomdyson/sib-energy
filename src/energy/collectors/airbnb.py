"""Airbnb reservation collector."""

import csv
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

import httpx

from .. import db


def _generate_uid(start_date: str, identifier: str) -> str:
    """Generate a deterministic UID."""
    return hashlib.md5(f"{start_date}:{identifier}".encode()).hexdigest()


def save_reservations(reservations: List[Dict[str, Any]], db_path: Path) -> Dict[str, int]:
    """Save reservations to database."""
    stats = {"imported": 0, "skipped": 0}
    
    with db.get_connection(db_path) as conn:
        for res in reservations:
            try:
                conn.execute(
                    """
                    INSERT INTO airbnb_reservations 
                    (id, start_date, end_date, status, guest_name, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        res["id"],
                        res["start_date"],
                        res["end_date"],
                        res.get("status"),
                        res.get("guest_name"),
                        res["source"],
                    ),
                )
                stats["imported"] += 1
            except Exception: # likely integrity error if unique constraint existed on id
                # But here ID is primary key.
                # If ID exists, we skip.
                stats["skipped"] += 1
        conn.commit()
    
    return stats


def fetch_from_ical(url: str, db_path: Path) -> Dict[str, int]:
    """Fetch and parse iCal feed."""
    response = httpx.get(url)
    response.raise_for_status()
    content = response.text
    
    reservations = []
    
    # Simple state machine to parse VEVENTs
    lines = content.splitlines()
    current_event = {}
    in_event = False
    
    for line in lines:
        if line.startswith("BEGIN:VEVENT"):
            in_event = True
            current_event = {}
        elif line.startswith("END:VEVENT"):
            in_event = False
            if "start_date" in current_event and "end_date" in current_event:
                # Airbnb iCal end date is exclusive (checkout date).
                # Start date is inclusive (checkin date).
                # UID is provided in iCal.
                current_event["source"] = "ical"
                current_event["status"] = current_event.get("status", "Reserved")
                reservations.append(current_event)
        elif in_event:
            if line.startswith("DTSTART;VALUE=DATE:"):
                # Format: 20260417
                d_str = line.split(":")[1].strip()
                current_event["start_date"] = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
            elif line.startswith("DTEND;VALUE=DATE:"):
                # Format: 20260419
                d_str = line.split(":")[1].strip()
                current_event["end_date"] = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
            elif line.startswith("UID:"):
                current_event["id"] = line.split(":")[1].strip()
            elif line.startswith("SUMMARY:"):
                current_event["status"] = line.split(":")[1].strip()
    
    return save_reservations(reservations, db_path)


def import_from_csv(csv_path: Path, db_path: Path) -> Dict[str, int]:
    """Import reservations from CSV."""
    reservations = []
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Check for empty rows
            if not row.get("Start date") or not row.get("End date"):
                continue
                
            # Parse dates (MM/DD/YYYY based on the sample file)
            try:
                start_dt = datetime.strptime(row["Start date"], "%m/%d/%Y")
                end_dt = datetime.strptime(row["End date"], "%m/%d/%Y")
                
                # Format as YYYY-MM-DD
                start_date = start_dt.strftime("%Y-%m-%d")
                end_date = end_dt.strftime("%Y-%m-%d")
                
                guest_name = row.get("Guest", "").strip()
                
                # Generate a stable ID
                # If guest name is present, use it. If not, maybe just date?
                # But multiple bookings could start same day if one is cancelled? 
                # Let's use start_date + guest_name as key.
                # If guest name is empty, we must rely largely on dates.
                uid = _generate_uid(start_date, guest_name or "unknown")
                
                reservations.append({
                    "id": uid,
                    "start_date": start_date,
                    "end_date": end_date,
                    "status": "Reserved",
                    "guest_name": guest_name,
                    "source": "csv"
                })
            except ValueError:
                # Skip rows with bad date format
                continue
                
    return save_reservations(reservations, db_path)
