"""The serving benchmark script, run end to end against a stub Ollama server (no model, no GPU)."""
import csv
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from routing_pilot.config import ROOT

spec = importlib.util.spec_from_file_location("serving_bench", ROOT / "scripts" / "serving_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class StubOllama(BaseHTTPRequestHandler):
    loaded: set = set()

    def log_message(self, *a):
        pass

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/version":
            self._json({"version": "stub"})
        elif self.path == "/api/ps":
            self._json({"models": [{"name": m, "size": 2**30, "size_vram": 2**30, "context_length": 4096}
                                   for m in self.loaded]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/show":
            self._json({"details": {"parameter_size": "1.5B", "quantization_level": "Q4_K_M"}})
        elif self.path == "/api/generate":            # keep_alive=0: unload
            self.loaded.discard(body["model"])
            self._json({"done": True})
        elif self.path == "/api/chat":
            self.loaded.add(body["model"])
            self.send_response(200)
            self.end_headers()
            for tok in ["def", " f", "():"]:
                self.wfile.write((json.dumps({"message": {"content": tok}, "done": False}) + "\n").encode())
            self.wfile.write((json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop",
                                          "eval_count": 3, "eval_duration": 30_000_000, "prompt_eval_count": 100,
                                          "prompt_eval_duration": 10_000_000, "load_duration": 0}) + "\n").encode())


def test_serving_bench_end_to_end_against_stub(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), StubOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        bench.main(["--target", f"stub={url}", "--model", "m", "--concurrency", "1", "2",
                    "--rounds", "2", "--no-vram", "--campaign", "c", "--results-dir", str(tmp_path)])
    finally:
        srv.shutdown()
    out = tmp_path / "c"
    rows = list(csv.DictReader((out / "summary.csv").open()))
    assert [(r["concurrency"], r["n_requests"]) for r in rows] == [("1", "2"), ("2", "4")]
    assert float(rows[0]["median_decode_tok_s_per_request"]) == 100.0     # 3 tokens / 0.03 s
    assert int(rows[0]["ollama_model_vram_mib"]) == 1024
    reqs = list(csv.DictReader((out / "requests.csv").open()))
    assert all(float(r["ttft_s"]) > 0 for r in reqs)
    assert "<svg" in (out / "throughput.svg").read_text()
    env = json.loads((out / "environment.json").read_text())
    assert env["targets"]["stub"]["models"]["m"]["quantization_level"] == "Q4_K_M"
