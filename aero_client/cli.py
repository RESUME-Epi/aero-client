"""Osprey Command-line interface."""

import argparse
import dataclasses
import json
import logging
import os
import sys

from pprint import pprint

from aero_client.config import load_conf


logger = logging.getLogger(__name__)


def main():
    """
    TODO
    """
    parser = argparse.ArgumentParser(
        description="AERO client for querying stored data",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available actions")
    list_parser = subparsers.add_parser("list", help="List aero metadata")
    create_parser = subparsers.add_parser(
        "create", help="Create a source to store in AERO"
    )
    # get_parser = subparsers.add_parser("get", help="Get source table from server")
    types_parser = subparsers.add_parser(
        "types", help="List notification source types and their data ids"
    )
    delete_flows_parser = subparsers.add_parser(
        "delete-flows",
        help="Delete every flow attached to a data id (ingestion + analyses)",
    )
    search_parser = subparsers.add_parser("search", help="Search sources")
    register_parser = subparsers.add_parser("register", help="Register analysis flow")
    config_parser = subparsers.add_parser("configure", help="Configure the client")
    _ = subparsers.add_parser("logout", help="Log out of Globus auth")

    parser.add_argument("-l", "--log", type=str, default="INFO", help="Set log level")

    lp_g = list_parser.add_mutually_exclusive_group()
    lp_g.add_argument(
        "-t",
        "--type",
        choices=["data", "flow", "prov"],
        default="data",
        help="List metadata related to monitored data, flows or provenance. Defaults to `data`",
    )

    lp_g.add_argument(
        "-i",
        "--id",
        type=str,
        required=False,
        help="list all versions associated with provided data id",
    )

    # create_parser arguments
    create_parser.add_argument(
        "-f",
        "--file",
        type=str,
        default=None,
        help="YAML file supplying any of the create arguments (name, url, "
        "collection_url, collection_uuid, endpoint_uuid, verifier, description). "
        "Explicit CLI flags override values from the file.",
    )
    create_parser.add_argument(
        "-n",
        "--name",
        type=str,
        required=False,
        help="Name for the source",
    )
    create_parser.add_argument(
        "-u",
        "--url",
        type=str,
        required=False,
        help="URL to retrieve the source from",
    )
    create_parser.add_argument(
        "-c",
        "--collection-url",
        type=str,
        required=False,
        help="The GCS Guest Collection domain name",
    )
    create_parser.add_argument(
        "-C",
        "--collection-uuid",
        type=str,
        required=False,
        help="The GCS Guest Collection UUID to store the pulled file in",
    )
    create_parser.add_argument(
        "-g",
        "--endpoint-uuid",
        type=str,
        required=False,
        help="The Globus Compute Endpoint UUID to run ingestion flow on",
    )
    create_parser.add_argument(
        "-t",
        "--timer",
        default=None,
        type=int,
        help="Seconds between pulls. Implies a timer-driven source, so it "
        "requires --policy INGESTION. Left unset the server default (86400) "
        "applies.",
    )
    create_parser.add_argument(
        "-p",
        "--policy",
        type=str,
        default=None,
        help="How the source is pulled: INGESTION_EVENT (default, on the notify "
        "webhook) or INGESTION (on a timer).",
    )
    create_parser.add_argument(
        "-k",
        "--kwargs",
        metavar="KEY=VALUE",
        nargs="+",
        default=None,
        help="Extra keyword arguments for the verifier function "
        "(override/augment file kwargs). Values are passed as strings.",
    )
    create_parser.add_argument(
        "-d",
        "--description",
        default=None,
        type=str,
        help="description for the source",
    )
    create_parser.add_argument(
        "-v",
        "--verifier",
        default=None,
        type=str,
        help="globus-compute function uuid for the verifier",
    )
    create_parser.add_argument(
        "-m",
        "--modifier",
        default=None,
        type=str,
        help="globus-compute function uuid for the modifier",
    )
    create_parser.add_argument(
        "-e",
        "--email",
        type=str,
        default=None,
        help="email address to send notifications to in case of failure",
    )
    # No short flag: -t is already --timer on this subcommand.
    create_parser.add_argument(
        "--type",
        type=str,
        default=None,
        help="Group this url under a named type. If the type already exists the "
        "url is added to it and its existing data id is returned, so analysis "
        "flows registered against that id also run when this object changes.",
    )
    create_parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Track changes without copying any data: notify records a version "
        "from the event metadata and the analysis fetches the object by url. "
        "No collection, endpoint or function is needed.",
    )

    types_parser.add_argument(
        "name", nargs="?", type=str, help="Show only this type"
    )
    types_parser.add_argument(
        "--remove-url",
        type=str,
        metavar="URL",
        help="Remove this url from the named type. The type keeps its data id, "
        "versions and flows; a type left with no urls can no longer be reached "
        "by notify.",
    )
    types_parser.add_argument(
        "--json", action="store_true", help="Emit raw JSON instead of a summary"
    )

    delete_flows_parser.add_argument(
        "data_id", type=str, help="Data uuid whose flows should be deleted"
    )
    delete_flows_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )
    delete_flows_parser.add_argument(
        "--json", action="store_true", help="Emit raw JSON instead of a summary"
    )

    search_parser.add_argument("query", type=str, help="query to pass to search engine")

    register_parser.add_argument(
        "-f",
        "--file",
        type=str,
        default=None,
        help="YAML file describing the analysis flow (endpoint_uuid, function_uuid, "
        "policy, description, input_data, output_data, kwargs). output_data may be "
        "omitted if the analysis stores its own results. Explicit CLI flags "
        "override scalar values from the file.",
    )
    register_parser.add_argument(
        "-e", "--endpoint-uuid", type=str, help="Globus Compute endpoint uuid"
    )
    register_parser.add_argument(
        "-u",
        "--function-uuid",
        type=str,
        help="Globus Compute registered function UUID",
    )
    register_parser.add_argument(
        "-p",
        "--policy",
        type=str,
        default=None,
        help="Rerun policy: name (ANY, ALL, TIMER, ...) or int. Defaults to ANY "
        "(rerun when any input gets a new version).",
    )
    register_parser.add_argument(
        "-k",
        "--kwargs",
        metavar="KEY=VALUE",
        nargs="+",
        default=None,
        help="Keyword arguments to pass to function (override/augment file kwargs)",
    )
    register_parser.add_argument(
        "-d", "--description", type=str, default=None, help="Description of task"
    )

    config_parser.add_argument(
        "-f", "--file", type=str, default=None, help="Configuration file"
    )
    config_parser.add_argument(
        "-p",
        "--profile",
        type=str,
        default=None,
        help="Profile to load from the config file (defaults to AERO_PROFILE or 'default')",
    )

    args = parser.parse_args()

    log_level = getattr(logging, args.log.upper(), None)
    logging.basicConfig(level=log_level, handlers=[logging.StreamHandler()])

    # actions
    if args.command == "list":
        if args.id is not None:
            from aero_client.api import list_versions

            versions = list(list_versions(args.id))
            if len(versions) == 0:
                print("No versions available.")
            else:
                print(json.dumps(versions, indent=4))
        else:
            from aero_client.api import list_metadata

            for page in list_metadata(args.type):
                print(json.dumps(page, indent=4))
                try:
                    _ = input("Press enter to continue or CTRL-D to quit")
                except EOFError:
                    break

    # elif args.command == "get":
    #     try:
    #         file = get_file(
    #             ftype=args.type,
    #             id=args.id,
    #             version=args.version,
    #             output_path=args.output_path,
    #         )
    #         if not isinstance(file, bytes):
    #             print(file.head(5))
    #     except ClientError as e:
    #         print(e)

    elif args.command == "search":
        from aero_client.api import search_sources

        res = search_sources(args.query)

        if len(res) == 0:
            print("Search returned no results")
        else:
            print(json.dumps(res, indent=4))

    elif args.command == "create":
        # Optionally load defaults from a YAML file; explicit CLI flags win.
        cfg = {}
        if args.file is not None:
            import yaml

            with open(args.file) as fh:
                cfg = yaml.safe_load(fh) or {}

        def _pick(key, cli_val):
            return cli_val if cli_val is not None else cfg.get(key)

        name = _pick("name", args.name)
        url = _pick("url", args.url)
        collection_uuid = _pick("collection_uuid", args.collection_uuid)
        collection_url = _pick("collection_url", args.collection_url)
        endpoint_uuid = _pick("endpoint_uuid", args.endpoint_uuid)
        description = _pick("description", args.description)
        type_name = _pick("type", args.type)
        no_copy = args.no_copy or bool(cfg.get("no_copy"))
        timer = _pick("timer", args.timer)

        # kwargs: start from the file, then merge in any KEY=VALUE overrides.
        fn_kwargs = dict(cfg.get("kwargs", {}) or {})
        if args.kwargs:
            for pair in args.kwargs:
                k, _, v = pair.partition("=")
                fn_kwargs[k] = v

        from aero_client.utils import PolicyEnum

        policy_val = _pick("policy", args.policy)
        if policy_val is None:
            policy = PolicyEnum.INGESTION_EVENT
        elif isinstance(policy_val, int) or str(policy_val).lstrip("-").isdigit():
            policy = PolicyEnum(int(policy_val))
        else:
            try:
                policy = PolicyEnum[str(policy_val).strip().upper()]
            except KeyError:
                parser.error(
                    f"unknown policy {policy_val!r}; for a source use "
                    "INGESTION_EVENT (notify-driven) or INGESTION (timer-driven)"
                )
        # --verifier optional: when omitted (CLI and file), create_source
        # registers a raw-passthrough `stage` function (stores the file as-is).
        function_uuid = _pick("verifier", args.verifier) or cfg.get("function_uuid")

        from aero_client.api import create_source
        from aero_client.api import get_source_type
        from aero_client.api import source_id as _source_id

        # Adding a url to a type that already exists needs nothing else — no
        # collection, no endpoint, no function. Nor does a no-copy source, which
        # never pulls anything.
        adding_to_existing = type_name is not None and get_source_type(type_name)

        if adding_to_existing:
            required = {"type": type_name, "url": url}
        elif no_copy:
            required = {"name": name, "url": url}
        else:
            required = {
                "name": name,
                "url": url,
                "collection_url": collection_url,
                "collection_uuid": collection_uuid,
                "endpoint_uuid": endpoint_uuid,
            }

        missing = [k for k, v in required.items() if not v]
        if missing:
            parser.error(
                "create is missing required values "
                f"{missing}; provide them via CLI flags or --file"
            )

        if policy is PolicyEnum.INGESTION and timer is None:
            print(
                "No --timer given; the server's default of 86400s (24h) applies.",
                file=sys.stderr,
            )

        try:
            result = create_source(
                name=name,
                url=url,
                collection_uuid=collection_uuid,
                collection_url=collection_url,
                endpoint_uuid=endpoint_uuid,
                function_uuid=function_uuid,
                description=description,
                type_name=type_name,
                no_copy=no_copy,
                policy=policy,
                timer_delay=timer,
                kwargs=fn_kwargs,
            )
        except ValueError as e:
            parser.error(str(e))

        print(json.dumps(result, indent=4))

        source_id = _source_id(result)
        if source_id is not None:
            print(f"\nSource data id: {source_id}")
            if adding_to_existing:
                print(f"  - url added to existing type '{type_name}'")
                print("  - analysis flows on this id now also run for it")
            print("  - trigger an ingestion (notify webhook):")
            print(f"      POST <server>/data/{source_id}/notify")
            print("  - reference it as an analysis flow input_data entry:")
            print(f'      {{"<name>": {{"id": "{source_id}", "version": null}}}}')

    elif args.command == "types":
        from aero_client.api import delete_type_url
        from aero_client.api import get_source_type
        from aero_client.api import list_source_types

        if args.remove_url:
            if args.name is None:
                parser.error(
                    "--remove-url needs the name of the type to remove it from"
                )
            types = [delete_type_url(args.name, args.remove_url)]
        elif args.name:
            one = get_source_type(args.name)
            if one is None:
                parser.error(f"Source type '{args.name}' not found.")
            types = [one]
        else:
            types = list_source_types()

        if args.json:
            print(json.dumps(types, indent=4))
        elif not types:
            print("No source types registered.")
        else:
            for t in types:
                flag = " (no-copy)" if t.get("no_copy") else ""
                print(f"{t['name']}{flag}")
                print(f"  data id: {t['data_id']}")
                for u in t.get("urls", []):
                    # A pattern matches objects that were never registered
                    # individually, so it reads very differently from an exact url.
                    kind = (
                        "pattern"
                        if any(c in u["object_key"] for c in "*?[")
                        else "object "
                    )
                    print(f"    {kind}  {u['url']}")

                if not t.get("urls"):
                    # Notify resolves on the registered urls and falls back only
                    # to untyped sources, so there is now no way to reach this one.
                    print("    no urls: notify can no longer resolve this type")

    elif args.command == "delete-flows":
        from aero_client.api import delete_data_flows
        from aero_client.api import get_data_flows
        from aero_client.utils import PolicyEnum

        def _policy_name(value):
            try:
                return PolicyEnum(value).name
            except ValueError:
                return str(value)

        attached = get_data_flows(args.data_id)
        if not attached:
            print(f"No flows are attached to data {args.data_id}.")
            return

        if not args.yes:
            print(f"Flows attached to data {args.data_id}:")
            for f in attached:
                desc = f" {f['description']}" if f.get("description") else ""
                timer = " [has timer]" if f.get("timer_job_id") else ""
                print(
                    f"  {f['role']:<10} {f['flow_id']}  "
                    f"{_policy_name(f['policy']):<16}{desc}{timer}"
                )
            print(
                f"\nDeleting {len(attached)} flow(s) also removes their provenance "
                "records and cancels any Globus timer driving them.\n"
                "The data, its versions and any source type are kept."
            )
            if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted; nothing was deleted.")
                return

        deleted = delete_data_flows(args.data_id)

        if args.json:
            print(json.dumps(deleted, indent=4))
        else:
            for f in deleted:
                note = f", timer {f['timer']}" if f.get("timer") else ""
                print(
                    f"deleted {f['role']} flow {f['flow_id']} "
                    f"({f['provenance_deleted']} provenance record(s){note})"
                )
            print(f"\n{len(deleted)} flow(s) deleted.")

    elif args.command == "register":
        from aero_client.api import register_flow
        from aero_client.utils import PolicyEnum

        # Load the flow definition from YAML; explicit CLI flags override scalars.
        cfg = {}
        if args.file is not None:
            import yaml

            with open(args.file) as fh:
                cfg = yaml.safe_load(fh) or {}

        def _pick(key, cli_val):
            return cli_val if cli_val is not None else cfg.get(key)

        endpoint_uuid = _pick("endpoint_uuid", args.endpoint_uuid)
        function_uuid = _pick("function_uuid", args.function_uuid)
        description = _pick("description", args.description)
        input_data = cfg.get("input_data", {})
        output_data = cfg.get("output_data", {})

        # kwargs: start from the file, then merge in any KEY=VALUE CLI overrides.
        kwargs = dict(cfg.get("kwargs", {}) or {})
        if args.kwargs:
            for pair in args.kwargs:
                k, _, v = pair.partition("=")
                kwargs[k] = v

        # Policy: accept an enum name ("ANY") or an int; default to ANY so the
        # analysis reruns whenever an input gets a new version.
        policy_val = _pick("policy", args.policy)
        if policy_val is None:
            policy = PolicyEnum.ANY
        elif isinstance(policy_val, int) or str(policy_val).lstrip("-").isdigit():
            policy = PolicyEnum(int(policy_val))
        else:
            try:
                policy = PolicyEnum[str(policy_val).strip().upper()]
            except KeyError:
                parser.error(
                    f"unknown policy {policy_val!r}; expected one of "
                    f"{[p.name for p in PolicyEnum]} or an int"
                )

        # output_data is optional: an analysis that stores its own results (back
        # to the object store it read from, say) declares none, and AERO records
        # only that the run happened.
        required = {
            "endpoint_uuid": endpoint_uuid,
            "function_uuid": function_uuid,
            "input_data": input_data,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            parser.error(
                f"register is missing required values {missing}; "
                "provide them via --file or CLI flags"
            )

        result = register_flow(
            endpoint_uuid=endpoint_uuid,
            function_uuid=function_uuid,
            input_data=input_data,
            output_data=output_data,
            kwargs=kwargs,
            description=description,
            policy=policy,
        )
        print(json.dumps(result, indent=4))

        try:
            out_id = result["contributed_to"][0]["id"]
        except (KeyError, IndexError, TypeError):
            out_id = None
        if out_id is not None:
            print(f"\nOutput data id: {out_id}")
        elif not output_data:
            print(
                "\nNo output_data: this analysis stores its own results, so AERO "
                "records the run and its inputs but tracks no output version.\n"
                "Nothing can be registered downstream of it."
            )

    elif args.command == "configure":
        profile = args.profile or os.environ.get("AERO_PROFILE", "default")
        pprint(
            dataclasses.asdict(load_conf(args.file, update=True, profile=profile))
        )

    elif args.command == "logout":
        from aero_client.api import globus_logout

        # TODO: fix bug where user needs to be authenticated before logging out
        globus_logout()


if __name__ == "__main__":
    main()
