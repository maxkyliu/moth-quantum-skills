#!/usr/bin/env python3
"""Minimal Moth Quantum API client. Standard library only (Python 3.9+).

Reads the key from MOTH_API_KEY. Run `python moth.py -h` for commands.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("MOTH_API_URL", "https://api.mothquantum.com/api/v1")
TERMINAL = ("completed", "failed", "cancelled")
POLL_SECONDS = 3
BACKOFF_SECONDS = 5
MAX_RETRIES = 5
# The API's edge rejects urllib's default "Python-urllib" user agent (Cloudflare 1010).
USER_AGENT = "moth-quantum-skill/0.1"
# Rate limit, gateway errors, and Cloudflare's transient origin errors (520/522/524).
RETRY_STATUSES = (429, 502, 503, 520, 522, 524)
ASSET_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EXTRA_TYPES = {".mid": "audio/midi", ".midi": "audio/midi", ".wav": "audio/wav",
               ".flac": "audio/flac", ".qasm": "text/plain", ".json": "application/json"}


class MothError(RuntimeError):
    """An API error carrying the problem+json body."""

    def __init__(self, status: int, body: object):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {json.dumps(body, ensure_ascii=False)}")


def _key() -> str:
    key = os.environ.get("MOTH_API_KEY", "").strip()
    if not key:
        sys.exit("MOTH_API_KEY is not set. Create a key in the Moth dashboard and export it.")
    return key


def _http(method: str, url: str, *, body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def call(method: str, path: str, payload: dict | None = None) -> dict:
    """Call the API with auth, retrying on 429/502/503 with backoff."""
    headers = {"Authorization": f"Bearer {_key()}", "Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    for attempt in range(MAX_RETRIES):
        status, raw = _http(method, f"{API}{path}", body=body, headers=headers)
        if status in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
            continue
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"detail": raw[:500].decode(errors="replace")}
        if status >= 400:
            raise MothError(status, data)
        return data
    raise MothError(status, {"detail": "retries exhausted"})


def engine(engine_id: str) -> dict:
    return call("GET", f"/engines/{engine_id}")


def upload(path: Path, content_type: str | None = None) -> str:
    """Register, PUT and complete an upload; return the asset_id."""
    data = path.read_bytes()
    ctype = (content_type or EXTRA_TYPES.get(path.suffix.lower())
             or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    asset = call("POST", "/assets", {"filename": path.name, "content_type": ctype,
                                     "size_bytes": len(data)})
    status, raw = _http("PUT", asset["upload"]["url"], body=data,
                        headers=asset["upload"].get("headers") or {"Content-Type": ctype})
    if status >= 300:
        raise MothError(status, {"detail": "upload PUT rejected", "body": raw[:500].decode(errors="replace")})
    call("POST", f"/assets/{asset['asset_id']}/complete")
    return asset["asset_id"]


def output_asset(job_id: str, slot: str) -> str:
    """The asset id of a finished job's output slot (how outputs chain into later jobs)."""
    outputs = call("GET", f"/jobs/{job_id}").get("outputs") or []
    for out in outputs:
        if out.get("slot") == slot:
            return out["output_asset_id"]
    raise MothError(404, {"detail": f"job {job_id} has no output slot {slot!r}",
                          "slots": [o.get("slot") for o in outputs]})


def resolve_ref(value: str) -> str:
    """Turn `job:<job_id>/<slot>` into that output's asset id; pass anything else through.

    The API accepts only asset UUIDs in input_files, even where an engine page shows the
    `job:` form (verified 2026-10-14 on retrocausal-echo-v1 and qrc-gen-v2).
    """
    if not value.startswith("job:"):
        return value
    job_id, sep, slot = value[len("job:"):].partition("/")
    if not sep or not job_id or not slot:
        raise ValueError(f"expected job:<job_id>/<slot>, got {value!r}")
    return output_asset(job_id, slot)


def submit(engine_id: str, params: dict | None = None, input_files: dict | None = None) -> str:
    payload: dict = {"params": params or {}}
    if input_files:
        payload["input_files"] = {slot: resolve_ref(v) for slot, v in input_files.items()}
    return call("POST", f"/engines/{engine_id}/process", payload)["job_id"]


def wait(job_id: str, timeout: float | None = None, quiet: bool = False) -> dict:
    """Poll until the job reaches a terminal status."""
    started = time.monotonic()
    last = None
    while True:
        st = call("GET", f"/jobs/{job_id}/status")
        if st.get("status") != last and not quiet:
            print(f"[{job_id}] {st.get('status')}", file=sys.stderr)
            last = st.get("status")
        if st.get("status") in TERMINAL:
            return st
        if timeout and time.monotonic() - started > timeout:
            raise TimeoutError(f"job {job_id} still {st.get('status')} after {timeout}s")
        time.sleep(POLL_SECONDS)


