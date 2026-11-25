# Endpoint failover

Running a flow somewhere else when its Globus Compute endpoint is down, or when a run fails on it.

## The problem

Submitting a run to Globus **succeeds whether or not the compute endpoint is alive**. The run then
stalls or fails inside Globus, long after AERO accepted it. And because a flow's `last_executed` is
stamped when a run is submitted, not when it succeeds, the version that triggered the run now
counts as already processed and will never trigger it again.

So a dead endpoint doesn't produce an error. It produces a run that quietly never happened, and a
flow that has forgotten it needed to. Without failover, the only recovery is someone noticing and
running [`aero run --force`](cli.md#aero-run).

## What failover does

Give a flow one or more **failover endpoints**. Before submitting a run, AERO asks Globus Compute
whether the flow's own endpoint is online. If it reports **offline**, the run goes to the first
failover endpoint that isn't offline.

This applies to runs AERO submits in response to an event:

- analysis flows (`ANY` and `ALL` policies), whether triggered by a new input version, a no-copy
  notification, or `aero run`
- ingestion of a copy source driven by notifications (`INGESTION_EVENT`)

It does **not** apply to timer-driven flows (`INGESTION` and `TIMER`). Their Globus timer is
created once and then fires for days, so the endpoint's state when it was created says nothing
about its state when it fires.

A run that fails over doesn't move the flow. The next run checks the flow's own endpoint again
first.

### Only "offline" moves a run

An endpoint's status is `online`, `offline`, or `unknown`. Only `offline` moves a run.

`unknown` means AERO couldn't get an answer: a permission error, a timeout, a network failure.
That says nothing about the endpoint, and treating it as down would reroute every run whenever the
status service had a problem. If every endpoint is offline, the run goes to the flow's own endpoint
anyway: it fails visibly in Globus and `aero run --force` can recover it, whereas a dropped run
would leave nothing to find.

## Setting it up

Failover is per flow. Two analyses on the same endpoint can fall back to different places, because
what makes a fallback valid is whether it can run *that* analysis.

Add failover endpoints when you register the flow, with a `failover:` block in the YAML file:

```yaml title="analysis.yaml"
endpoint_uuid: 7591d0fb-3d12-4ff8-b8fb-7a51146c4871
function_uuid: cbaad5b0-01e4-47d7-ba87-2e5633ed0428
# ...input_data, output_data...

failover:
  - endpoint: 0ed1ddbc-ae82-428c-8431-989a295eaa6a
    priority: 1
  - endpoint: 3aa0677e-f729-45ba-895c-adc9937fb276
    priority: 2
    # this endpoint runs a different Python, so its functions are separate registrations
    function_uuid: 5e1c2f0a-8b7d-4c3e-9f21-6a4d0b7e8c13
    pull_function_uuid: 9b3f7d21-4e6a-4f8c-b0d5-2c1e8a7f6d94
    commit_function_uuid: 1f8e6c4b-3a2d-4b9e-8c7f-0d5a9e2b6c71
```

Or add them to a flow that is already registered:

```sh
aero failover add <flow-id> -e 0ed1ddbc-ae82-428c-8431-989a295eaa6a -p 1
aero failover list <flow-id>
```

Endpoints are tried in ascending `priority`, which must be unique within the flow. See the
[CLI reference](cli.md#aero-failover) for every `aero failover` command and flag, and the
[YAML reference](yaml-reference.md#failover-entries) for the `failover:` keys.

### Functions on a different Python

A function registered from one Python minor version doesn't deserialize on an endpoint running
another; the symptom is `loss of worker 0`. If a failover endpoint runs a different Python,
register the analysis function again from that version, and give the new uuid as the failover's
`function_uuid`.

The pull and commit functions are AERO's own steps that run before and after your function. If you
leave `pull_function_uuid` and `commit_function_uuid` out, the flow's own are used, which only
works on a matching Python.

### Preparing the failover endpoint

Being online is not enough. A failover endpoint needs the same setup as the flow's own:

- `AERO_PROFILE`, and `AERO_CONFIG_FILE` if you use one, set in its
  `~/.globus_compute/<name>/user_environment.yaml`, matching the primary. Functions read the client
  configuration from the endpoint's environment, not from the machine that registered them.
- Network access to whatever the analysis reads, such as an object store or a guest collection,
  since the endpoint downloads it. That includes the CA certificate for a self-signed store.
- `aero-client` and your analysis's own dependencies installed.

An endpoint that reports `online` but has no workers, the wrong profile, or a Python mismatch will
still fail the run. The status check tells you the endpoint is up, not that it can run your
function.

### Check it before relying on it

```sh
aero failover status <flow-id>
```

```
offline  primary   7591d0fb-3d12-4ff8-b8fb-7a51146c4871
online   failover  0ed1ddbc-ae82-428c-8431-989a295eaa6a
```

**If every line says `unknown`, failover will never fire.** An endpoint's status only answers to a
caller with a role on that endpoint, and the AERO server checks with its own Globus credential. If
that credential has no role on your endpoints, every check comes back `unknown`. This is the one
failure that looks exactly like a working setup, so ask the server's operator to fix it before you
rely on failover.

## When a run fails on a healthy endpoint

The check above only moves a run off an endpoint that is offline **before** the run starts. It
can't see a run that reaches a healthy endpoint and then fails there. The usual case is an analysis
that submits a job to an HPC cluster and watches it: the job is rejected or dies, and the analysis
knows, while the endpoint still reports `online`.

So the analysis can ask AERO to run the flow again elsewhere, by calling
`aero_client.failover.request_failover`. See
[Handing a failed run to the failover endpoint](writing-functions.md#handing-a-failed-run-to-the-failover-endpoint)
for the code. To do the same by hand, use [`aero failover run`](cli.md#running-on-a-failover-by-hand).

The re-run:

- **goes to the next endpoint that hasn't failed.** The candidates are the flow's own endpoint,
  then its failovers in priority order. AERO skips the endpoint the run failed on, every endpoint
  earlier runs in the chain failed on, and any that reports offline. If the run first failed over
  because the flow's own endpoint was offline, and that endpoint is back, the re-run goes there.
- **can fail over again.** Each re-run can call `request_failover` in turn. Every attempt adds an
  endpoint to the skip list, so once every endpoint has been tried AERO declines and the chain
  ends, rather than looping.
- **processes the same data.** It uses the same input versions as the failed run, even if newer
  versions have arrived since. HPC jobs can run for hours.
- **is always forced**, because the failed run already marked its inputs as processed.

### Limitations

- **No fresh presigned URL.** A run triggered by a notification for a no-copy source in a private
  bucket gets a presigned URL to download it. The re-run doesn't, because AERO holds no object-store
  credentials to make one. Only inputs marked
  [`fetch: false`](cli.md#fetching-an-input-yourself), which your function retrieves with its own
  credentials, survive a re-run.
- **The endpoint needs a current client.** Both endpoints need an `aero-client` that has
  `aero_client.failover`, and the function must be re-registered so it gets the wrapper that passes
  `aero_run`.
- **It authenticates as the endpoint.** `request_failover` uses the Globus token stored on the
  endpoint host, the same way AERO's own pull and commit steps do.

## Where did a run go?

AERO doesn't record which endpoint a run used. The only places it appears are the server log and
the output of the command that submitted the run, which `aero run` and `aero failover run` print:

```
Submitted a run of flow f9aaedb0-....
  endpoint: 0ed1ddbc-ae82-428c-8431-989a295eaa6a (failover)
  last_executed: 2026-10-30T14:02:11+00:00
```
