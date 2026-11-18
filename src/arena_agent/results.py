"""Run results: every statistic of the last run, per replication and across replications.

Primary source: the report database Arena writes next to the model (<model>.db, SQLite, Arena 16 and later). Its
views DiscreteTimeStatsQuery (tallies: waiting times, times in system), ContinuousTimeStatsQuery (time-persistent:
queue lengths, WIP, number busy, utilization), CounterStatsQuery and OutputStatsQuery hold one row per statistic per
replication; warm-up rows are already excluded. Across replications this module reports the mean, the 95% confidence
half-width (t distribution, n - 1 degrees of freedom, as Arena's own report), the minimum and maximum of the
replication values, and n.

Fallback (no .db, e.g. Arena 14): the SIMAN summary report <model>.out. Where there is no native aggregate,
derive it from the printed replication values.
"""
import math
import os
from pathlib import Path
import sqlite3
import re
import statistics

VIEWS = (("DiscreteTimeStatsQuery", "Average", "tally"), ("ContinuousTimeStatsQuery", "Average", "time-persistent"),
         ("CounterStatsQuery", "RecordedValue", "counter"), ("OutputStatsQuery", "RecordedValue", "output"))
# t(0.975, df) for df = 1..30; beyond that the normal value is close enough
T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
        2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t975(df):
    if df <= 0:
        return float("nan")
    if df <= 30:
        return T975[df - 1]
    return 1.960 + 2.4 / df  # within 0.002 of the exact value for df > 30


def summarize(values):
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    n = len(vals)
    if not n:
        return {"average": None, "half_width": None, "min": None, "max": None, "n": 0}
    mean = sum(vals) / n
    hw = t975(n - 1) * statistics.stdev(vals) / math.sqrt(n) if n > 1 else None
    return {"average": mean, "half_width": hw, "min": min(vals), "max": max(vals), "n": n}


def read_db(path, per_replication=False):
    """{"Examination.Queue.Waiting Time": {"kind", "source", "average", "half_width", "min", "max", "n"}, ...}
    from the report database at path. Only the latest run in the database is used."""
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        # 四类统计共用一次读事务和运行时间，避免读到另外一轮的旧数据。
        con.execute("BEGIN")
        try:
            latest = con.execute("SELECT MAX(OutputCreationDate) FROM RunOutput").fetchone()[0]
        except sqlite3.OperationalError as exc:
            if not str(exc).startswith(("no such table:", "no such column:")):
                raise
            latest = None

        reports = []
        for view, col, k in VIEWS:
            query = (f'SELECT [Project RunDateTime], Replication, Name, Type, Source, [{col}] '
                     f'FROM [{view}] WHERE [Project RunDateTime] = ')
            try:
                if latest is None:
                    rows = con.execute(query + f'(SELECT MAX([Project RunDateTime]) FROM [{view}])').fetchall()
                else:
                    rows = con.execute(query + '?', (latest,)).fetchall()
            except sqlite3.OperationalError as exc:
                if not str(exc).startswith(("no such table:", "no such column:")):
                    raise
                continue
            if rows:
                reports.append((rows[0][0], k, rows))

        if not reports:
            return {}
        if latest is None:
            latest = max(when for when, _, _ in reports)

        res = {}
        for when, k, rows in reports:
            if when != latest:
                continue
            by = {}
            for _, rep, name, typ, src, val in rows:
                key = name if k in ("output", "counter") else f"{name}.{typ}"  # outputs/counters have unique names
                by.setdefault((key, src), {})[rep] = val
            for (key, src), reps in by.items():
                rec = {"kind": k, "source": src, **summarize([reps[r] for r in sorted(reps)])}
                if per_replication:
                    rec["replications"] = [reps[r] for r in sorted(reps)]
                res[key] = rec
        return res
    finally:
        con.close()


def drop_empty(res):
    """Leave out statistics with no observations in any replication (unused entity types and the like)."""
    return {k: v for k, v in res.items() if not (v["n"] == 0 or (v["kind"] == "tally" and v["average"] is None))}


