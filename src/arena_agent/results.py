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
from collections import Counter
import csv
from itertools import chain
import math
import os
from pathlib import Path
import sqlite3
import re
import statistics
import tempfile

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
            known = [when for when, _, _ in reports if when is not None]
            latest = max(known) if known else None

        by_metric = {}
        for when, k, rows in reports:
            if when != latest:
                continue
            for _, rep, name, typ, src, val in rows:
                key = name if k in ("output", "counter") else f"{name}.{typ}"
                by_metric.setdefault((key, k, typ, src), {})[rep] = val
        occurrences = Counter(key for key, _, _, _ in by_metric)
        res = {}
        for (key, kind, typ, source), reps in by_metric.items():
            label = key
            if occurrences[key] > 1:
                base = f"{key} [{kind}, {source}, {typ}]"
                label, index = base, 2
                while label in occurrences or label in res:
                    label = f"{base} #{index}"
                    index += 1
            ids = sorted(reps)
            values = [reps[i] for i in ids]
            rec = {"kind": kind, "source": source, **summarize(values)}
            if per_replication:
                rec["replications"], rec["replication_ids"] = values, ids
            res[label] = rec
        return res
    finally:
        con.close()


def drop_empty(res):
    """Leave out statistics with no observations in any replication (unused entity types and the like). Records from
    the text report carry a `section` instead of a `kind`, so nothing here may index a key blindly."""
    return {k: v for k, v in res.items()
            if v.get("n") != 0 and not (v.get("kind") == "tally" and v.get("average") is None)}


def read(model_path, since=0, per_replication=False, include_empty=False, *, text_only=False):
    model_path = os.fspath(model_path)
    base, extension = os.path.splitext(model_path)
    explicit_report = extension.lower() in (".db", ".out") and not text_only
    suffixes = (extension.lower(),) if explicit_report else (".out",) if text_only else (".db", ".out")
    candidates = []
    for suffix in suffixes:
        path = model_path if explicit_report else base + suffix
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
    try:
        with open(out, errors="replace") as f:
            for _ in range(5):
                line = f.readline()
                if "License:" in line:
                    return line.split("License:", 1)[1].strip() or None
    except OSError:
        # 报表锁定时仍要交付本轮仿真状态，许可提示只是辅助信息。
        return None
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
            # 表头行 "Identifier  Average  Half-width ..." 也是全字母，不能当成区段名。
            if (head.isupper() and not head.lower().startswith("identifier")
                    and head.replace(" ", "").replace("-", "").isalpha()):
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
    body, has_summary, _ = text.partition("Output Summary for")
    headers = list(re.finditer(r"^[ \t]*Summary for Replication[ \t]+(\d+)[ \t]+of[ \t]+\d+[ \t]*\r?$",
                               body, flags=re.M))
    if len(headers) <= 1 and not per_replication:
        return result
    if headers:
        ids = [int(header.group(1)) for header in headers]
        ends = [header.start() for header in headers[1:]] + [len(body)]
        replications = [parse_out(body[h.end():end]) for h, end in zip(headers, ends)]
    elif has_summary:
        return result
    else:
        ids, replications = [1], [result]

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
            result[name]["replication_ids"] = ids
    return result


def filter_statistics(stats, measures):
    if not measures:
        return stats
    needles = tuple(q.casefold() for q in measures)
    return {k: v for k, v in stats.items() if any(q in k.casefold() for q in needles)}


def csv_destination(path):
    out = Path(path).absolute()
    if out.suffix.lower() != ".csv" or out.is_dir():
        raise ValueError("give a CSV file path ending in .csv")
    return out


SUMMARY_FIELDS = ("average", "half_width", "min", "max", "n", "value", "count", "observations", "limit")
CONTEXT_FIELDS = ("scenario_index", "scenario", "statistic", "kind", "statistic_source", "report")


def _csv_rows(reports, per_replication):
    for index, report in enumerate(reports, 1):
        for name, stat in report["statistics"].items():
            context = {
                "scenario_index": index if "name" in report else None,
                "scenario": report.get("name"),
                "statistic": name,
                "kind": stat.get("kind", stat.get("section")),
                "statistic_source": stat.get("source"),
                "report": report.get("results_source", report.get("source")),
            }
            if per_replication:
                ids = stat.get("replication_ids", [])
                for rep, value in zip(ids, stat.get("replications", [])):
                    yield {**context, "replication": rep, "value": value}
            else:
                row = {key: stat.get(key) for key in SUMMARY_FIELDS}
                row["n"] = stat.get("n", stat.get("reps"))
                row["min"] = stat.get("min", stat.get("minimum"))
                row["max"] = stat.get("max", stat.get("maximum"))
                yield {**context, **row}


def _csv_cell(value):
    # 统计名称和场景名称可能来自输入。按文字写出，防止电子表格把它们当作公式。
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@"))
                                  or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def write_csv(path, reports, per_replication=False):
    """Write a summary or a long replication table, preserving an existing CSV until the replacement is complete."""
    out = csv_destination(path)
    rows = _csv_rows(reports, per_replication)
    first = next(rows, None)
    if first is None:
        what = "replication values" if per_replication else "statistics"
        raise ValueError(f"no matching {what} to export")
    header = CONTEXT_FIELDS + (("replication", "value") if per_replication else SUMMARY_FIELDS)
    out.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=out.parent, suffix=".tmp", delete=False,
                                         newline="", encoding="utf-8-sig") as stream:
            staged = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=header)
            writer.writeheader()
            for count, row in enumerate(chain((first,), rows), 1):
                writer.writerow({k: _csv_cell(v) for k, v in row.items()})
        os.replace(staged, out)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return {"path": str(out), "rows": count, "mode": "per_replication" if per_replication else "summary"}


def last_results(model, per_replication=False, include_empty=False, *, measures=None, csv_path=None):
    path = os.path.abspath(model)
    result = {"model": path, **read(path, per_replication=per_replication, include_empty=include_empty)}
    result["ok"] = result["source"] is not None
    result["statistics"] = filter_statistics(result["statistics"], measures)
    if csv_path is not None and result["ok"]:
        try:
            result["csv"] = write_csv(csv_path, [result], per_replication)
        except (OSError, ValueError) as e:
            result.update(ok=False, error=str(e))
    return result
