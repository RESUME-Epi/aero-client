# AERO Client

![AERO](docs/static/img/aero-flow.png)

## What is AERO?

[AERO](https://github.com/RESUME-Epi/aero) is a service that runs your code whenever its input data
changes. It tracks versioned **data sources** and the **flows** that depend on them. When a source
changes, AERO records a new version and automatically reruns every flow that consumes it. Each
result is stored as a new version of its own, linked back to the exact input versions that produced
it, establishing the provenance of the result.

A flow can run any code, not only data analysis, it might rerun a simulation model, such as an epidemic or weather model, whenever updated observations or parameters arrive. Because results are themselves versioned sources, one flow's output can feed the next, so a change to raw data can ripple through a whole
chain of models and analyses.

A **source** can be **copied**, where AERO pulls the data and keeps its own versioned copy, or tracked
**by reference** (no-copy), where AERO records a new version from a change event and the flow
reads the object directly from where it is stored, such as an S3 bucket.

## What the client does

`aero-client` is the Python library for working with an AERO server, and includes
a command-line client `aero`.

With the CLI or the API you can:

- configure a connection to an AERO server
- register data sources, either copied or tracked by reference with a server
- register ingestion and analysis flows, along with the policies that decide when they run
- list sources, versions and flows, and trigger runs or failover by hand
- write the functions your flows run on a Globus Compute endpoint, whether analyses, simulation
  models or anything else, using the
  wrappers the client provides for reading inputs and recording outputs

## Installation

Requires Python 3.11 or newer. Clone the repository and install it:

```sh
pip install .
```

If your functions will run on a Globus Compute endpoint, install the client into that endpoint's
environment as well.

## Documentation

See [docs/index.md](docs/index.md) for links to configuration, the core concepts, the CLI reference, and the guide to writing functions.
