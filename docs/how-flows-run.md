# How flows run

How AERO connects flows to the data they use, and what happens when a source gets a new version.

## How flows are connected

Flows never refer to each other directly. They are connected through the data they share:

- An **ingestion** flow produces a source. `aero create` registers both together and prints the
  source's data id.
- An **analysis** flow lists the data ids it consumes in `input_data`, and declares what it
  produces in `output_data`. Each `output_data` entry becomes a new data record of its own, and
  `aero register` prints its id.

So an analysis depends on a source by naming its data id, and a second analysis can depend on the
first by naming the first one's output data id. That is how a change to raw data ripples through a
chain of analyses.

## What happens when a source changes

1. **A new version is recorded.** For a copy source, the ingestion flow downloads the object and
   stores it, and AERO records a version if the contents changed. For a no-copy source, AERO
   records the version straight from the change notification.
2. **AERO finds the flows that depend on it**: every analysis that lists this data id in its
   `input_data`.
3. **Each flow's policy decides whether it runs.** `ANY` runs when at least one input has a version
   newer than the flow's last run; `ALL` only when every input does. See
   [When a flow runs](cli.md#when-a-flow-runs) for the details.
4. **AERO submits the run to Globus.** The run has three steps on your endpoint: a pull step
   resolves each input's latest version, your function runs, and a commit step records the result.
   The flow's `last_executed` is stamped when the run is submitted.
5. **The result becomes a new version.** The commit step stores each output as a new version of
   the flow's output data, with a provenance record linking it to the input versions it came from.
   That new version starts the same process for any flow downstream.

All of this starts from step 1, so only `ANY` and `ALL` flows react to new data. `TIMER` flows run
on a schedule, and the two ingestion policies belong to sources. See
[Policies](cli.md#policies).

To see the links between versions, use `aero list -t prov`.

## Analyses with no output

`output_data` is optional. Leave it out when the analysis stores its results itself, for example
back into the object store it read from. The function returns `None` instead of an `AeroOutput`, no
output data record is created, and no output version is recorded. AERO still records which input
versions the run consumed.

The cost is the connection described above: with no output data, there is nothing for another flow
to depend on, so **nothing can be registered downstream of it**. Declare `output_data` if anything
else needs to react to the result.

!!! warning "Two output-less analyses can collide"

    AERO rejects a flow identical to one already registered, with `501 Flow already exists`. The
    comparison covers the function, inputs, outputs and kwargs. Two different analyses with no
    output that share a function, inputs and kwargs therefore look identical, and the second is
    rejected. Giving them different kwargs or functions avoids it.

## Removing the flows around a source

[`aero delete-flows <data-id>`](cli.md#aero-delete-flows) removes every flow attached to a data
id: the flow that produces it and the analyses that consume it. This is how to re-register a flow
that would otherwise be rejected as already existing.

It also deletes their provenance records and cancels any Globus timer driving them. The data
record, its versions and any source type are kept, so you can register new flows against the same
data id.

It follows one level only: an analysis consuming this data produces its own output data, and the
flows consuming *that* are left alone.
