"""Long-running engine process for the VS Code extension.

Speaks newline-delimited JSON over stdin/stdout, one request per line:

    -> {"id": 1, "method": "open", "params": {"root": "/path/to/repo"}}
    <- {"id": 1, "result": {...report...}}
    -> {"id": 2, "method": "update", "params": {"paths": ["src/app.py"]}}
    <- {"id": 2, "result": {...report..., "changed": ["src/app.py"]}}

Methods: open, update, report, dot, shutdown. Errors come back as
{"id": n, "error": "message"}. Because the process stays alive, parse results
stay in memory between requests, which is what makes updates incremental.
"""

import json
import sys
import traceback

from stratum_engine.analyze import to_dot, to_folder_dot
from stratum_engine.session import Session


class Server:
    def __init__(self):
        self.session: Session | None = None

    def handle(self, method: str, params: dict):
        if method == "open":
            self.session = Session(params["root"])
            return self._with_timings(self.session.scan())
        if method == "shutdown":
            return {"ok": True}
        if self.session is None:
            raise RuntimeError("call 'open' before " + method)
        if method == "update":
            return self._with_timings(self.session.update(params.get("paths", [])))
        if method == "report":
            return self.session.report()
        if method == "dot":
            report = self.session.report()
            if params.get("mode") == "folders":
                return {"dot": to_folder_dot(report, int(params.get("depth", 2)))}
            return {"dot": to_dot(report, params.get("focus", ""))}
        raise ValueError(f"unknown method: {method}")

    def _with_timings(self, report: dict) -> dict:
        report["timings"] = self.session.last_timings
        return report


def serve(stdin=sys.stdin, stdout=sys.stdout):
    server = Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        req_id, method = None, None
        try:
            req = json.loads(line)
            req_id, method = req.get("id"), req["method"]
            result = server.handle(method, req.get("params") or {})
            reply = {"id": req_id, "result": result}
        except Exception as exc:  # report, keep serving
            reply = {"id": req_id, "error": f"{type(exc).__name__}: {exc}"}
            traceback.print_exc(file=sys.stderr)
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()
        if method == "shutdown":
            break
