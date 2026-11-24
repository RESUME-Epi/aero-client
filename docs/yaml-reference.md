# YAML reference

[`aero create`](cli.md#aero-create) and [`aero register`](cli.md#aero-register) both take a YAML
file with `-f/--file`. This page lists every key each one accepts. For worked examples, see the
CLI reference.

## How the file and the flags combine

- A flag given on the command line overrides the same key in the file.
- `-k KEY=VALUE` is merged over the file's `kwargs` block rather than replacing it. Values given
  with `-k` are always strings, so use the `kwargs` block for numbers and booleans.
- Structured keys — `input_data`, `output_data` and `failover` on `register` — can only come from
  the file.
- **Unknown keys are ignored without a warning**, so a misspelled key behaves as if it were absent.
  If a value seems to have no effect, check its spelling.

## Source file (`aero create`)

```yaml title="source.yaml"
name: wastewater                # matches the ingestion function's parameter
description: Influenza A RNA in wastewater, by sampling site
url: https://data.example.org/wastewater/flu.csv
collection_uuid: 94d05b66-bf20-435d-a406-2577096b6cb6
collection_url: https://g-18d480.a4b6ed.a567.data.globus.org/
endpoint_uuid: fd0abffb-1e0b-403d-a2ec-2c53c9285df0
function_uuid: 443d9008-d1dd-411d-a3d4-2651d172b0b9
policy: INGESTION_EVENT
```

```sh
aero create -f source.yaml
```

| Key | Type | Required | |
|---|---|---|---|
| `name` | string | yes¹ | Name of the source. With an ingestion function, it must match that function's parameter name and the `AeroOutput(name=...)` it returns |
| `url` | string | yes | URL of the object, or a [glob pattern](cli.md#matching-objects-with-a-glob) |
| `description` | string | no | Human-readable label. Put it here rather than in `name` when `name` has to be a parameter name |
| `no_copy` | boolean | no | `true` to track changes without copying any data. Defaults to `false` |
| `type` | string | no | Group this url under a named [type](cli.md#adding-more-objects-to-a-type) |
| `collection_uuid` | string | copy only | Guest collection to store versions in |
| `collection_url` | string | copy only | That guest collection's HTTPS domain |
| `endpoint_uuid` | string | copy only | Globus Compute endpoint the ingestion runs on |
| `function_uuid` | string | no | Ingestion function that transforms the pulled file. Omit it to store the file unchanged. Copy sources only |
| `verifier` | string | no | Older name for `function_uuid`. If both are set, `verifier` wins |
| `policy` | string or int | no | `INGESTION_EVENT` (default) pulls when AERO is notified of a change; `INGESTION` pulls on a timer |
| `timer` | int | no | Seconds between pulls. Only with `policy: INGESTION`; defaults to the server's 86400 (24h) |
| `kwargs` | mapping | no | Extra keyword arguments for the ingestion function. Copy sources only |

¹ Not needed when adding a url to a type that already exists.

What is required depends on the kind of source:

| Kind | Needs |
|---|---|
| Copy (the default) | `name`, `url`, `collection_uuid`, `collection_url`, `endpoint_uuid` |
| No-copy (`no_copy: true`) | `name`, `url` |
| Another url for an existing type | `type`, `url` — every other key is ignored |

Some combinations are rejected rather than ignored: a `timer` without `policy: INGESTION`, and a
`timer`, `policy: INGESTION` or `kwargs` on a no-copy source, which runs no function and no flow.

!!! note "`--no-copy` can only switch it on"

    `no_copy: true` in the file cannot be turned off from the command line; edit the file instead.

## Flow file (`aero register`)

```yaml title="analysis.yaml"
endpoint_uuid: fd0abffb-1e0b-403d-a2ec-2c53c9285df0
function_uuid: cbaad5b0-01e4-47d7-ba87-2e5633ed0428
policy: ANY
description: Estimate flu cases from wastewater RNA

input_data:
  wastewater:                   # matches the analysis function's parameter
    id: 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266
    version: null

output_data:
  estimates:                    # matches AeroOutput(name="estimates")
    collection_uuid: 94d05b66-bf20-435d-a406-2577096b6cb6
    collection_url: https://g-18d480.a4b6ed.a567.data.globus.org/

kwargs:
  window_days: 7

failover:
  - endpoint: 0ed1ddbc-ae82-428c-8431-989a295eaa6a
    priority: 1
```

```sh
aero register -f analysis.yaml
```

| Key | Type | Required | |
|---|---|---|---|
| `endpoint_uuid` | string | yes | Globus Compute endpoint the function runs on |
| `function_uuid` | string | yes | The registered function to run |
| `input_data` | mapping | yes | The sources the flow consumes; at least one. See [below](#input_data-entries) |
| `output_data` | mapping | no | Where results are stored. Omit it when the function [stores its own results](cli.md#analyses-that-store-their-own-results). See [below](#output_data-entries) |
| `policy` | string or int | no | `ANY` (default) reruns when any input has a new version; `ALL` only when every input does. See [policies](cli.md#policies) |
| `description` | string | no | Human-readable label |
| `kwargs` | mapping | no | Extra keyword arguments for the function |
| `failover` | list | no | Endpoints to fall back to when `endpoint_uuid` is offline. See [below](#failover-entries) |

### `input_data` entries

Each key is the name of the function parameter the input arrives as.

| Key | Type | Required | |
|---|---|---|---|
| `id` | string | yes | The source's data id, as printed by `aero create` |
| `version` | null | no | Must be `null`: every run uses the latest version. Pinning a version is not implemented, and an integer makes the run fail on the worker |
| `fetch` | boolean | no | `false` to pass the function the object's url as `<name>_url` instead of downloading it. Defaults to `true`. See [fetching an input yourself](cli.md#fetching-an-input-yourself) |

### `output_data` entries

Each key must match the `AeroOutput(name=...)` the function returns.

| Key | Type | Required | |
|---|---|---|---|
| `collection_uuid` | string | yes | Guest collection to store the output in |
| `collection_url` | string | yes | That guest collection's HTTPS domain |

### `failover` entries

The same fields as [`aero failover add`](cli.md#aero-failover), which attaches a failover to a flow
that is already registered.

| Key | Type | Required | |
|---|---|---|---|
| `endpoint` | string | yes | Globus Compute endpoint to fall back to |
| `priority` | int | no | Try order, ascending. Defaults to `0`, and must be unique within the flow, so give each entry its own |
| `function_uuid` | string | no | Function to run there instead of the flow's own — needed when that endpoint runs a different Python minor version |
| `pull_function_uuid` | string | no | Likewise for the pull function |
| `commit_function_uuid` | string | no | Likewise for the commit function |
| `enabled` | boolean | no | `false` to keep the entry but skip it. Defaults to `true` |
