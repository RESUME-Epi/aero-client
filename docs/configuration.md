# Configuration

The client needs two things before any command works: a `config.toml` naming the AERO server and
the Globus application it authenticates against, and — if you will register functions or flows —
Globus credentials in the environment.

## config.toml

The `config.toml` can contain multiple **profiles**, one table per environment, selected by names so a single file can hold production and test settings, 
for example.

```toml
# config.toml
[default]
client_uuid      = "32aa2afb-8f11-4012-8bb3-c18329d815db"
portal_client_id = "7408753c-04de-42b9-8ca3-3db94cd2a848"
server           = "https://aero.foo.edu"
cache_dir        = "~/.local/share/aero_production"

[testing]
client_uuid      = "32aa2afb-8f11-4012-8bb3-c18329d815db"
portal_client_id = "7408753c-04de-42b9-8ca3-3db94cd2a848"
server           = "http://127.0.0.1/aero-testing"
cache_dir        = "~/.local/share/aero_testing"
```

These are **two different Globus applications**, both registered through the
[Globus developer dashboard](https://app.globus.org/settings/developers):

| Key | Meaning |
|---|---|
| `client_uuid` | The **thick client** — the application this CLI authenticates *as*. You create it yourself; it is the identity that ends up holding transfer tokens and needing access to collections. |
| `portal_client_id` | The **AERO server portal** — the resource server this client requests a scope *on*. It must match the `PORTAL_CLIENT_ID` the AERO server runs with, because the `action_all` scope the client asks for is derived from it. |
| `server` | Base URL of the AERO server, including any path prefix (`/fhwa`, `/aero-testing`). |
| `cache_dir` | Where the client keeps its tokens and a copy of this file. |


### Applying the configuration

```sh
aero configure -f config.toml              # the [default] profile
aero configure -f config.toml -p testing   # a named profile
```

This copies the file into `cache_dir` and points `~/.aero` at that directory, which is where every
later command reads it from. It prints the resolved configuration, which is the quickest way to
check the server URL and cache directory came out as you intended.

### Selecting a profile afterwards

`aero configure` records one profile. To switch per-command without reconfiguring, use the
environment:

| Variable | Effect |
|---|---|
| `AERO_PROFILE` | Profile to read from `config.toml`. Defaults to `default`. |
| `AERO_CONFIG_FILE` | Path to a `config.toml` to use instead of `~/.aero/config.toml`. Leaves the `~/.aero` symlink alone. |

```sh
AERO_PROFILE=testing aero list
```

Both are read **once, when the client is imported**, so they must be set before the command runs,
not changed during it.

!!! warning "Globus Compute endpoints need this too"

    Worker functions resolve their configuration from the *endpoint's* environment, not from the
    machine that registered them. If your endpoint should talk to a non-default profile, set
    `AERO_PROFILE` in the endpoint's `user_environment.yaml` — otherwise the worker will use
    `default` and post results to the wrong server.

## Authenticating

The first command that talks to the server triggers a Globus login:

```sh
aero list
```

Follow the printed URL, authorize, and paste the code back. Tokens are cached in
`cache_dir/client_tokens.json` and reused. `aero logout` clears them.

## Globus credentials for registering functions and flows

Registering a Globus Compute function or starting an endpoint needs a Globus **client id and
secret** in the environment. These are the **thick client's** — the same UUID as `client_uuid` in
`config.toml` — together with a secret generated for it in the developer dashboard:

```sh
export GLOBUS_COMPUTE_CLIENT_ID=<client_uuid>
export GLOBUS_COMPUTE_CLIENT_SECRET=<its secret>

# the globus CLI reads its own pair; set them to the same values
export GLOBUS_CLI_CLIENT_ID=$GLOBUS_COMPUTE_CLIENT_ID
export GLOBUS_CLI_CLIENT_SECRET=$GLOBUS_COMPUTE_CLIENT_SECRET
```

These must be exported in **all** of: the shell that starts the Globus Compute endpoint, the shell
that registers a function, and the shell that registers a flow. Registering with a different
identity than the endpoint runs as is a common cause of runs that never execute.

## Guest collection access

A **copy** source stages its data into a Globus guest collection, which needs two one-time steps.

Fetch a transfer token for the collection:

```python
from aero_client.utils import get_transfer_token

get_transfer_token("<collection_uuid>")
```


**No-copy sources skip all of this** — nothing is staged, so no collection and no transfer token
are involved.

## Checklist for a working setup

- [ ] `config.toml` written and applied with `aero configure -f config.toml`
- [ ] `aero list` completes, having prompted for a Globus login once
- [ ] `GLOBUS_COMPUTE_CLIENT_ID` / `_SECRET` exported, if you will register functions or flows
- [ ] `aero-client` installed in the Globus Compute endpoint's environment, at a Python minor
      version matching the one you register functions from
- [ ] the endpoint is actually running
- [ ] *(copy sources only)* transfer token fetched and the client granted access to the collection
