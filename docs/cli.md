# CLI reference

```
aero [-l LEVEL] <command> [options]
```

`-l/--log` sets the log level (`DEBUG`, `INFO`, …) and goes **before** the command.

| Command | |
|---|---|
| [`configure`](#aero-configure) | Point the client at a server and apply a config profile |
| [`list`](#aero-list) | List sources, flows, provenance, or one source's versions |
| [`create`](#aero-create) | Register a data source |
| [`register`](#aero-register) | Register an analysis flow |
| [`types`](#aero-types) | List notification types and their data ids |
| [`delete-flows`](#aero-delete-flows) | Delete the flows attached to a source |
| [`search`](#aero-search) | Query the Globus Search index |
| [`logout`](#aero-logout) | Clear cached Globus tokens |

---

## `aero configure`

Apply a `config.toml` and print the resolved settings. See [Configuration](configuration.md).

```sh
aero configure -f config.toml
aero configure -f config.toml -p testing
```

| Flag | |
|---|---|
| `-f, --file` | Path to the config file |
| `-p, --profile` | Profile to read. Defaults to `$AERO_PROFILE`, then `default` |

---

## `aero list`

```sh
aero list                    # sources (default)
aero list -t flow            # registered flows
aero list -t prov            # provenance records
aero list -i <data-id>       # every version of one source
```

`-t/--type` and `-i/--id` are mutually exclusive. Output is JSON, paged — press Enter for the next
page, Ctrl-D to stop.

Listing versions of a source is how you check that an ingestion actually produced something:

```sh
aero list -i 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266
```

```json
[
    {
        "id": "4af0e417-0527-43c8-99e7-22cfd88ef951",
        "checksum": "e55c21331b43487d86e269e47edcf3a3",
        "source_key": "test-bucket/test-data/lhs_results.csv",
        "data_id": "6f76e7b5-0f1d-4b4d-9769-e8b85dca5266",
        "version": 1,
        "created_at": "2026-08-07T20:55:42.766024"
    }
]
```

---

## `aero create`

Registers a data source. What it does depends on two choices: whether the source is **copy** or
**no-copy**, and whether it belongs to a **type**.

| Flag | |
|---|---|
| `-f, --file` | YAML file supplying any of the below; explicit flags override it |
| `-n, --name` | Name for the source — **and the parameter name your verifier receives**, see below |
| `-u, --url` | URL of the object, or a glob pattern (quote it) |
| `-c, --collection-url` | Guest collection domain. *Copy sources only* |
| `-C, --collection-uuid` | Guest collection UUID. *Copy sources only* |
| `-g, --endpoint-uuid` | Globus Compute endpoint to pull on. *Copy sources only* |
| `-v, --verifier` | Function uuid to transform the pulled file. *Copy sources only* |
| `-d, --description` | Description |
| `-p, --policy` | `INGESTION_EVENT` (default, pulled on notify) or `INGESTION` (pulled on a timer) |
| `-t, --timer` | Seconds between pulls. Requires `--policy INGESTION` |
| `-k, --kwargs KEY=VALUE` | Extra arguments for the verifier function. *Copy sources only* |
| `--type` | Group this url under a named type |
| `--no-copy` | Track changes without copying any data |

!!! warning "`name` is a Python parameter name when you supply a verifier"

    The pulled file is handed to your verifier as a **keyword argument named after the source**, so
    with `--verifier` the name must match that function's parameter *and* the
    `AeroOutput(name=...)` it returns. Given `def traffic_to_csv(output, ...)` returning
    `AeroOutput(name="output", ...)`, the source must be `name: output` — put the human-readable
    label in `description`.

    A name like `"Midwest Traffic 5min Pull"` fails at run time with
    `traffic_to_csv() got an unexpected keyword argument 'Midwest Traffic 5min Pull'`.

    Without a verifier the name is free-form: the passthrough finds the file whatever it is called.
    So is a `--no-copy` source, which runs no function at all.

!!! note "Accepted but not implemented"

    `-m/--modifier` and `-e/--email` are parsed but ignored by this command. Setting them has no
    effect.

!!! note "`-e` means different things"

    On `create`, the endpoint flag is **`-g`** (`-e` is the unused email). On
    [`register`](#aero-register) it is **`-e`**.

### A copy source

Pulled on notify and staged into a guest collection. Needs a collection and an endpoint:

```sh
aero create \
  --name "midwest traffic" \
  --url https://minio.internal:9000/traffic/LinkTrafficReport.xml.gz \
  --collection-uuid 94d05b66-bf20-435d-a406-2577096b6cb6 \
  --collection-url https://g-18d480.a4b6ed.a567.data.globus.org/ \
  --endpoint-uuid fd0abffb-1e0b-403d-a2ec-2c53c9285df0
```

Omitting `--verifier` stores the file unchanged (AERO registers a passthrough for you).

By default the pull happens when the server receives a notify for the source. To pull on a
schedule instead, ask for the timer-driven policy:

```sh
aero create \
  --name "midwest traffic" \
  --url https://minio.internal:9000/traffic/LinkTrafficReport.xml.gz \
  --collection-uuid 94d05b66-bf20-435d-a406-2577096b6cb6 \
  --collection-url https://g-18d480.a4b6ed.a567.data.globus.org/ \
  --endpoint-uuid fd0abffb-1e0b-403d-a2ec-2c53c9285df0 \
  --policy INGESTION \
  --timer 3600
```

Omitting `--timer` under `INGESTION` is allowed; the **server's default of 86400s (24h)** applies,
and the command says so. A timer without `--policy INGESTION` is rejected rather than ignored,
as is a timer on a `--no-copy` source — that registers no flow at all, so there is nothing to
schedule.

### A no-copy source

Records versions from the change event; nothing is pulled and nothing is stored. Needs neither a
collection nor an endpoint:

```sh
aero create --name "traffic reports" --type traffic --no-copy \
  --url http://minio.internal:9000/traffic/a.xml.gz
```

### Adding more objects to a type

A type binds several objects to **one** data id, so a change to any of them drives the same
analysis. Register the type once, then add urls — only `--type` and `--url` matter, and the same
data id comes back:

```sh
aero create --type traffic --url http://minio.internal:9000/traffic/b.xml.gz
aero create --type traffic --url http://minio.internal:9000/traffic/c.xml.gz
```

### Matching objects with a glob

Instead of listing objects, match them — including ones created later:

```sh
# quote it, or the shell expands the pattern before aero sees it
aero create --type traffic --no-copy \
  --url 'http://minio.internal:9000/traffic/**/*.xml.gz'
```

`*` matches within one path segment, `**` crosses segments, `?` is a single character. So
`traffic/*/x.gz` is exactly one level deep while `traffic/**/x.gz` is any depth.

An exact url always beats a pattern, and among patterns the one with the longest literal prefix
wins — a broad rule plus a narrow exception both work. Each version records the **concrete** object
that changed, so change detection stays per object.

### From a YAML file

```yaml title="source.yaml"
name: traffic reports
type: traffic
url: http://minio.internal:9000/traffic/a.xml.gz
description: MinIO traffic report objects, tracked by reference

# Track changes without copying. Drop this for the usual copy-to-collection
# behavior, which additionally needs collection_uuid, collection_url and
# endpoint_uuid.
no_copy: true
```

A timer-driven source, which does pull and therefore does need a collection and an endpoint:

A timer-driven source with a verifier. Note `name` matches the function's parameter, and the
readable label lives in `description`:

```yaml title="timed-source.yaml"
# def traffic_to_csv(output, bin_freq="1min", db_dsn=None)
#     -> AeroOutput(name="output", ...)
name: output
url: https://travelmidwest.com/lmiga/LinkTrafficReport.xml.gz
collection_uuid: 94d05b66-bf20-435d-a406-2577096b6cb6
collection_url: https://g-18d480.a4b6ed.a567.data.globus.org/
endpoint_uuid: 83934b52-4072-409f-a90c-1e1f2574ebde
description: Midwest Traffic 5min Pull
function_uuid: 443d9008-d1dd-411d-a3d4-2651d172b0b9

policy: INGESTION      # default is INGESTION_EVENT (pulled on notify)
timer: 300             # seconds; omit to take the server default of 86400

kwargs:                # the verifier's other parameters
  bin_freq: 5min
```

```sh
aero create -f source.yaml
aero create -f source.yaml -n "different name"    # flags override the file
aero create -f timed-source.yaml -t 3600          # ...including the timer
aero create -f timed-source.yaml -k bin_freq=1min # ...and kwargs, merged over the file
```

Accepted keys: `name`, `url`, `collection_uuid`, `collection_url`, `endpoint_uuid`, `description`,
`verifier` (or `function_uuid`), `type`, `no_copy`, `policy`, `timer`, `kwargs`.

Values given as `-k KEY=VALUE` arrive as **strings**; use the YAML `kwargs` block when a
parameter needs a number or a boolean.

### Output

Every path prints the **source data id** — the UUID to use as an analysis `input_data` id:

```
Source data id: 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266
  - trigger an ingestion (notify webhook):
      POST <server>/data/6f76e7b5-.../notify
  - reference it as an analysis flow input_data entry:
      {"<name>": {"id": "6f76e7b5-...", "version": null}}
```

---

## `aero register`

Registers an analysis flow: it consumes one or more sources and reruns when they change.

| Flag | |
|---|---|
| `-f, --file` | YAML file describing the flow |
| `-e, --endpoint-uuid` | Globus Compute endpoint to run on |
| `-u, --function-uuid` | Registered analysis function uuid |
| `-p, --policy` | Rerun policy, by name or int. Defaults to `ANY` |
| `-k, --kwargs KEY=VALUE` | Extra keyword arguments; merged over the file's `kwargs` |
| `-d, --description` | Description |

`input_data` and `output_data` are structured, so they can only come from a file.

```yaml title="analysis.yaml"
endpoint_uuid: fd0abffb-1e0b-403d-a2ec-2c53c9285df0
function_uuid: cbaad5b0-01e4-47d7-ba87-2e5633ed0428
policy: ANY                    # rerun when any input gets a new version
description: LHS results CSV summary

input_data:
  lhs_input:                   # must match your function's parameter name
    id: 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266   # the data id from `aero create`
    version: null              # must be null; see the key list below
    # fetch: false             # optional; see "Fetching an input yourself" below

output_data:
  summary:                     # must match AeroOutput(name="summary")
    collection_uuid: 94d05b66-bf20-435d-a406-2577096b6cb6
    collection_url: https://g-18d480.a4b6ed.a567.data.globus.org/

kwargs: {}                     # extra keyword args for the function
```

```sh
aero register -f analysis.yaml
aero register -f analysis.yaml -p ALL -k threshold=0.5
```

Accepted keys: `endpoint_uuid`, `function_uuid`, `description`, `policy`, `input_data`,
`output_data`, `kwargs`.

Each **`input_data` entry** accepts `id` (required), `version`, and
[`fetch`](#fetching-an-input-yourself). Each **`output_data` entry** accepts `collection_uuid` and
`collection_url`.

`version` must be `null`: every run resolves the input's latest version. Pinning an input to a
particular version is not implemented — give it an integer and the run fails on the worker rather
than at registration, because the entry is skipped by the step that resolves where the bytes live.
A function can *see* which version it got: declare `<name>_version` or `<name>_version_id`, as in
[writing functions](writing-functions.md#knowing-which-version-an-input-is).

### Fetching an input yourself

AERO downloads each input and hands your function a local path. For a **no-copy** input it fetches
the object's own url, signed when the relay supplied a signature — but only the input whose notify
triggered the run gets one. On a **private bucket** every other no-copy input is therefore fetched
unsigned and returns `403 Forbidden`. With two no-copy inputs that is one of them on every run.

AERO cannot sign; it holds no object-store credentials by design. Mark such an input `fetch: false`
and it hands over the url instead, downloading nothing:

```yaml
input_data:
  a_input:
    id: <data id>
    version: null
    fetch: false     # AERO passes the url; your function retrieves it
  b_input:
    id: <data id>
    version: null    # fetched as usual, arrives as a path
```

```python
def run(a_input_url, b_input, a_input_signed_url=None):
    ...
```

An opted-out input arrives as **`<name>_url`**, and `<name>` is not passed at all — so a function
still expecting a path fails immediately rather than silently receiving nothing.
`<name>_signed_url` is passed too if your signature names it, and is `None` for any input that did
not trigger this run.

Mark every input `fetch: false` and AERO downloads nothing at all. Provenance is unaffected either
way: the input's version is still resolved and recorded.

`fetch: false` on a **copy** input yields the collection url, which still needs a Globus transfer
token to read — so this is really for no-copy inputs.

### Policies

| Name | Value | |
|---|---|---|
| `ANY` | 2 | Rerun when **any** input has a new version. The usual choice |
| `ALL` | 3 | Rerun only when **all** inputs have a version newer than the last run |
| `TIMER` | 1 | Run on a schedule |
| `INGESTION` | 0 | This flow *is* a timer-driven ingestion |
| `INGESTION_EVENT` | 4 | Ingestion driven by a notify webhook, no timer |
| `NONE` | -1 | Never runs on its own |

`register` is for `ANY`/`ALL`. The two ingestion policies belong to
[`aero create`](#aero-create), which defaults to `INGESTION_EVENT` and takes `--policy INGESTION`
for a timer.

### Analyses that store their own results

`output_data` is optional. Omit it when the analysis writes its result itself — back into the object
store it read from, say. The function then returns `None`, AERO records the run and the input
versions it consumed, and no output version is tracked:

```yaml title="terminal-analysis.yaml"
endpoint_uuid: fd0abffb-1e0b-403d-a2ec-2c53c9285df0
function_uuid: 229c5fe3-49dd-465e-8eb3-ba70d7351e3e
policy: ANY
description: summarize and write back to MinIO

input_data:
  lhs_input:
    id: 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266
    version: null

# no output_data
```

The trade-off: with no output there is nothing for another flow to depend on, so **nothing can be
registered downstream of it**.

### When a flow runs

Two conditions, and they are independent.

**Every input must have at least one version**, whatever the policy. The run resolves each input's
latest version, and a source that has been registered but never notified has none — the task would
fail with `No versions exist for this data ID`. So a flow whose inputs are not all populated yet
simply does not run, and starts running once they are.

**Then the policy decides how many must be new.** `ANY` fires when at least one input has a version
newer than the flow's `last_executed`; `ALL` only when every one of them does. Before the first run
there is no `last_executed`, so everything counts as new — but the requirement above still holds,
which is what stops `ALL` from firing on the first update to a single input.

With inputs that already have data, registration therefore runs the flow once immediately. Two
things delay that, and neither is a failure:

- an input with **no versions yet** — it runs when they all have some;
- a **no-copy** input — the registration run is skipped outright, because outside a notify there is
  no signed URL to read the object with, so the first notify becomes the first run.

Either way a freshly registered flow can sit at `last_executed: null` for a while.

### Already exists?

Registration deduplicates on a hash of the function, inputs, outputs and kwargs. Re-registering an
identical flow returns `501 Flow already exists`. Change something, or remove the old one with
[`delete-flows`](#aero-delete-flows).

---

## `aero types`

Lists notification types with their data id and registered objects.

```sh
aero types
aero types traffic
aero types --json
```

```
traffic (no-copy)
  data id: 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266
    object   http://minio.internal:9000/traffic/a.xml.gz
    pattern  http://minio.internal:9000/traffic/**/*.xml.gz
```

The data id shown here is what an analysis `input_data` entry should reference.

### Removing a url

`--remove-url` detaches one object or pattern from a type:

```sh
aero types traffic --remove-url http://minio.internal:9000/traffic/a.xml.gz
```

The type keeps its data id, its version history and every flow registered against it; only the
matching rule goes, and the key is then free for another type to claim. Give the url in whatever
form is to hand — a presigned one resolves to the same object.

A type with no urls left can no longer be reached by notify at all: resolution falls back to
untyped sources only, so `POST /data/notify` 404s even though the source's own url is unchanged.
For a no-copy source, where notify is the only way a version is ever recorded, that stops
ingestion until a url is added back with `aero create --type traffic --url ...`.

---

## `aero delete-flows`

Deletes every flow attached to a data id — the ingestion flow that produces it and the analyses
that consume it — so they can be re-registered.

```sh
aero delete-flows 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266
aero delete-flows 6f76e7b5-... -y        # skip the prompt
aero delete-flows 6f76e7b5-... --json
```

It lists what it found and asks first:

```
Flows attached to data 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266:
  ingestion  d725132c-ad2c-4adc-9081-cbd9d77fb6a0  INGESTION_EVENT
  analysis   f9aaedb0-2f23-42e3-bb69-0bc4dce4d7b2  ANY               LHS results CSV summary

Deleting 2 flow(s) also removes their provenance records and cancels any Globus timer driving them.
The data, its versions and any source type are kept.
Proceed? [y/N]
```

**This is irreversible.** It discards the provenance of those runs — which input versions each
consumed — and cancels their Globus timers. The source itself, its versions and any type survive,
so flows can be registered against the same data id afterwards.

It follows one level only: an analysis consuming this source produces its own output data, and
flows consuming *that* are left alone.

---

## `aero search`

Queries the server's Globus Search index.

```sh
aero search "traffic"
```

Returns nothing useful if the server runs with Globus Search disabled.

---

## `aero logout`

Clears the cached Globus tokens. The next command will prompt for login again.

```sh
aero logout
```
