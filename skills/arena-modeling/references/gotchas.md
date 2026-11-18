# Gotchas: Arena through COM

The original findings were verified on Arena 16.20. The native Arena 14 checks below were verified on Arena 14.0
(COM 1400).
Background and evidence for each: `arena_guide("discoveries")`.

## Building

1. **Unknown choice labels are stored without error.** Choice operands take the dialog label ("N-way by Condition") and
   Arena stores an internal value (NIf). A typo is stored verbatim and only fails at check. build/set read every value
   back: act on `errors` ("not a valid choice") and on `warnings` that show an unexpected readback.
2. **Names are not declared through COM.** Typing a new name in an Arena dialog creates its data module; COM does not,
   and check fails with `Undefined identifier: X`. arena-agent declares names *assigned* in Assign/ReadWrite, queues of
   Hold/Search/Remove, Process resources and Create entity types. Names only *read* in expressions go in the spec's
   `declare` or as explicit data modules.
3. **SIMAN reserved words** (`Wait`, `Group`, `Queue`, `Station`, `Delay`, `Count`, `Batch`, ... every block and
   element name) as attribute/variable names compile with only a warning, then the run stops at time 0. The tools
   refuse them; rename (`RWait`, `ShiftGrp`).
4. **Repeat rows are `Operand(i)`**, and writing row i creates missing rows in between with defaults: write rows 1..n
   in order (the spec's `rows` / `cases` / `resources` do).
5. **Exit and entry points are hidden operands.** Hold's entry is `QLabel`; a connection to its `HoldLabel` is accepted
   and then silently deleted by check (reported as `dropped_connections`). Use the spec's `exit` names.
6. **Arena adds data modules by itself**: Create -> Entity (renamed to the spec's entity type), ReadWrite -> `File 1`
   (fails check until edited; delete it with the spec's `delete`), Hold/Search -> a queue, Batch -> `<name>.Queue`.
   Names the tools declare themselves are placed in a 6-wide grid from `declare_at` (Attribute, Variable, Queue,
   Resource, Entity share one cursor), because several data modules created at one point pile up and read as one
   unreadable block in the flowchart and in every export. A data module listed in the spec without x/y joins the
   same grid instead of taking a place in the flow.
7. **Changing a Hold's queue settings can drop its incoming connections.** Re-check after editing a Hold.
8. **View coordinates are 16-bit.** Modules can be placed anywhere through COM, but the GUI only shows -32,768..32,767.
   Keep layouts inside +-32,000; compress spacing for big models.
9. **Functions taking an attribute number** (`AQUE`, `SAQUE`, `AG`, `SAG`, `ATTR`, `ED`, `EXPR`) need `NSYM(name)`.
   A bare name compiles but is evaluated as the attribute's *value*; the run stops with "Attribute number 0 is not in
   valid range".
10. **2-D Expression tables are stored column by column**: cell (r, c) is `Value((c-1)*Dim1 + r)`. A grid may hold
    fewer values than Dim1 x Dim2 and still compile: count them.
11. **Never write a non-number into a numeric operand** (e.g. a mis-parsed argument into `Initial Value`): Arena stores
    it and the next check fails with "Invalid Real Number".

## Running

12. **One model runs at a time.** A model left in run mode blocks `Go` on every other model for ~2 minutes and then
    returns without running. `run_model` closes completed runs left in run mode (`ended_first`). If a model is
    playing or paused, the tool returns its name and state without ending it. Arena 14 polls batch completion
    with a 300-second default limit, configurable through `timeout` / `--timeout`. A timed-out call requests
    `End()` and returns no statistics for that batch.
13. **Simultaneous events are ordered by Arena's tie rules.** Two processes or entities scheduled at the same instant
    can swap order when an unrelated module is added, changing results. Give clocks distinct offsets (first creation
    0.505 instead of 0.5) and simultaneous entities an explicit order (`+ 0.000001 * ID`). Adding a module that "does
    nothing" must leave results identical: compare per-replication results between versions.
14. **`show` expressions are evaluated at the end of the last replication only.** For a property over all
    replications, keep it in a variable with Clear Option `None`, or use output statistics / Record.
15. **A statistic's name evaluates to its number**, not its value, in `show`: use `OVALUE(Name)` for outputs,
    `TAVG(Name)` for tallies, `DAVG(Name)` for time-persistent statistics.
16. **Scenario runs need no save.** Set values in memory and run again (`run_scenarios`); saving a large model after
    every change costs about a minute each.

## Files and Arena itself

17. **Close a model before deleting or replacing its files**; Arena keeps them locked while the model is open.
18. **`Model.Close()` can crash Arena** ("The remote procedure call failed"). Saved files are intact; the next call
    starts a fresh Arena. arena-agent reconnects on its own.
19. **Two edits must never run into the same model at once.** The MCP server serialises calls; do not also run CLI
    builds on the same file from a background shell at the same time.
20. **Screenshots capture the Arena drawing pane.** Open Arena in a Windows desktop session. The exporter captures
    the native drawing window so taskbar, cursor overlays and other desktop panels do not enter the tiled image.
    The default region is the flowchart modules plus the text shapes within 5,000 units of them, so section titles are
    part of the image and a stray shape far away is ignored; a model that only declares names is exported from its
    data modules. Without `pixels_per_unit` the scale is never below Arena's current zoom: a region that fits one pane
    at that scale is one image, a bigger one is tiled up to `max_tiles` (64), and only a region too large even for
    that many tiles is scaled down; `pixels_per_unit` forces an exact scale. For readable details, set `region` to the
    section bounds and raise `max_tiles` rather than lowering the scale — and zoom Arena in before exporting, since
    the export follows the zoom. Leave a margin around the outer modules and their connectors. Inspect tiled images at
    100% for broken lines at the joins and for labels that Arena wraps against a module border. Shorten a crowded
    label if its full text will not fit, then check any references and rerun the model.
21. Arena's report database is `<model>.db` (SQLite in Arena 16+), the text report `<model>.out`, errors `<model>.err`
    (binary; arena-agent decodes it and maps each record to its module). A model with no saved path has no report
    beside it, so the run reports "no report was read" instead of looking for `.err` in the working directory.
22. **Close every file you read or write.** Arena holds `.doe`, `.out` and `.err` open, and an unclosed handle in a
    helper keeps a lock on the user's config or report. Config files are written to a temporary file and replaced, so
    a reader never sees half a JSON file.

## Arena 14

- The agent checks the installed executable's major version before a cold launch and the active COM version for
  each build, check, and run. COM versions in the 1400–1499 range use the Arena 14.x report settings. Record rows
  follow the actual template: a flat Record takes one row, and a definition with a repeat group accepts indexed rows.
  The active version and compatibility mode are shown by `arena_status` and `arena-agent doctor`.
- A fresh COM launch can fail during Check with `FOPEN`, `SIM.out` and `Permission denied`. The agent starts Arena 14
  in a writable working directory. If an existing instance was started in a protected directory, close it normally
  after saving your work, then let the agent start Arena again.
- `Not a valid password.` and DAO/Jet initialization dialogs can occur during Check or Run on an ordinary, unprotected
  model. This path uses the legacy Access report database. For Arena 14 checks and batch runs, the agent temporarily
  sets `DisableReportDatabase` and reads the native `.out`. It restores the model setting afterward. A password prompt
  while opening a protected model or activating Arena still requires the proper model credentials or license.
- `.out` uses the native text identifiers, e.g. `Queue1.WaitingTime`. Arena 16+ SQLite reports can use spacing such as
  `Queue1.Waiting Time`. Text results use the printed precision of the report for per-replication analysis.
- Arena 14 can reopen a saved model at a different zoom. Save named views for each section and select the intended view
  before capturing. At full scale, check the first and last modules, branch captions, and a running state with populated
  queue counts. Keep status numbers away from route lines and verify a multi-digit value before exporting.
- The first screenshot tile after opening some Arena 14 named views can jump to twice the requested world coordinates.
  `screenshot_model` checks that the displayed area covers the requested tile and retries before capturing. If the
  view still misses the tile, it reports the affected region.
- Animation frames: `capture_animation_frame` plays and pauses at one simulation time (`BreakOnTime`);
  `capture_animation_frames` repeats it for a list of increasing times. Times must be greater than zero — Arena
  cannot break at time 0, so the first sample is a small positive time. Both end a stale run first, because a model
  left in run mode blocks playback (12) and every frame of a series replays the run. Arena 14 cannot resume a paused
  animation through COM, so every frame replays from zero — sample 5 to 8 key moments (start, first queue, steady
  state, a status switch, the end) rather than a dense series. When a run cannot reach the time, lower `speed` so the
  tool has time to set the breakpoint, and raise `timeout` for long runs. A visible jump between two frames is either
  a model event (a resource turning busy, a batching release) or too coarse a time step: compare the printed clock in
  both frames before blaming playback.
- The flowchart pane is normally `Afx:…:8:…`; where a build numbers it differently the exporter takes the largest
  visible `Afx:` child that is not the spreadsheet (`AfxFrameOrView…`).
- A view can report mirrored bounds (`Left > Right`, `Top > Bottom`). Every export path compares and pastes in model
  coordinates, so the bounds are ordered first and a captured tile from a mirrored view is turned back before it is
  pasted; without that, a tiled image shows mirrored modules in the wrong place.
- `arena-agent` exits 0 on success, 1 when the result reports `error` or `ok: false` (a failed check, build or run),
  and 2 for a bad argument, so `arena-agent build … && arena-agent validate …` stops at the first failure.
