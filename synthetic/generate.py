"""Build the synthetic IHS database in an Oracle Database Free container.

Everything this script writes is FAKE: participants, device data, mood and
survey answers are drawn from a seeded random generator. Only the *shape*
(object names, column names, Oracle types, cohort drift) comes from the real
catalog metadata, via spec/objects.yaml.

    uv run --project synthetic python synthetic/generate.py [--seed N]

Steps: drop and recreate the cohort schemas (IHS_2024, IHS_2025, IHS_2026) and
the DATALAB_RO account and roles, create tables and views from the spec,
generate each cohort's rows, and bulk-load them. Safe to re-run.
"""

from __future__ import annotations

import argparse
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import oracledb
import yaml

from guard import MARKER_SCHEMA, create_marker, require_local_dsn, require_synthetic_server

HERE = Path(__file__).resolve().parent
DSN = os.environ.get("SYNTH_ORACLE_DSN", "localhost:1522/FREEPDB1")
ADMIN_PWD = os.environ.get("SYNTH_ORACLE_PWD", "SynthDev2026")  # dev-only, see README
RO_USER = "DATALAB_RO"
RO_PWD = os.environ.get("SYNTH_RO_PWD", "datalab_ro")  # dev-only, see README
ROLES = ("IHS_2025_RO", "IHS_2026_RO", "IHS_2026_ROLE")  # read-only, read-only, over-privileged (as in reality)
TSTZ_FMT = "YYYY-MM-DD HH24:MI:SS TZH:TZM"
UTC = timezone.utc

ZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Phoenix"]
ZONE_WEIGHTS = [0.40, 0.25, 0.10, 0.20, 0.05]
WATCH_MODELS = ["Watch6,1", "Watch6,2", "Watch6,14", "Watch7,1", "Watch7,3"]
IPHONES = ["iPhone14,5", "iPhone15,2", "iPhone16,1"]
ANDROIDS = ["Pixel 8", "SM-S918U"]
FITBITS = ["Charge 6", "Inspire 3", "Sense 2"]
WAVE_VIEWS = {0: "VW_BASELINE_SURVEY", 1: "VW_SEP_SURVEY", 2: "VW_DEC_SURVEY", 3: "VW_MAR_SURVEY", 4: "VW_JUN_SURVEY"}
MOOD_COMMENTS = ["Long call shift", "Good day off", "Tired", "Night float", "Busy clinic", "Felt productive"]


# ---------------------------------------------------------------- spec ----

def load_spec() -> tuple[dict, dict]:
    objects = yaml.safe_load((HERE / "spec" / "objects.yaml").read_text())["objects"]
    config = yaml.safe_load((HERE / "spec" / "cohorts.yaml").read_text())
    return objects, config


def cohort_columns(obj: dict, cohort: str) -> list[tuple[str, str]]:
    """Column list for one cohort: the reference columns with that cohort's drift applied."""
    cols = [tuple(c) for c in obj["columns"]]
    drift = (obj.get("drift") or {}).get(cohort, {})
    cols = [c for c in cols if c[0] not in set(drift.get("drop", []))]
    for name, typ in drift.get("set", []):
        idx = next((i for i, c in enumerate(cols) if c[0] == name), None)
        if idx is None:
            cols.append((name, typ))
        else:
            cols[idx] = (name, typ)
    return cols


# ----------------------------------------------------------- time utils ----

def at(d: date, hours: float, tz: ZoneInfo) -> datetime:
    """Local wall-clock time `hours` after midnight of `d` in `tz` (may roll past midnight)."""
    return datetime(d.year, d.month, d.day, tzinfo=tz) + timedelta(hours=hours)


