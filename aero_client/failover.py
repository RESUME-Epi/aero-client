"""Handing a failed analysis run to a failover endpoint, from inside the run.

For an analysis that can tell it has failed while its endpoint is still healthy
-- typically one that submits a job to an HPC cluster and polls it. AERO's own
failover only moves a run off an endpoint that reports offline before the run
starts; this is the analysis saying "this run failed here, run it elsewhere".

Runs on the Globus Compute worker. It must not import ``aero_client.api``,
which mints a token at import and can stop at an interactive login prompt.
"""

from typing import NoReturn


class FailoverRequested(RuntimeError):
    """Raised by ``request_failover`` to end the failed run.

    The analysis fails, so the commit step after it never records provenance
    for a run that produced nothing, and the Globus run shows as failed.

    ``submitted`` says whether AERO started a re-run, and ``endpoint`` where.
    """

    def __init__(self, message: str, submitted: bool = False, endpoint: str | None = None):
        super().__init__(message)
        self.submitted = submitted
        self.endpoint = endpoint


def request_failover(aero_run: dict, reason: str) -> NoReturn:
    """Ask AERO to re-run this flow on its next failover endpoint, then fail.

    Call it from an analysis function that declares an ``aero_run`` parameter,
    passing that through unchanged::

        def run_model(track, aero_run):
            from aero_client.failover import request_failover
            ...
            if state != "SUCCESS":
                request_failover(aero_run, reason=f"PBS job {job_id} {state}")

    The re-run skips the endpoint this run is on and every endpoint earlier runs
    in the chain failed on, and processes the same input versions this run did.
    If it fails too and asks again, the chain moves on to the next failover; it
    ends when none is left.

    Always raises ``FailoverRequested``, whether or not AERO started a re-run,
    so that this run stops here and is recorded as failed.

    Args:
        aero_run (dict): the ``aero_run`` value AERO passed the analysis.
        reason (str): why the run failed, for the server log.

    Raises:
        FailoverRequested: always.
    """
    import json
    import requests

    from aero_client.utils import CONF
    from aero_client.utils import build_url
    from aero_client.utils import load_tokens

    flow_id = aero_run.get("flow_id")
    if not flow_id:
        raise ValueError(
            "aero_run has no flow_id; pass the aero_run argument AERO gave the "
            "analysis function, unchanged."
        )

    failed = aero_run.get("endpoint")
    here = failed or "this endpoint"
    payload = {
        "failed_endpoint": failed,
        "tried_endpoints": list(aero_run.get("tried_endpoints") or []),
        "reason": reason,
        "input_versions": dict(aero_run.get("input_versions") or {}),
    }

    # The same credential the other worker callbacks in aero_client.jobs send.
    tokens = load_tokens()
    auth_token = tokens[CONF.portal_client_id]["refresh_token"]
    aero_headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-type": "application/json",
    }

    try:
        response = requests.post(
            build_url("flow", str(flow_id), "failover", "run"),
            headers=aero_headers,
            data=json.dumps(payload),
            verify=False,
            timeout=60,
        )
    except requests.RequestException as e:
        raise FailoverRequested(
            f"run failed on {here} ({reason}); asking AERO to re-run it "
            f"elsewhere failed: {e}"
        ) from e

    if response.status_code != 200:
        raise FailoverRequested(
            f"run failed on {here} ({reason}); AERO refused the failover "
            f"({response.status_code}): {response.content!r}"
        )

    result = response.json()
    if not result.get("submitted"):
        raise FailoverRequested(
            f"run failed on {here} ({reason}); AERO did not re-run it: "
            f"{result.get('reason')}"
        )

    endpoint = result.get("endpoint")
    raise FailoverRequested(
        f"run failed on {here} ({reason}); handed to {endpoint}",
        submitted=True,
        endpoint=endpoint,
    )
