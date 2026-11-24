![AERO](static/img/aero-flow.png)

# AERO client

AERO runs your code whenever its input data changes. It tracks versioned data sources and the flows
that depend on them. When a source changes, AERO records a new version and automatically reruns
every flow that consumes it. Each result is recorded as a new version linked back to the exact
input versions that produced it, establishing the provenance of the result.

A flow can run any code, not only data analysis. It might rerun a simulation model, such as an
epidemic or weather model, whenever updated observations or parameters arrive. Because results are
themselves versioned sources, one flow's output can feed the next, so a change to raw data can
ripple through a whole chain of models and analyses.

`aero-client` is the Python library for working with an AERO server, and includes the `aero`
command-line client. You use it to register sources and flows, list what AERO knows about, trigger
runs by hand, and write the functions your flows run on a Globus Compute endpoint.

## Installation

```sh
pip install -e .            # from a checkout
```

Python 3.11 or newer. If your functions will run on a Globus Compute endpoint, install the client
into that endpoint's environment too — and **register functions from a Python minor version
matching the endpoint's**, or the endpoint will fail to deserialize them.

## Getting started

Given a data file, a CSV file containing measures of genetic material (RNA) from influenza viruses in wastewater, for example, that is available for download from
a some public site, we want AERO to keep a versioned copy of that file,
perhaps dropping columns we don't need, and to rerun an analysis estimating the number
of flu cases every time a new version of the data is available. 


**1. Configure the client.** Point it at your AERO server with `aero configure` and log in with
Globus. [Configuration](configuration.md) covers `config.toml`, the Globus credentials you need for
registering functions, and access to the guest collection where AERO stores data. Note that this is typically only done whenever the `config.toml` changes. 

**2. Write an ingestion function (optional).** To transform the CSV before it is stored, write a
function that receives the downloaded file, drops the unused columns, and returns the result as an
`AeroOutput`. Register it with `register_function` to get a function uuid. Skip this step to store the file unchanged. See [Ingestion functions](writing-functions.md#ingestion-functions).

**3. Register the source.** Describe the source in a YAML file that gives the CSV's `url`, the
guest collection to store versions in (`collection_uuid` and `collection_url`), the Globus Compute
`endpoint_uuid` to run on, and your ingestion function's uuid as `function_uuid`. The source's
`name` must match that function's parameter name, so put a readable label in `description`. Set
`policy` to have AERO pull the file when it is notified of a change, or on a `timer`. Then run
[`aero create`](cli.md#aero-create) `-f source.yaml`; see [From a YAML file](cli.md#from-a-yaml-file)
for an example. The command prints a **data id**, which the analysis refers to.

**4. Write the analysis function.** It takes the wastewater file as a named input and returns its
results, such as a table of weekly trends, as an `AeroOutput`. Register it the same way to get its
function uuid. See [An analysis function](writing-functions.md#an-analysis-function).

**5. Register the analysis flow.** Describe the flow in a YAML file that names the function, the
endpoint, the data id from step 3 as its input, and where to store the output, then run
[`aero register`](cli.md#aero-register). The flow's [policy](cli.md#policies) decides when it
reruns.

From then on, each new version of the CSV is ingested and the analysis reruns on it, storing its
results as a new version linked to the input that produced them. Use [`aero list`](cli.md#aero-list)
to see sources, versions and flows, and [`aero run`](cli.md#aero-run) to run a flow by hand.

[Examples](examples.md) walks through this scenario in full, with the functions and YAML files.

## Additional Documentation

| | |
|---|---|
| [Configuration](configuration.md) | `config.toml`, profiles, Globus credentials, collection permissions |
| [CLI reference](cli.md) | Every command, with examples and the YAML each accepts |
| [YAML reference](yaml-reference.md) | Every key the `aero create` and `aero register` files accept |
| [Writing functions](writing-functions.md) | What your ingestion and analysis functions must look like |
| [Examples](examples.md) | Worked examples: a wastewater CSV copied by AERO, and no-copy files in an S3 bucket |

## Concepts

A **source** is a `Data` record with a series of versions. AERO stores metadata about it; whether it
stores the bytes is up to you.

A **copy** source is pulled and staged into a Globus guest collection, so AERO holds its own copy. A
**no-copy** source records a new version from the change event alone — nothing is pulled and no
bytes move, and the flow reads the object directly from where it is stored, such as an S3 bucket.
No-copy needs no collection, no Globus Compute endpoint, and no ingestion function.

A **type** groups several objects under one source, so a change to any of them drives the same
flow. Objects can be listed individually or matched by a glob pattern.

A **flow** is either an **ingestion**, which produces a source, or an **analysis**, which consumes
one or more sources and reruns when they change. "Analysis" is just AERO's name for the second kind:
its function can be any code, including a simulation model. A flow's **policy** decides what
triggers it.