def tstz(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S %:z")  # the text form TO_TIMESTAMP_TZ(.., TSTZ_FMT) reads


def offset_s(d: datetime) -> int:
    return int(d.utcoffset().total_seconds())


def midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


# -------------------------------------------------------------- people ----

@dataclass
class Night:
    bed: datetime  # aware, local
    wake: datetime
    deep: int  # stage minutes; latency = minutes from bed to first sleep
    light: int
    rem: int
    awake: int
    latency: int


@dataclass
class Day:
    d: date
    tz: ZoneInfo
    worn: bool
    steps: int
    rhr: int
    hrv: float
    active_min: int
    night: Night | None     # the sleep that ends on the morning of `d`
    nap: tuple[datetime, int] | None
    mood: int | None


@dataclass
class Person:
    pid: str
    study_id: int | None
    internal_id: str        # PARTICIPANTID (platform id; also used as the Garmin/Oura user id)
    device: str
    phone: str
    watch: str | None
    fitbit: str | None
    old_watch: bool         # older watchOS: sleep is plain "Asleep", no stages
    hk_third_party: bool    # iPhone user whose Garmin/Oura app also writes to HealthKit
    enroll: date
    end: date
    withdraw: date | None
    zones: list[tuple[date, ZoneInfo]]
    sex: int
    days: list[Day] = field(default_factory=list)

    def zone(self, d: date) -> ZoneInfo:
        return [z for since, z in self.zones if since <= d][-1]


def make_people(rng: random.Random, name: str, c: dict, config: dict, has_oura: bool) -> list[Person]:
    people = []
    mix = c.get("device_mix", config["device_mix"])
    devices, weights = list(mix), list(mix.values())
    yy = name[-2:]
    for n in range(1, c["participants"] + 1):
        device = rng.choices(devices, weights)[0]
        if device == "Oura" and not has_oura:
            device = "Fitbit"
        iphone = device == "AppleWatch" or rng.random() < 0.6
        enroll = c["window_start"] + timedelta(days=rng.randint(0, 45))
        end, withdraw = c["data_end"], None
        if rng.random() < 0.15:  # dropout: data stop partway, some formally withdraw
            span = (c["data_end"] - enroll).days
            end = enroll + timedelta(days=rng.randint(min(30, span), span))
            withdraw = end if rng.random() < 0.6 else None
        home = ZoneInfo(rng.choices(ZONES, ZONE_WEIGHTS)[0])
        zones = [(date.min, home)]
        if rng.random() < 0.35:  # many interns move to a new city just before internship
            other = ZoneInfo(rng.choice([z for z in ZONES if z != home.key]))
            zones.append((c["internship_start"] - timedelta(days=rng.randint(3, 20)), other))
        people.append(Person(
            pid=f"{c['id_prefix']}-{n:04d}",
            study_id=None if rng.random() < 0.06 else int(f"9{yy}{n:04d}"),  # None = screened, never enrolled
            internal_id=f"{rng.getrandbits(128):032x}",
            device=device,
            phone=rng.choice(IPHONES) if iphone else rng.choice(ANDROIDS),
            watch=rng.choice(WATCH_MODELS) if device == "AppleWatch" else None,
            fitbit=rng.choice(FITBITS) if device == "Fitbit" else None,
            old_watch=device == "AppleWatch" and name == "IHS_2024" and rng.random() < 0.15,
            hk_third_party=iphone and device in ("Garmin", "Oura") and rng.random() < 0.7,
            enroll=enroll, end=end, withdraw=withdraw, zones=zones, sex=rng.choice([1, 2]),
        ))
    return people


def simulate_days(rng: random.Random, p: Person, internship: date) -> None:
    """Latent daily behaviour for one person; device writers below render it per vendor."""
    base_steps, base_rhr, base_hrv = rng.uniform(5500, 11000), rng.uniform(52, 74), rng.uniform(25, 75)
    base_sleep, base_bed, base_mood = rng.uniform(6.3, 8.0), rng.uniform(22.3, 24.3), rng.uniform(5.5, 8.0)
    wear, mood_resp = rng.uniform(0.75, 0.97), rng.uniform(0.5, 0.9)  # share of days worn / mood answered
    gap, d = 0, p.enroll
    while d < p.end:
        intern = d >= internship
        if gap == 0 and rng.random() < 0.01:
            gap = rng.randint(3, 14)  # device not synced / charger lost
        gap = max(0, gap - 1)
        worn = gap == 0 and rng.random() < wear
        tz = p.zone(d)
        night = None
        if worn and rng.random() < 0.93:
            hours = min(10.0, max(3.5, rng.gauss(base_sleep - (0.6 if intern else 0), 0.9)))
            bed = at(d - timedelta(days=1), rng.gauss(base_bed + (0.4 if intern else 0), 0.8), tz)
            asleep = int(hours * 60)
            deep = int(asleep * rng.uniform(0.12, 0.22))
            rem = int(asleep * rng.uniform(0.18, 0.26))
            awake = rng.randint(5, 50)
            latency = rng.randint(2, 25)
            night = Night(bed, bed + timedelta(minutes=latency + asleep + awake), deep,
                          asleep - deep - rem, rem, awake, latency)
        nap = (at(d, rng.uniform(12.5, 17.5), tz), rng.randint(15, 90)) if worn and rng.random() < 0.06 else None
        weekend = d.weekday() >= 5
        steps = rng.lognormvariate(0, 0.3) * base_steps * (0.85 if intern else 1.0) * (1.1 if weekend else 1.0)
        mood = None
        if rng.random() < mood_resp * (0.75 if intern else 1.0):
            mood = min(10, max(1, round(rng.gauss(base_mood - (0.7 if intern else 0), 1.4))))
        p.days.append(Day(
            d=d, tz=tz, worn=worn, steps=int(steps),
            rhr=int(rng.gauss(base_rhr + (2 if intern else 0), 2.5)),
            hrv=max(8.0, rng.gauss(base_hrv - (4 if intern else 0), 8)),
            active_min=max(0, int(rng.gauss(35 if not intern else 25, 18))),
            night=night, nap=nap, mood=mood,
        ))
        d += timedelta(days=1)


# ------------------------------------------------------------- writers ----
# Each writer appends dicts (column -> value) to rows[TABLE]. Missing columns
# load as NULL. Quirks the real pipelines have to cope with are marked QUIRK.

class Cohort:
    def __init__(self, rng: random.Random, name: str, c: dict, tables: set[str], items: dict):
        self.rng, self.name, self.c, self.tables, self.items = rng, name, c, tables, items
        self.rows: dict[str, list[dict]] = {t: [] for t in tables}

    def add(self, table: str, row: dict) -> None:
        if table in self.tables:
            self.rows[table].append(row)

    def key(self) -> bytes:
        return self.rng.randbytes(16)

    def inserted(self, after: datetime, max_hours: float = 12) -> datetime:
        """INSERTEDDATE: a UTC load time some hours after the event."""
        return (after + timedelta(hours=self.rng.uniform(0.5, max_hours))).astimezone(UTC)


def write_fitbit(co: Cohort, p: Person) -> None:
    r = co.rng
    for day in p.days:
        if not day.worn:
            continue
        zones_missing = r.random() < 0.15  # QUIRK: HR-zone minutes NULL; pipeline falls back to tracker minutes
        very, fairly = int(day.active_min * 0.4), int(day.active_min * 0.6)
        light, sedentary = r.randint(120, 280), r.randint(550, 820)
        co.add("FITBITDAILYDATA", {
            "PARTICIPANTIDENTIFIER": p.pid, "RECORD_DATE": midnight(day.d),
            "TRACKERSTEPS": day.steps, "STEPS": day.steps + r.randint(0, 150),
            "RESTINGHEARTRATE": None if r.random() < 0.05 else day.rhr,
            "HRVDAILYRMSSD": None if r.random() < 0.1 else round(day.hrv, 1),
            "HRVDEEPRMSSD": None if r.random() < 0.1 else round(day.hrv * r.uniform(0.9, 1.2), 1),
            "HEARTRATEZONEOUTOFRANGEMINUTES": None if zones_missing else 1440 - day.active_min - 60,
            "HEARTRATEZONEFATBURNMINUTES": None if zones_missing else int(day.active_min * 0.7),
            "HEARTRATEZONECARDIOMINUTES": None if zones_missing else int(day.active_min * 0.2),
            "HEARTRATEZONEPEAKMINUTES": None if zones_missing else int(day.active_min * 0.05),
            "TRACKERMINUTESVERYACTIVE": very, "TRACKERMINUTESFAIRLYACTIVE": fairly,
            "TRACKERMINUTESLIGHTLYACTIVE": light, "TRACKERMINUTESSEDENTARY": sedentary,
            "MINUTESVERYACTIVE": very, "MINUTESFAIRLYACTIVE": fairly,
            "MINUTESLIGHTLYACTIVE": light, "MINUTESSEDENTARY": sedentary,
            "CALORIES": r.randint(1700, 3200), "CALORIESBMR": r.randint(1400, 1800),
            "ACTIVITYCALORIES": r.randint(200, 1200), "DISTANCE": round(day.steps * 0.00075, 2),
            "TRACKERDISTANCE": round(day.steps * 0.00075, 2), "FLOORS": r.randint(0, 25),
            "BREATHINGRATE": round(r.uniform(12, 18), 1), "SPO2AVG": round(r.uniform(94, 99), 1),
            "BODYWEIGHT": round(r.uniform(55, 95), 1) if r.random() < 0.02 else None,
            "MODIFIEDDATE": co.inserted(at(day.d, 23.9, day.tz), 30),
        })
        sleeps = []
        if day.night:
            sleeps.append((day.night.bed, day.night.wake, "true", day.night))
        if day.nap:
            sleeps.append((day.nap[0], day.nap[0] + timedelta(minutes=day.nap[1]), "false", None))
        for start, end, main, n in sleeps:
            inbed = int((end - start).total_seconds() // 60)
            staged = n is not None and r.random() < 0.95  # QUIRK: 'classic' logs have no stage minutes
            asleep = (n.deep + n.light + n.rem) if n else inbed - r.randint(0, 5)
            co.add("FITBITSLEEPLOGS", {
                "PARTICIPANTIDENTIFIER": p.pid, "STARTDATE": start, "ENDDATE": end,
                "STARTDATE_UTC": start.astimezone(UTC).replace(tzinfo=None),  # IHS_2024 only
                "DURATION": inbed * 60000, "EFFICIENCY": round(100 * asleep / max(inbed, 1)), "INFOCODE": 0,
                "MINUTESASLEEP": asleep, "MINUTESAWAKE": inbed - asleep, "TIMEINBED": inbed,
                "MINUTESTOFALLASLEEP": n.latency if n else 0, "MINUTESAFTERWAKEUP": r.randint(0, 5),
                "TYPE": "stages" if staged else "classic",
                "SLEEPLEVELDEEP": n.deep if staged else None, "SLEEPLEVELLIGHT": n.light if staged else None,
                "SLEEPLEVELREM": n.rem if staged else None, "SLEEPLEVELWAKE": inbed - asleep if staged else None,
                "SLEEPLEVELASLEEP": None if staged else asleep, "SLEEPLEVELAWAKE": None if staged else r.randint(0, 5),
                "SLEEPLEVELRESTLESS": None if staged else inbed - asleep,
                "ISMAINSLEEP": main, "LOGTYPE": "auto_detected" if r.random() < 0.97 else "manual",
                "MODIFIEDDATE": co.inserted(end),
            })


def garmin_summary_id(r: random.Random, epoch: int) -> str:
    return f"x{r.getrandbits(32):08x}-{epoch:x}"


def write_garmin(co: Cohort, p: Person) -> None:
    r = co.rng
    goal = r.choice([7500, 8000, 10000])
    for day in p.days:
        if not day.worn:
            continue
        start = at(day.d, 0, day.tz)
        epoch = int(start.timestamp())
        full = {
            "PARTICIPANTID": p.internal_id, "PARTICIPANTIDENTIFIER": p.pid,
            "SUMMARYID": garmin_summary_id(r, epoch), "STARTTIMEINSECONDS": epoch,
            "STARTTIMEOFFSETINSECONDS": offset_s(start), "DURATIONINSECONDS": 86400,
            "CALENDARDATE": midnight(day.d), "STEPS": day.steps, "STEPSGOAL": goal,
            "RESTINGHEARTRATEINBEATSPERMINUTE": None if r.random() < 0.04 else day.rhr,
            "MODERATEINTENSITYDURATIONINSECONDS": int(day.active_min * 0.7) * 60,
            "VIGOROUSINTENSITYDURATIONINSECONDS": int(day.active_min * 0.3) * 60,
            "ACTIVEKILOCALORIES": r.randint(200, 1100), "BMRKILOCALORIES": r.randint(1400, 1800),
            "DISTANCEINMETERS": round(day.steps * 0.75, 1), "ACTIVETIMEINSECONDS": r.randint(3000, 12000),
            "FLOORSCLIMBED": r.randint(0, 20), "MINHEARTRATEINBEATSPERMINUTE": day.rhr - r.randint(2, 8),
            "MAXHEARTRATEINBEATSPERMINUTE": r.randint(120, 175), "AVERAGEHEARTRATEINBEATSPERMINUTE": day.rhr + r.randint(10, 25),
            "AVERAGESTRESSLEVEL": r.randint(18, 50), "MAXSTRESSLEVEL": r.randint(70, 99),
            "STRESSQUALIFIER": r.choice(["calm", "balanced", "stressful", "very_stressful"]),
            "INSERTEDDATE": co.inserted(at(day.d, 24, day.tz), 20),
        }
        if r.random() < 0.06:  # QUIRK: an earlier partial-day row, superseded by a later INSERTEDDATE
            frac = r.uniform(0.3, 0.8)
            co.add("GARMINDAILYSUMMARY", {**full, "SUMMARYID": garmin_summary_id(r, epoch),
                                          "DURATIONINSECONDS": int(86400 * frac), "STEPS": int(day.steps * frac),
                                          "INSERTEDDATE": full["INSERTEDDATE"] - timedelta(hours=r.uniform(8, 16))})
        co.add("GARMINDAILYSUMMARY", full)
        if r.random() < 0.01:  # QUIRK: exact duplicate row
            co.add("GARMINDAILYSUMMARY", dict(full))

        n = day.night
        if n:
            bed_epoch = int(n.bed.timestamp())
            asleep_s = (n.deep + n.light + n.rem) * 60
            unmeasurable = r.choice([0, 0, 0, 60, 120])
            score = r.randint(40, 95)
            sleep = {
                "PARTICIPANTID": p.internal_id, "PARTICIPANTIDENTIFIER": p.pid,
                "SUMMARYID": garmin_summary_id(r, bed_epoch), "STARTTIMEINSECONDS": bed_epoch,
                "STARTTIMEOFFSETINSECONDS": offset_s(n.bed), "DURATIONINSECONDS": asleep_s + unmeasurable,
                # QUIRK: CALENDARDATE is occasionally the bed date instead of the wake date
                "CALENDARDATE": midnight(n.bed.date() if r.random() < 0.01 else day.d),
                "UNMEASURABLESLEEPINSECONDS": unmeasurable, "DEEPSLEEPDURATIONINSECONDS": n.deep * 60,
                "LIGHTSLEEPDURATIONINSECONDS": n.light * 60, "REMSLEEPINSECONDS": n.rem * 60,
                "AWAKEDURATIONINSECONDS": n.awake * 60,
                "VALIDATION": r.choices(["ENHANCED_FINAL", "AUTO_FINAL", "AUTO_TENTATIVE", "MANUAL", "DEVICE"],
                                        [0.86, 0.07, 0.03, 0.02, 0.02])[0],
                "OVERALLSLEEPSCOREVALUE": score,
                "OVERALLSLEEPSCOREQUALIFIERKEY": "EXCELLENT" if score >= 90 else "GOOD" if score >= 80 else "FAIR" if score >= 60 else "POOR",
                "TOTALNAPDURATIONINSECONDS": day.nap[1] * 60 if day.nap else None,
                "INSERTEDDATE": co.inserted(n.wake),
            }
            if r.random() < 0.05:  # QUIRK: tentative version first, final version loaded later
                co.add("GARMINSLEEPSUMMARY", {**sleep, "VALIDATION": "ENHANCED_TENTATIVE",
                                              "DURATIONINSECONDS": sleep["DURATIONINSECONDS"] - 600,
                                              "INSERTEDDATE": sleep["INSERTEDDATE"] - timedelta(hours=3)})
            co.add("GARMINSLEEPSUMMARY", sleep)
            if r.random() < 0.02:  # QUIRK: the same FINAL night re-sent later
                co.add("GARMINSLEEPSUMMARY", {**sleep, "INSERTEDDATE": sleep["INSERTEDDATE"] + timedelta(days=2)})
            if day.nap:  # naps carry no PARTICIPANTIDENTIFIER; join on PARTICIPANTID
                co.add("GARMINSLEEPSUMMARY_NAPS", {
                    "SUMMARYID": sleep["SUMMARYID"] if r.random() < 0.8 else None,
                    "NAPSTARTTIMEINSECONDS": int(day.nap[0].timestamp()), "NAPOFFSETINSECONDS": offset_s(day.nap[0]),
                    "NAPDURATIONINSECONDS": day.nap[1] * 60, "PARTICIPANTID": p.internal_id,
                    "NAPVALIDATION": r.choices(["AUTO", "DEVICE", "MANUAL"], [0.6, 0.3, 0.1])[0],
                })
            # QUIRK: some HRV CALENDARDATEs carry a time: UTC midnight shifted to local (previous day, 17:00-20:00)
            cal = midnight(day.d)
            if r.random() < 0.03:
                cal = cal + timedelta(seconds=offset_s(n.bed))
            hrv = {
                "PARTICIPANTID": p.internal_id, "PARTICIPANTIDENTIFIER": p.pid,
                "SUMMARYID": f"hrv{garmin_summary_id(r, bed_epoch)}", "STARTTIMEINSECONDS": bed_epoch,
                "STARTTIMEOFFSETINSECONDS": offset_s(n.bed), "DURATIONINSECONDS": asleep_s, "CALENDARDATE": cal,
                "LASTNIGHTAVG": None if r.random() < 0.05 else round(day.hrv),
                "LASTNIGHT5MINHIGH": round(day.hrv * r.uniform(1.3, 1.8)), "INSERTEDDATE": co.inserted(n.wake),
            }
            co.add("GARMINHRVSUMMARY", hrv)
            if r.random() < 0.03:  # QUIRK: re-sent with a later INSERTEDDATE
                co.add("GARMINHRVSUMMARY", {**hrv, "INSERTEDDATE": hrv["INSERTEDDATE"] + timedelta(days=1)})


def hk_sample(co: Cohort, p: Person, table: str, start: datetime, end: datetime, value, units: str,
              source: str, product: str | None, **extra) -> dict:
    row = {
        "HEALTHKITSAMPLEKEY": co.key(), "PARTICIPANTIDENTIFIER": p.pid, "STARTDATE": start, "RECORD_DATE": end,
        "VALUE": str(value), "UNITS": units, "SOURCENAME": source, "SOURCEPRODUCTTYPE": product,
        "SOURCEIDENTIFIER": "com.apple.health" if source == "Apple Watch" else f"com.synthetic.{source.lower()}",
        "INSERTEDDATE": co.inserted(end), **extra,
    }
    if product and product.startswith("Watch"):
        row |= {"DEVICENAME": "Apple Watch", "DEVICEMODEL": "Watch", "DEVICEMANUFACTURER": "Apple Inc."}
    co.add(table, row)
    return row


def day_window(p: Person, day: Day, moved_after: date | None, r: random.Random) -> tuple[datetime, datetime]:
    """HealthKit daily bucket. QUIRK: after a move, some buckets stay aligned to the old zone's midnight."""
    start = at(day.d, 0, day.tz)
    if moved_after and day.d >= moved_after and r.random() < 0.25:
        start = datetime(day.d.year, day.d.month, day.d.day, tzinfo=p.zones[0][1]).astimezone(day.tz)
    return start, start + timedelta(days=1)


def write_healthkit(co: Cohort, p: Person) -> None:
    r = co.rng
    watch = p.device == "AppleWatch"
    phone_steps = p.phone.startswith("iPhone") and not watch and r.random() < 0.3
    if not (watch or p.hk_third_party or phone_steps):
        return
    third_party = "Connect" if p.device == "Garmin" else "Oura"
    moved_after = p.zones[1][0] if len(p.zones) > 1 else None
    for day in p.days:
        if not day.worn and not phone_steps:
            continue
        if watch or phone_steps:  # daily step statistics (phone-only users count fewer steps)
            start, end = day_window(p, day, moved_after, r)
            value = day.steps if watch else int(day.steps * r.uniform(0.4, 0.8))
            row = {"HEALTHKITSTATISTICKEY": co.key(), "PARTICIPANTIDENTIFIER": p.pid, "STARTDATE": start,
                   "RECORD_DATE": end, "VALUE": value, "UNITS": "count", "INSERTEDDATE": co.inserted(end)}
            if r.random() < 0.06:  # QUIRK: partial count loaded earlier; keep the latest INSERTEDDATE
                co.add("HEALTHKITSTATISTICS_DAILYSTEPS", {**row, "HEALTHKITSTATISTICKEY": co.key(),
                                                          "VALUE": int(value * r.uniform(0.3, 0.8)),
                                                          "INSERTEDDATE": row["INSERTEDDATE"] - timedelta(hours=10)})
            co.add("HEALTHKITSTATISTICS_DAILYSTEPS", row)
        if not watch:
            if p.hk_third_party and day.worn:  # QUIRK: iPhone-sourced rows the "Watch" filter must drop
                hk_sample(co, p, "HEALTHKITSAMPLES_RESTINGHEARTRATE", at(day.d, 0, day.tz), at(day.d, 23.9, day.tz),
                          day.rhr, "count/min", third_party, p.phone)
                if day.night and r.random() < 0.5:
                    hk_sample(co, p, "HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL", day.night.bed, day.night.wake,
                              "InBed", None, third_party, p.phone)
            continue
        if not day.worn:
            continue
        product = p.watch if r.random() > 0.01 else None  # QUIRK: rare NULL SOURCEPRODUCTTYPE
        # Resting HR: usually one sample spanning the day, sometimes two (a later one supersedes).
        for _ in range(2 if r.random() < 0.15 else 1):
            start = at(day.d, r.uniform(0, 2), day.tz)
            end = at(day.d, r.uniform(20, 26), day.tz)  # QUIRK: some end after midnight
            hk_sample(co, p, "HEALTHKITSAMPLES_RESTINGHEARTRATE", start, end, day.rhr + r.randint(-2, 2),
                      "count/min", "Apple Watch", product)
        if r.random() < 0.03:
            hk_sample(co, p, "HEALTHKITSAMPLES_RESTINGHEARTRATE", at(day.d, 7, day.tz), at(day.d, 7, day.tz),
                      day.rhr + 5, "count/min", "Withings", p.phone)
        start, end = day_window(p, day, moved_after, r)
        act = {"HEALTHKITACTIVITYSUMMARYKEY": co.key(), "PARTICIPANTIDENTIFIER": p.pid, "STARTDATE": start,
               "ENDDATE": end, "ACTIVEENERGYBURNED": r.randint(150, 900), "ACTIVEENERGYBURNEDGOAL": 500,
               "APPLEEXERCISETIME": day.active_min, "APPLEEXERCISETIMEGOAL": 30,
               "APPLESTANDHOURS": r.randint(5, 14), "APPLESTANDHOURSGOAL": 12, "INSERTEDDATE": co.inserted(end)}
        if r.random() < 0.05:  # QUIRK: partial-day summary loaded first
            co.add("HEALTHKITACTIVITYSUMMARIES", {**act, "HEALTHKITACTIVITYSUMMARYKEY": co.key(),
                                                  "APPLEEXERCISETIME": day.active_min // 2,
                                                  "INSERTEDDATE": act["INSERTEDDATE"] - timedelta(hours=9)})
        co.add("HEALTHKITACTIVITYSUMMARIES", act)
        if day.night:
            write_hk_night(co, p, day, product)


def write_hk_night(co: Cohort, p: Person, day: Day, product: str | None) -> None:
    r, n = co.rng, day.night
    meta = f'{{"HKTimeZone":"{day.tz.key}"}}'
    rows = [hk_sample(co, p, "HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL", n.bed, n.wake, "InBed", None,
                      "Apple Watch", product, METADATA=meta)]
    t = n.bed + timedelta(minutes=n.latency)
    stop = n.wake - timedelta(minutes=r.randint(0, 10))
    while t < stop:
        if p.old_watch:
            stage, length = "Asleep", (stop - t).total_seconds() / 60
        else:
            stage = r.choices(["AsleepCore", "AsleepDeep", "AsleepREM", "Awake"], [0.5, 0.2, 0.22, 0.08])[0]
            length = r.randint(2, 8) if stage == "Awake" else r.randint(10, 60)
        seg_end = min(stop, t + timedelta(minutes=length))
        if stage != "Awake" and r.random() < 0.03:  # QUIRK: overlapping stage record from a re-sync
            hk_sample(co, p, "HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL", t - timedelta(minutes=5), seg_end,
                      "AsleepCore", None, "Apple Watch", product, METADATA=meta)
        rows.append(hk_sample(co, p, "HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL", t, seg_end, stage, None,
                              "Apple Watch", product, METADATA=meta))
        t = seg_end
    if r.random() < 0.01:  # QUIRK: whole night duplicated verbatim
        for row in rows:
            co.add("HEALTHKITSAMPLES_SLEEPANALYSISINTERVAL", dict(row))
    for _ in range(r.randint(2, 5)):  # HRV (SDNN) spot checks during the night
        s = n.bed + timedelta(minutes=r.uniform(30, max(31, (n.wake - n.bed).total_seconds() / 60 - 30)))
        hk_sample(co, p, "HEALTHKITSAMPLES_HEARTRATEVARIABILITY", s, s + timedelta(seconds=60),
                  round(day.hrv * r.uniform(0.7, 1.3), 4), "ms", "Apple Watch", product)


def write_oura(co: Cohort, p: Person) -> None:
    r = co.rng
    for day in p.days:
        if not day.worn:
            continue
        start = at(day.d, 4, day.tz)  # Oura days start at 04:00 local
        high, med = r.randint(0, 1800), r.randint(600, 3600)
        row = {
            "DAILYACTIVITYKEY": f"{r.getrandbits(128):032x}", "PARTICIPANTID": p.internal_id,
            "PARTICIPANTIDENTIFIER": p.pid, "ID": f"{r.getrandbits(64):016x}", "SCORE": r.randint(50, 99),
            "STEPS": day.steps, "ACTIVECALORIES": r.randint(150, 900), "TOTALCALORIES": r.randint(1800, 3200),
            "HIGHACTIVITYTIME": high, "MEDIUMACTIVITYTIME": med, "LOWACTIVITYTIME": r.randint(7200, 18000),
            "SEDENTARYTIME": r.randint(20000, 40000), "RESTINGTIME": r.randint(20000, 32000),
            "NONWEARTIME": r.randint(0, 5000), "EQUIVALENTWALKINGDISTANCE": int(day.steps * 0.75),
            "TARGETCALORIES": 500, "TARGETMETERS": 8000, "INACTIVITYALERTS": r.randint(0, 3),
            "AVERAGEMETMINUTES": round(r.uniform(1.1, 2.0), 2), "EVENT_DAY": midnight(day.d),
            "TIMESTAMP_START": start, "INSERTEDDATE": co.inserted(start + timedelta(days=1)),
        }
        if r.random() < 0.15:  # QUIRK: early sync with a tiny step count, replaced later
            co.add("OURADAILYACTIVITY", {**row, "DAILYACTIVITYKEY": f"{r.getrandbits(128):032x}",
                                         "STEPS": r.randint(0, 400), "INSERTEDDATE": row["INSERTEDDATE"] - timedelta(hours=20)})
        co.add("OURADAILYACTIVITY", row)


def write_mood(co: Cohort, p: Person) -> None:
    r = co.rng
    for day in p.days:
        if day.mood is None:
            continue
        start = at(day.d, r.uniform(18, 24.2), day.tz)  # a few answered just after midnight
        co.add("VW_DAILY_MOOD", {
            "PARTICIPANTIDENTIFIER": p.pid, "USERID": p.study_id, "MOOD_STARTDATE": start,
            "MOOD_ENDDATE": start + timedelta(seconds=r.randint(10, 180)), "MOOD_SCORE": str(day.mood),
            "MOOD_COMMENT": r.choice(MOOD_COMMENTS) if r.random() < 0.05 else None,
        })


def write_surveys(co: Cohort, p: Person, waves: list, survey_keys: dict) -> dict[int, date]:
    """Baseline + quarterly surveys, in long form (SURVEY*RESULTS) and wide form (VW_*_SURVEY)."""
    r, c = co.rng, co.c
    depr = r.betavariate(1.5, 5)  # latent tendency toward higher PHQ-9 items
    done = {}
    for wave, survey, offset in waves:
        opens = p.enroll + timedelta(days=r.randint(0, 10)) if offset is None else c["internship_start"] + timedelta(days=offset)
        start = at(opens, r.uniform(0, 21 * 24), p.zone(opens))
        if start.date() >= min(p.end, c["data_end"]) or r.random() > (0.95 - 0.06 * wave):
            continue
        end = start + timedelta(minutes=r.uniform(4, 25))
        version = 2 if wave == 1 and co.name != "IHS_2024" and r.random() < 0.5 else 1  # Q1 revised mid-wave
        lift = 0 if wave == 0 else 0.5
        answers = {k: min(3, max(0, round(r.gauss(depr * 3 + lift, 0.7)) - 1)) for k in co.items["PHQ9"]["items"]}
        answers["substance_tobacco"] = 0 if r.random() < 0.9 else r.randint(1, 4)
        answers["substance_alcohol"] = r.choices(range(5), [0.2, 0.3, 0.3, 0.15, 0.05])[0]
        result_key = co.key()
        platform = "iOS" if p.phone.startswith("iPhone") else "Android"
        co.add("SURVEYRESULTS", {
            "SURVEYRESULTKEY": result_key, "SURVEYKEY": survey_keys[(survey, version)], "SURVEYNAME": survey,
            "SURVEYVERSION": version, "PARTICIPANTIDENTIFIER": p.pid, "SURVEYTASKKEY": co.key(), "TYPE": "Survey",
            "STARTDATE": start, "ENDDATE": end, "DEVICEPLATFORM": platform, "DEVICENAME": p.phone,
            "DEVICEOSVERSION": "17.5.1" if platform == "iOS" else "14", "INSERTEDDATE": co.inserted(end),
            "SCHEDULEID": f"sched-{wave}", "SCHEDULENAME": survey, "SCHEDULECATEGORY": "Baseline" if wave == 0 else "Quarterly",
            "USERTYPE": "Participant", "USER_EMAIL": f"{p.pid.lower()}@example.invalid", "LOCALE": "en-US",
        })
        for i, (item, value) in enumerate(answers.items()):
            t = start + timedelta(seconds=20 * (i + 1))
            co.add("SURVEYQUESTIONRESULTS", {
                "SURVEYQUESTIONRESULTKEY": co.key(), "SURVEYSTEPRESULTKEY": co.key(), "SURVEYRESULTKEY": result_key,
                "PARTICIPANTIDENTIFIER": p.pid, "RESULTIDENTIFIER": item, "ANSWERS": str(value),
                "STARTDATE": t, "ENDDATE": t + timedelta(seconds=15),
            })
        wide = {"PARTICIPANTIDENTIFIER": p.pid, "STUDY_PARTICIPANT_ID": p.study_id,
                f"STARTDATE{wave}": start.replace(tzinfo=None), f"ENDDATE{wave}": end.replace(tzinfo=None)}
        wide |= {f"{k}{wave}": v for k, v in answers.items() if not k.startswith("substance")}
        if wave == 0:  # the baseline view names these differently from the quarterly views
            wide |= {"ENROLLMENTDATE": midnight(p.enroll), "CONSENTED": "Y", "DEVICEPLATFORM0": platform,
                     "hours0": r.randint(40, 65), "sleep24h0": round(r.uniform(5, 9), 1), "Sex": p.sex,
                     "tobacco0": answers["substance_tobacco"], "alcohol0": answers["substance_alcohol"],
                     "cannabis0": 0 if r.random() < 0.85 else r.randint(1, 3), "Black tea": r.randint(0, 1)}
        else:
            wide |= {f"substance_tobacco{wave}": answers["substance_tobacco"],
                     f"substance_alcohol{wave}": answers["substance_alcohol"]}
        co.add(WAVE_VIEWS[wave], wide)
        done[wave] = end.date()
    return done


def write_dictionary(co: Cohort, survey_keys: dict) -> None:
    table = "SURVEYDICTIONARY" if "SURVEYDICTIONARY" in co.tables else "STG_SURVEYDICTIONARY"
    text_types = table == "STG_SURVEYDICTIONARY"  # QUIRK: key and version are VARCHAR2 in the STG table
    for (survey, version), key in survey_keys.items():
        for step, spec in co.items.items():
            for item, question in spec["items"].items():
                co.add(table, {
                    "SURVEYNAME": survey, "SURVEYKEY": key.hex().upper() if text_types else key,
                    "SURVEYVERSION": str(version) if text_types else version, "STEPIDENTIFIER": step,
                    "RESULTIDENTIFIER": item, "QUESTIONTEXT": question, "ANSWERFORMAT": "TextChoice",
                    "ANSWERCHOICES": spec["choices"],
                })


def write_participant(co: Cohort, p: Person, waves_done: dict[int, date]) -> None:
    r, end = co.rng, co.c["data_end"]
    steps = [d.d for d in p.days if d.worn]
    sleeps = [d.d for d in p.days if d.night]
    # Mood is only queryable where the cohort has VW_DAILY_MOOD; keep the summary consistent.
    moods = [d.d for d in p.days if d.mood is not None] if "VW_DAILY_MOOD" in co.tables else []
    count = lambda ds, n: sum(1 for x in ds if x >= end - timedelta(days=n))
    home = p.zones[0][1]
    enrolled = at(p.enroll, r.uniform(8, 20), home)
    co.add("STUDYPARTICIPANTS", {
        "PARTICIPANTIDENTIFIER": p.pid, "PARTICIPANTID": p.internal_id, "SECONDARYIDENTIFIER": p.study_id,
        "EMAILADDRESS": f"{p.pid.lower()}@example.invalid", "FIRSTNAME": "Synthetic", "LASTNAME": f"Participant {p.pid}",
        "GENDER": "F" if p.sex == 2 else "M", "DATEOFBIRTH": datetime(r.randint(1990, 2000), r.randint(1, 12), r.randint(1, 28)),
        "ENROLLMENTDATE": enrolled, "UTCOFFSET": tstz(enrolled)[-6:], "TIMEZONE": home.key,
        "PREFERREDLANGUAGE": "en", "UNSUBSCRIBEDFROMEMAILS": "false", "UNSUBSCRIBEDFROMSMS": "false",
        "INSERTEDDATE": co.inserted(enrolled), "WITHDRAWDATE": p.withdraw.isoformat() if p.withdraw else None,
    })
    summary = {
        "PARTICIPANTIDENTIFIER": p.pid, "STUDY_PARTICIPANT_ID": p.study_id, "FITBIT": p.fitbit,
        "APPLE_WATCH": p.watch, "PHONE": p.phone,
        "STEP_LATEST": midnight(max(steps)) if steps else None, "SLEEP_LATEST": midnight(max(sleeps)) if sleeps else None,
        "MOOD_LATEST": midnight(max(moods)) if moods else None,
        "STEP_COUNT_30": count(steps, 30), "STEP_COUNT_90": count(steps, 90),
        "SLEEP_COUNT_30": count(sleeps, 30), "SLEEP_COUNT_90": count(sleeps, 90),
        "MOOD_COUNT_30": count(moods, 30), "MOOD_COUNT_90": count(moods, 90),
    }
    summary |= {f"Q{w}_SURVEY_COMPLETION": midnight(d) for w, d in waves_done.items() if w > 0}
    co.add("VW_IHS_PARTICIPANT_SUMMARY", summary)


def generate_cohort(seed: int, name: str, c: dict, config: dict, tables: set[str]) -> dict[str, list[dict]]:
    rng = random.Random(f"{seed}:{name}")  # string seeds are hashed deterministically
    co = Cohort(rng, name, c, tables, config["survey_items"])
    waves = config["survey_waves"]
    survey_keys = {(s, v): rng.randbytes(16) for _, s, _ in waves for v in (1, 2)}
    write_dictionary(co, survey_keys)
    for p in make_people(rng, name, c, config, has_oura="OURADAILYACTIVITY" in tables):
        simulate_days(rng, p, c["internship_start"])
        vendor = {"Fitbit": write_fitbit, "Garmin": write_garmin, "Oura": write_oura}.get(p.device)
        if vendor:
            vendor(co, p)
        write_healthkit(co, p)  # Apple Watch users, plus iPhone-sourced rows for others
        write_mood(co, p)
        write_participant(co, p, write_surveys(co, p, waves, survey_keys))
    return co.rows


# ------------------------------------------------------------- Oracle ----

def qi(name: str) -> str:
    return f'"{name}"'  # always quote: some real columns are mixed case or contain spaces


def is_tstz(typ: str) -> bool:
    return typ.startswith("TIMESTAMP") and "TIME ZONE" in typ


def bind_type(typ: str):
    if is_tstz(typ):
        return oracledb.DB_TYPE_VARCHAR  # bound as text, converted by TO_TIMESTAMP_TZ
    for prefix, t in [("NUMBER", oracledb.DB_TYPE_NUMBER), ("DATE", oracledb.DB_TYPE_DATE),
                      ("TIMESTAMP", oracledb.DB_TYPE_TIMESTAMP), ("RAW", oracledb.DB_TYPE_RAW)]:
        if typ.startswith(prefix):
            return t
    return oracledb.DB_TYPE_VARCHAR  # VARCHAR2, CHAR, CLOB (our CLOB values are short)


def to_bind(value, typ: str):
    if value is None or not isinstance(value, datetime):
        return value
    return tstz(value) if is_tstz(typ) else value.replace(tzinfo=None)


def run(cur, sql: str, ignore: tuple[int, ...] = ()) -> None:
    try:
        cur.execute(sql)
    except oracledb.DatabaseError as e:
        if e.args[0].code not in ignore:
            raise


def reset_accounts(cur, cohorts: list[str]) -> None:
    users = cohorts + [RO_USER, MARKER_SCHEMA]
    cur.execute("SELECT sid, serial# FROM v$session WHERE username IN ({})".format(",".join(f"'{u}'" for u in users)))
    for sid, serial in cur.fetchall():  # a lingering dev session would block DROP USER
        run(cur, f"ALTER SYSTEM KILL SESSION '{sid},{serial}' IMMEDIATE", ignore=(30, 31))
    for u in users:
        run(cur, f"DROP USER {u} CASCADE", ignore=(1918,))
    for role in ROLES:
        run(cur, f"DROP ROLE {role}", ignore=(1919,))
    for u in cohorts:  # schema-only owners, like the real cohort schemas nobody logs into
        cur.execute(f"CREATE USER {u} NO AUTHENTICATION")
        cur.execute(f"GRANT UNLIMITED TABLESPACE TO {u}")  # the -lite image has no USERS tablespace
    cur.execute(f'CREATE USER {RO_USER} IDENTIFIED BY "{RO_PWD}"')
    cur.execute(f"GRANT CREATE SESSION TO {RO_USER}")
    for role in ROLES:
        cur.execute(f"CREATE ROLE {role}")
    # Mirrors the real shared account: the 2026 write role also carries CREATE privileges.
    cur.execute("GRANT CREATE TABLE, CREATE VIEW, CREATE PROCEDURE TO IHS_2026_ROLE")


def grant(cur, cohort: str, name: str, is_table: bool) -> None:
    target = {"IHS_2024": RO_USER, "IHS_2025": "IHS_2025_RO", "IHS_2026": "IHS_2026_RO"}[cohort]
    cur.execute(f"GRANT SELECT ON {cohort}.{qi(name)} TO {target}")
    if cohort == "IHS_2026" and is_table:
        cur.execute(f"GRANT SELECT, UPDATE, DELETE, ALTER ON {cohort}.{qi(name)} TO IHS_2026_ROLE")


def create_object(cur, cohort: str, name: str, obj: dict, objects: dict) -> list[tuple[str, str]]:
    cols = cohort_columns(obj, cohort)
    if obj["create_as"] == "table":
        cur.execute(f"CREATE TABLE {cohort}.{qi(name)} ({', '.join(f'{qi(n)} {t}' for n, t in cols)})")
    else:
        base_types = dict(cohort_columns(objects[obj["base"]], cohort))
        # V* views expose timezone-aware timestamps as text.
        select = [f"CAST(TO_CHAR({qi(n)}, '{TSTZ_FMT}') AS VARCHAR2(26)) AS {qi(n)}"
                  if t == "VARCHAR2(26)" and is_tstz(base_types[n]) else qi(n) for n, t in cols]
        cur.execute(f"CREATE VIEW {cohort}.{qi(name)} AS SELECT {', '.join(select)} FROM {cohort}.{qi(obj['base'])}")
    return cols


def load(cur, cohort: str, name: str, cols: list[tuple[str, str]], rows: list[dict], batch: int = 50_000) -> None:
    binds = [f"TO_TIMESTAMP_TZ(:{i}, '{TSTZ_FMT}')" if is_tstz(t) else f":{i}"
             for i, (_, t) in enumerate(cols, 1)]
    sql = f"INSERT INTO {cohort}.{qi(name)} ({', '.join(qi(n) for n, _ in cols)}) VALUES ({', '.join(binds)})"
    for i in range(0, len(rows), batch):
        cur.setinputsizes(*[bind_type(t) for _, t in cols])
        chunk = [[to_bind(row.get(n), t) for n, t in cols] for row in rows[i:i + batch]]
        cur.executemany(sql, chunk)


def main() -> None:
    objects, config = load_spec()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=int, default=config["seed"])
    args = parser.parse_args()
    cohorts = list(config["cohorts"])
    all_columns = {n for o in objects.values() for n, _ in o["columns"]} | {
        n for o in objects.values() for d in (o.get("drift") or {}).values() for n, _ in d.get("set", [])}

    require_local_dsn(DSN)
    t0 = time.monotonic()
    with oracledb.connect(user="SYSTEM", password=ADMIN_PWD, dsn=DSN) as conn:
        cur = conn.cursor()
        require_synthetic_server(cur)
        reset_accounts(cur, cohorts)
        create_marker(cur, RO_USER)
        for cohort in cohorts:
            present = {n: o for n, o in objects.items() if cohort in o["cohorts"]}
            tables = {n for n, o in present.items() if o["create_as"] == "table"}
            rows = generate_cohort(args.seed, cohort, config["cohorts"][cohort], config, tables)
            for name, obj in present.items():  # spec order puts each base table before its view
                cols = create_object(cur, cohort, name, obj, objects)
                if name in tables:
                    unknown = set().union(*map(dict.keys, rows[name])) - all_columns if rows[name] else set()
                    assert not unknown, f"{name}: generator wrote unknown columns {unknown}"
                    load(cur, cohort, name, cols, rows[name])
                grant(cur, cohort, name, name in tables)
            conn.commit()
            total = sum(len(v) for v in rows.values())
            print(f"{cohort}: {len(present)} objects, {total:,} rows ({time.monotonic() - t0:.0f}s)")
        cur.execute(f"GRANT {', '.join(ROLES)} TO {RO_USER}")
        cur.execute(f"ALTER USER {RO_USER} DEFAULT ROLE ALL")
    print(f"done in {time.monotonic() - t0:.0f}s. Connect: {RO_USER}/{RO_PWD}@{DSN}")


if __name__ == "__main__":
    main()