def read(model_path, since=0, per_replication=False, include_empty=False, *, text_only=False):
    base = os.path.splitext(model_path)[0]
    candidates = []
    for suffix in ((".out",) if text_only else (".db", ".out")):
        path = base + suffix
        try:
            modified = os.stat(path).st_mtime
        except OSError:
            continue
        if suffix == ".db":
            # SQLite 可能先把最新事务写进 WAL，主数据库要到 checkpoint 才更新。
            try:
                modified = max(modified, os.stat(path + "-wal").st_mtime)
            except OSError:
                pass
        if modified >= since:
            candidates.append((suffix, path, modified))
    if not since:
        # 离线复读优先取最近写出的报告；时间相同时保留 SQLite 的精度。
        candidates.sort(key=lambda item: item[2], reverse=True)
    for suffix, path, _ in candidates:
        if suffix == ".db":
            try:
                res = read_db(path, per_replication)
            except sqlite3.DatabaseError:
                continue
            if res:
                return {"source": path, "statistics": res if include_empty else drop_empty(res)}
        else:
            try:
                with open(path, errors="replace") as report:
                    stats = text_statistics(report.read(), per_replication)
            except OSError:  # Arena may still hold the report: try the next candidate
                continue
            if stats:
                return {"source": path, "statistics": stats,
                        "note": "from the SIMAN text report, with native labels and printed precision"}
    return {"source": None, "statistics": {}, "note": "no readable report with statistics for this run"}


def report_license(model_path):
    """The license line Arena prints in the text report header, e.g. "STUDENT"; None if there is no report."""
    out = os.path.splitext(model_path)[0] + ".out"
    if not os.path.exists(out):
        return None
    with open(out, errors="replace") as f:
        for _ in range(5):
            line = f.readline()
            if "License:" in line:
                return line.split("License:", 1)[1].strip() or None
    return None


NUM = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?|\(Insuf\)|\(Corr(?:el)?\)|--)"


def _num(x):
    try:
        return float(x)
    except ValueError:
        return None if x == "--" else x


def parse_out(txt):
    """The SIMAN summary report (<model>.out). With several replications, the across-replication table after "Output
    Summary for N Replications": {identifier: {average, half_width, min, max, reps}} (every Tally, Dstat, Output and
    Counter). The per-replication sections come before it; parse only after that header, or the last replication's
    values get overwritten. With one replication, the single report's TALLY / DISCRETE-CHANGE / OUTPUTS / COUNTERS
    sections."""
    res = {}
    if "Output Summary for" in txt:
        tail = txt.split("Output Summary for", 1)[1]
        section = None
        for line in tail.splitlines():
            head = line.strip()
            if head.isupper() and head.replace(" ", "").replace("-", "").isalpha():
                section = head.title()
                continue
            mm = re.match(r"^(\S[^\n]*?)\s{2,}" + r"\s+".join([NUM] * 4) + r"\s+(\d+)\s*$", line)
            if mm:
                res[mm.group(1).strip()] = {"section": section, "average": _num(mm.group(2)),
                                            "half_width": _num(mm.group(3)), "min": _num(mm.group(4)),
                                            "max": _num(mm.group(5)), "reps": int(mm.group(6))}
        return res
    section, cols = None, None
    for line in txt.splitlines():
        head = line.strip()
        if head.startswith(("TALLY VARIABLES", "DISCRETE-CHANGE VARIABLES", "OUTPUTS", "COUNTERS")):
            section = head.title()
            continue
        if not section:
            continue
        if re.match(r"^Identifier\s", head):
            cols = [c.lower().replace(" ", "_").replace("#", "n") for c in re.split(r"\s{2,}", head)[1:]]
            continue
        mm = re.match(r"^(\S[^\n]*?)\s{2,}(\S.*)$", line)
        if mm and cols:
            vals = mm.group(2).split()
            # a counter's Limit column prints "Infinite" when it has none
            if len(vals) == len(cols) and all(re.fullmatch(NUM, v) or v == "Infinite" for v in vals):
                res[mm.group(1).strip()] = {"section": section, **{c: _num(v) for c, v in zip(cols, vals)}}
    return res


def text_statistics(text, per_replication=False):
    result = parse_out(text)
    body = text.split("Output Summary for", 1)[0]
    blocks = re.split(r"^\s*Summary for Replication\s+\d+\s+of\s+\d+\s*$", body, flags=re.M)[1:]
    if len(blocks) <= 1 and not per_replication:
        return result

    replications = [parse_out(block) for block in blocks] if blocks else [result]
    samples = {}
    for index, replication in enumerate(replications):
        for name, record in replication.items():
            value = record.get("average", record.get("value", record.get("count")))
            section, values = samples.setdefault(name, (record["section"], [None] * len(replications)))
            if isinstance(value, (int, float)):
                values[index] = value
    for name, (section, values) in samples.items():
        if name not in result:
            stats = summarize(values)
            result[name] = {"section": section, **stats, "reps": stats["n"]}
        if per_replication:
            result[name]["replications"] = values
    return result