def result(job_id: str, out_dir: Path | None = None) -> dict:
    """Fetch the result; download file outputs into out_dir when given."""
    res = call("GET", f"/jobs/{job_id}/result")
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        for out in res.get("outputs") or []:
            status, raw = _http("GET", out["url"])  # presigned: no auth header
            if status >= 300:
                raise MothError(status, {"detail": f"download failed for slot {out['slot']}"})
            target = out_dir / (out.get("filename") or out["slot"])
            target.write_bytes(raw)
            print(f"saved {out['slot']} -> {target}", file=sys.stderr)
        if res.get("result") is not None:
            (out_dir / f"{job_id}-result.json").write_text(
                json.dumps(res["result"], indent=2, ensure_ascii=False))
    return res


def _parse_files(specs: list[str], dry_run: bool) -> dict:
    """slot=path[@mime] | slot=<asset_id> | slot=job:<id>/<slot>."""
    files = {}
    for spec in specs:
        slot, sep, value = spec.partition("=")
        if not sep or not slot or not value:
            sys.exit(f"--file expects slot=value, got {spec!r}")
        path_str, _, mime = value.partition("@")
        path = Path(path_str)
        if value.startswith("job:") or ASSET_ID.fullmatch(value):
            files[slot] = value
        elif not path.is_file():
            sys.exit(f"--file {slot}: {path_str} is not a file, an asset id or a job:<id>/<slot> reference")
        elif dry_run:
            files[slot] = f"<upload {path}>"
        else:
            files[slot] = upload(path, mime or None)
            print(f"uploaded {path} -> {files[slot]}", file=sys.stderr)
    return files


def _check_slots(definition: dict, files: dict) -> None:
    declared = {s["name"] for s in definition.get("input_files") or []}
    unknown = set(files) - declared
    if unknown:
        sys.exit(f"unknown input slot(s) {sorted(unknown)}; engine declares {sorted(declared)}")
    missing = {s["name"] for s in definition.get("input_files") or []
               if s.get("required")} - set(files)
    if missing:
        sys.exit(f"missing required input slot(s): {sorted(missing)}")


def cmd_run(args: argparse.Namespace) -> None:
    params = json.loads(args.params) if args.params else {}
    definition = engine(args.engine_id)
    files_in = args.file or []
    _check_slots(definition, {s.partition("=")[0]: None for s in files_in})
    cost = definition.get("credits_per_run")
    print(f"{args.engine_id}: {cost} credit(s) per run, timeout "
          f"{(definition.get('run_policy') or {}).get('timeout')}s", file=sys.stderr)
    files = _parse_files(files_in, args.dry_run)
    if args.dry_run:
        print(json.dumps({"params": params, "input_files": files}, indent=2, ensure_ascii=False))
        return
    job_id = submit(args.engine_id, params, files or None)
    print(f"job_id {job_id}", file=sys.stderr)
    st = wait(job_id, timeout=args.timeout)
    if st["status"] != "completed":
        print(json.dumps(st, indent=2, ensure_ascii=False))
        sys.exit(1)
    res = result(job_id, Path(args.out) if args.out else None)
    print(json.dumps(res, indent=2, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description="Moth Quantum API helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("me", help="show the identity behind MOTH_API_KEY")
    sub.add_parser("engines", help="list visible engines")
    e = sub.add_parser("engine", help="show an engine definition")
    e.add_argument("engine_id")
    u = sub.add_parser("upload", help="upload a file, print its asset_id")
    u.add_argument("path")
    u.add_argument("--type", help="content type override")
    r = sub.add_parser("run", help="submit a job, wait, fetch the result")
    r.add_argument("engine_id")
    r.add_argument("--params", help="JSON object of engine params")
    r.add_argument("--file", action="append",
                   help="slot=path[@mime] | slot=asset_id | slot=job:<id>/<slot> (repeatable)")
    r.add_argument("--out", help="directory for downloaded outputs")
    r.add_argument("--timeout", type=float, help="give up waiting after N seconds")
    r.add_argument("--dry-run", action="store_true", help="show the request, spend nothing")
    for name in ("status", "result"):
        s = sub.add_parser(name)
        s.add_argument("job_id")
        if name == "result":
            s.add_argument("--out")
    args = p.parse_args()

    try:
        if args.cmd == "me":
            out = call("GET", "/me")
        elif args.cmd == "engines":
            out = [{"engine_id": x["engine_id"], "credits_per_run": x.get("credits_per_run"),
                    "visibility": x.get("visibility")} for x in call("GET", "/engines")["engines"]]
        elif args.cmd == "engine":
            d = engine(args.engine_id)
            out = {k: d.get(k) for k in ("engine_id", "name", "credits_per_run", "run_policy",
                                         "visibility", "params_schema", "input_files",
                                         "output_files", "error_codes")}
        elif args.cmd == "upload":
            out = {"asset_id": upload(Path(args.path), args.type)}
        elif args.cmd == "status":
            out = call("GET", f"/jobs/{args.job_id}/status")
        elif args.cmd == "result":
            out = result(args.job_id, Path(args.out) if args.out else None)
        else:
            cmd_run(args)
            return
    except MothError as err:
        print(json.dumps({"status": err.status, "error": err.body}, indent=2, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
