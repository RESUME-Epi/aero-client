# Examples

Two worked examples.

- [Wastewater CSV from a public site](#wastewater-csv-from-a-public-site) — a **copy** source.
  AERO pulls the file on a timer, cleans it up with an ingestion function, keeps every version, and
  reruns an analysis on each new one.
- [Daily files in an S3 bucket](#daily-files-in-an-s3-bucket) — a **no-copy** source. AERO
  copies nothing; a bucket event tells it a file changed, and the analysis is given the file's URL
  and reads it from the bucket itself.

Both assume the client is [configured](configuration.md) and logged in. The uuids below are
placeholders; use your own collection, endpoint and function ids.

---

## Wastewater CSV from a public site

A public health lab publishes measurements of influenza RNA in wastewater as a CSV at
`https://data.example.org/wastewater/flu.csv`, overwriting it as new samples come in. We want AERO
to keep a versioned copy with the columns we don't use dropped, and to rerun a model estimating flu
cases whenever the data changes.

The site sends no change notifications, so AERO pulls the file once a day. A pull that finds the
file unchanged records no new version, so the analysis runs only when the data actually changes.

You need:

- a Globus guest collection for AERO to store versions in, readable by your client
  (see [guest collection access](configuration.md#guest-collection-access))
- a Globus Compute endpoint with `aero-client` and `pandas` installed

### 1. Write and register the ingestion function

The ingestion function receives the downloaded file and returns the version to store. Its
parameter name, the `AeroOutput` name and the source's `name` must all be the same: `wastewater`.

```python title="wastewater_ingestion.py"
def wastewater_ingestion(wastewater):
    """Clean the downloaded CSV before AERO stores it as a new version."""
    import pandas as pd
    from aero_client.utils import AeroOutput

    df = pd.read_csv(wastewater)

    # --- your clean-up here: drop columns, fix types, filter rows ---
    df = df.drop(columns=["influenza_b"])

    df.to_csv(wastewater, index=False)
    return AeroOutput(name="wastewater", path=wastewater)


if __name__ == "__main__":
    from aero_client.utils import register_function

    print(register_function(wastewater_ingestion))
```

```sh
python wastewater_ingestion.py     # prints the function uuid
```

Everything the function needs is imported inside it, because the function is serialized and run on
the endpoint. **Register it from the same Python minor version the endpoint runs.**

### 2. Register the source

```yaml title="wastewater-source.yaml"
name: wastewater
description: Influenza RNA in wastewater, by sampling site
url: https://data.example.org/wastewater/flu.csv

collection_uuid: 94d05b66-bf20-435d-a406-2577096b6cb6
collection_url: https://g-18d480.a4b6ed.a567.data.globus.org/
endpoint_uuid: fd0abffb-1e0b-403d-a2ec-2c53c9285df0
function_uuid: 443d9008-d1dd-411d-a3d4-2651d172b0b9   # from step 1

policy: INGESTION       # pull on a timer
timer: 86400            # once a day
```

```sh
aero create -f wastewater-source.yaml
```

It prints the source's **data id**:

```
Source data id: 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266
```

Once the first pull has run, `aero list -i 6f76e7b5-...` shows version 1.

### 3. Write and register the analysis function

The parameter name, `wastewater`, must match the `input_data` key in the yaml file
used to register the analysis, and the
`AeroOutput` name must match the `output_data` key. `window_days` comes from the flow's `kwargs`.

```python title="estimate_cases.py"
def estimate_cases(wastewater, window_days=7):
    """Estimate flu cases from the latest wastewater data."""
    import pandas as pd
    from aero_client.utils import AeroOutput

    df = pd.read_csv(wastewater)

    # --- call your model here ---
    estimates = df  # placeholder: replace with the model's output table

    out_path = f"{wastewater}.estimates.csv"
    estimates.to_csv(out_path, index=False)
    return AeroOutput(name="estimates", path=out_path)


if __name__ == "__main__":
    from aero_client.utils import register_function

    print(register_function(estimate_cases))
```

```sh
python estimate_cases.py           # prints the function uuid
```

### 4. Register the analysis flow

```yaml title="estimate-cases.yaml"
endpoint_uuid: fd0abffb-1e0b-403d-a2ec-2c53c9285df0
function_uuid: cbaad5b0-01e4-47d7-ba87-2e5633ed0428   # from step 3
policy: ANY
description: Flu case estimates from wastewater

input_data:
  wastewater:
    id: 6f76e7b5-0f1d-4b4d-9769-e8b85dca5266          # from step 2
    version: null

output_data:
  estimates:
    collection_uuid: 94d05b66-bf20-435d-a406-2577096b6cb6
    collection_url: https://g-18d480.a4b6ed.a567.data.globus.org/

kwargs:
  window_days: 7
```

```sh
aero register -f estimate-cases.yaml
```

It prints the **output data id**, the source that holds the estimates. If the wastewater source
already has a version, the flow runs once straight away.

### 5. What happens from here

Each day AERO pulls the CSV. When it has changed, the ingestion function cleans it, AERO stores it
as a new version, and `estimate_cases` runs on it. Each set of estimates is stored as a new version
of the output source, linked to the wastewater version it was computed from.

```sh
aero list -i <output data id>      # every set of estimates
aero list -t prov                  # which input version produced which output
```

To check the file more often, or have the lab's site tell AERO when it changes, see
[policies](cli.md#policies) and [triggering a pull](#triggering-a-run-yourself) below. Because the
estimates are a source of their own, another flow can use them as its input.

---

## Daily files in an S3 bucket

Given a scenario where one CSV per day of wastewater sampling is written into a private S3 bucket, as `wastewater/2026-11-24.csv` and so on, in the bucket `flu-surveillance`. We want an analysis to run
on each new file. The bucket already keeps the data, so AERO doesn't need a copy: a **no-copy**
source records a version for each file from the bucket's change event, and the analysis reads the
file from the bucket.

No-copy needs no guest collection and no ingestion function. You still need a Globus Compute
endpoint for the analysis to run on.

### 1. Register the source

One source covers every file in the folder, using a **type** with a glob pattern, so files added
later are picked up too.

```yaml title="daily-source.yaml"
name: daily wastewater samples
description: One CSV per sampling day from the partner lab
type: daily-wastewater
url: https://s3.us-east-2.amazonaws.com/flu-surveillance/wastewater/*.csv
no_copy: true
```

```sh
aero create -f daily-source.yaml
```

```
Source data id: 2c1e7a90-5b7d-4f0e-9a51-0d3f6e8b4c21
```

!!! tip "Use a path-style URL"

    AERO identifies an object by the path of its URL: `bucket/key`. A path-style URL
    (`https://s3.<region>.amazonaws.com/<bucket>/<key>`) puts the bucket in that path. A
    virtual-hosted URL (`https://<bucket>.s3.amazonaws.com/<key>`) leaves it out, so use the same
    form in the source and in every notification.

With no ingestion function, a no-copy source's `name` is free-form.

### 2. Write and register the analysis function

AERO does not download anything for this analysis. It passes the URL of the file that changed as
`sample_url`, and the function reads the file from the bucket itself, with its own S3 credentials.
The URL also says which day's file this run is for.

This analysis writes its results back to the bucket too, so it returns `None` and the flow declares
no `output_data`.

```python title="summarize_sample.py"
def summarize_sample(sample_url):
    """Analyze one day's file and write the result back to the bucket."""
    import os

    # --- read the file at sample_url here, e.g. with boto3 ---
    # the endpoint needs its own S3 credentials for this; AERO holds none

    # --- call your analysis here ---

    # --- write the result back to the bucket here ---
    day = os.path.splitext(os.path.basename(sample_url))[0]   # e.g. 2026-11-24

    return None


if __name__ == "__main__":
    from aero_client.utils import register_function

    print(register_function(summarize_sample))
```

```sh
python summarize_sample.py         # prints the function uuid
```

To have AERO store the result instead, return an `AeroOutput` and add `output_data`, as in the
first example. That needs a guest collection, but lets other flows build on the result.

### 3. Register the analysis flow

`fetch: false` tells AERO to pass the input's URL as `sample_url` instead of downloading it and
passing a path as `sample`.

```yaml title="summarize-sample.yaml"
endpoint_uuid: fd0abffb-1e0b-403d-a2ec-2c53c9285df0
function_uuid: 229c5fe3-49dd-465e-8eb3-ba70d7351e3e   # from step 2
policy: ANY
description: Daily wastewater sample summary

input_data:
  sample:
    id: 2c1e7a90-5b7d-4f0e-9a51-0d3f6e8b4c21          # from step 1
    version: null
    fetch: false                                      # pass the url, not the file

# no output_data: the function writes its own results
```

```sh
aero register -f summarize-sample.yaml
```

A flow on a no-copy input does not run at registration. Its first run is the first notification.

### 4. Send a notification when a file lands

Something has to tell AERO each time a file is written. Usually that is a small service that
receives the bucket's event notifications and passes each one on to AERO's notify webhook. It can also be done manually with the following. Here, because
the analysis reads the file itself, the notification only needs to say which object changed:

```sh
curl -X POST "$AERO_SERVER/data/notify" \
  -H "Content-Type: application/json" \
  -H "X-Aero-Token: $AERO_WEBHOOK_SECRET" \
  -d '{
        "file_id": "flu-surveillance/wastewater/2026-11-24.csv",
        "etag": "e55c21331b43487d86e269e47edcf3a3",
        "size": 48213
      }'
```

| Field | |
|---|---|
| `file_id` | The object that changed, as `bucket/key` or a full URL. AERO matches it against the source's pattern |
| `etag`, `size` | From the event. A notification with an unchanged `etag` records no new version |

`$AERO_SERVER` is the same `server` URL as in your `config.toml`. The `X-Aero-Token` header is
needed only if the AERO server is set up with a webhook secret.

### 5. What happens from here

Each notification records a new version of the source for that file, and `summarize_sample` runs
with that file's URL. The results go wherever the function puts them, and AERO records which file
version each run used.

```sh
aero list -i 2c1e7a90-...          # one version per notified file
aero types daily-wastewater        # the pattern the source matches
```

To rerun the analysis without a new notification, for example after a failed run, use
`aero run <flow id> --force`. It reruns on the most recently notified file, and because the function
fetches the file itself, it needs no presigned URL.

---

## Triggering a run yourself

For a source whose `policy` is `INGESTION_EVENT`, the default, AERO pulls the file when it is told
the file changed rather than on a timer. A notification for a copy source can go to the source's
own data id:

```sh
curl -X POST "$AERO_SERVER/data/<data id>/notify" \
  -H "X-Aero-Token: $AERO_WEBHOOK_SECRET"
```

To rerun an analysis flow on the data it already has, for example after a failed run, use
[`aero run`](cli.md#aero-run):

```sh
aero list -t flow                  # find the flow id
aero run <flow id> --force
```
