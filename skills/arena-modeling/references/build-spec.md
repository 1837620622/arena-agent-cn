# Build spec

`build_model(spec)` (MCP) or `arena-agent build spec.json` (CLI). One spec creates a new model or extends an existing
one, sets every module, declares the names they use, connects them, compiles and saves.

```json
{
  "base": "C:/work/clinic_v1.doe",       // optional: extend this model. It is saved as save_as FIRST; base is never changed
  "save_as": "C:/work/clinic_v2.doe",    // required for a new model; where to save
  "run_setup": {"replications": 10, "replication_length": 480, "replication_length_units": "minutes",
                "warm_up": 0, "warm_up_units": "minutes", "base_time_units": "minutes"},
  "updates": [{"name": "Doctor", "data": {"Capacity": "4"}}],          // change existing modules, by Name
  "modules": [
    {"id": "arrive", "panel": "BasicProcess", "type": "Create", "x": 0, "y": 0,
     "data": {"Name": "Patients Arrive", "Entity Type": "Patient", "Interarrival Type": "Random",
              "Value": "5", "Units": "Minutes"}},
    {"id": "init", "type": "Assign", "x": 2000, "y": 0, "data": {"Name": "Init"},
     "rows": [{"Type": "Attribute", "AName": "ArriveAt", "Value": "TNOW"},          // repeat-group rows, in order
              {"Type": "Variable",  "VName": "N",        "Value": "N + 1"}]},
    {"id": "route", "type": "Decide", "x": 4000, "y": 0,
     "data": {"Name": "Route", "Type": "N-way by Condition"},
     "cases": [{"N If": "Expression", "N Value": "Acuity == 1"},
               {"N If": "Expression", "N Value": "Acuity == 2"}]},
    {"id": "exam", "type": "Process", "x": 6000, "y": 0,
     "data": {"Name": "Exam", "Action": "Seize Delay Release", "DelayType": "Expression",
              "Expression": "EXPO(12)", "Units": "Minutes"},
     "resources": [{"Resource Type": "Resource", "Resource Name": "Doctor", "Quantity": "1"}]},
    {"id": "docs", "type": "Resource", "data": {"Name": "Doctor", "Capacity": "3"}}
  ],
  "connections": [
    {"from": "arrive", "to": "init"},                       // default exit
    {"from": "route", "to": "exam", "exit": "case:1"},
    {"from": "route", "to": "other", "exit": "else"}
  ],
  "declare": {"attributes": ["Acuity"], "variables": []},  // extra names only READ in expressions
  "declare_at": [0, -4000],                                // where the auto-declared data modules start (a 6-wide grid)
  "delete": ["File 1"],                                    // stray modules to remove (see auto_created)
  "layout": {"dx": 2600, "dy": 1800, "columns": 12},       // spacing of modules built without x/y
  "shapes": [{"text": "Triage", "x": 0, "y": -1500, "level": "title"}],   // titles on the flowchart
  "views": [{"name": "MAIN", "modules": ["arrive", "exam"], "margin": 600},
            {"name": "DETAIL", "region": [-500, -500, 7000, 2500]}],      // saved named views to export
  "check": true                                            // compile at the end (default)
}
```

(The `//` comments are for reading only; real JSON has none.)

## Fields

- `panel` defaults to `BasicProcess`. Hold, Search, Remove, Delay, Seize, Release, ReadWrite, Expression, File,
  Statistic, Signal ... are on `AdvancedProcess`. Panel names are internal names: `list_panels`.
- `type` is the module type name (`list_module_types`), `data` maps operand names (`module_operands`,
  `template_reference`) to string values.
- `rows`, `cases`, `resources` write repeat groups: row i becomes `Operand(i)`. Always give rows 1..n in order (rows
  in between would get defaults). You can also address one row directly in `data`/`updates`: `"Value(3)": "7"`.
- `id` is how connections refer to the module; connections can also name existing modules by their Name.
- `x`, `y`: model units. Keep every module inside -32,000..32,000 (Arena's view coordinates are 16-bit; modules
  outside can never be seen in the GUI). A spacing of ~2,000-2,500 between modules in a row reads well.
- A module with no `x`/`y` is placed from the connection graph: one column right of the module that feeds it,
  branches stacked and centred around the main row, long chains wrapped into bands. `layout` tunes it (`dx`, `dy`,
  `columns` per band, `origin`); the default 2600 x 1800 leaves room for labels and route lines. Extending a model
  (`base`) puts the new band below the existing flowchart, or beside it when below would leave Arena's +-30,000 view.
  A layout that would run past that view (a big model, or a column with many branches) is moved and compressed to fit,
  and the build result says so in `warnings`; split such a model into named views instead of shrinking the spacing
  further. Coordinates from the spec are rounded to whole model units, which is what Arena's COM methods take.
- Data modules (Resource, Variable, Queue, Entity, Attribute, Expression, Statistic, Schedule ...) need no x/y.
  A data module listed in `modules` without x/y, and every name declared automatically, is placed in a 6-wide grid
  from `declare_at`: beside the flow, never stacked on one point and never inside the process drawing.

## Titles and named views

- `shapes` writes flowchart text. `level` picks one type hierarchy for the whole drawing: `title` (700 model units),
  `section` (480, the default) and `note` (320); `height` overrides it, and `font`, `angle`, `italic`, `underline`,
  `bold` are passed to Arena. Keep one font for the drawing (Arial or Segoe UI) and one level per kind of label.
- `views` saves named views, which is what `list_named_views` reports and `screenshot_model` exports by name: give
  either `region` as `[x0, y0, x1, y1]` or `modules` (ids or Names) plus `margin` (default 600) and the bounds are
  computed from the real module shapes. Plan one view per business section, plus the animation and return path.
  The build result lists what was created under `views`; a missing module or a bad region is reported in `errors`.

## Exits

`next` (default), `true` / `false` (2-way Decide), `case:N` and `else` (N-way Decide), `original` / `duplicate`
(Separate), `found` / `notfound` (Search), `removed` (Remove; `original` is the remover).

## Names declared automatically

Arena's GUI creates data modules when you type a new name in a dialog; COM does not, and Check then fails with
"Undefined identifier". build_model / set_module declare:

- attributes and variables assigned in Assign rows and ReadWrite rows,
- queues named in Hold `QName` and Search/Remove `Queue Name`,
- resources named in Process rows (`Resource Name(i)` with `Resource Type(i)` = Resource),
- entity types named in Create `Entity Type` (Arena's own auto-created `Entity 1` is renamed to it).

Names that are only *read* (an attribute used in a Decide condition before any Assign sets it, a variable used in an
expression) must go in `declare`, or be created as explicit data modules (e.g. `{"type": "Variable", "data":
{"Name": "Rate", "Initial Value": "5"}}`).

## Output

`ok`, `errors` (operands that could not be set, invalid choices), `warnings` (values that read back differently),
`declared`, `auto_created` (data modules Arena added on its own), `deleted`, `check_errors` (each with `message`,
`level`, and `module`/`type` when Arena names the faulty module; SIMAN compile errors carry the generated `code`),
`dropped_connections` (connections Arena's compiler deleted).
