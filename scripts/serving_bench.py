"""Serving benchmark: time to first token, decode speed, throughput and VRAM for Ollama endpoints.

Added November 2026, after the original study. It measures serving, not repair quality.
The prompts are the study's own first-attempt repair prompts for the 5 dev tasks
(routing_pilot.prompts.build_messages), so the load looks like the real workload.

    python scripts/serving_bench.py --target native=http://localhost:11434 \
        --target docker=http://localhost:11435 --campaign serving_bench_v1

Run one target at a time on an 8 GB GPU, or make sure the other server has no model loaded:
the script unloads each model before and after measuring it, but a model held by another
server would still take VRAM. Standard library only (urllib, threads); VRAM is read with
nvidia-smi (total GPU memory in use) and Ollama's /api/ps (memory held by the model).

Outputs in results/<campaign>/: requests.csv (one row per request), batches.csv (one row per
concurrent batch), summary.csv, summary.md, throughput.svg and environment.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing_pilot.prompts import build_messages  # noqa: E402
from routing_pilot.tasks import list_tasks, load_public  # noqa: E402

DEFAULT_MODELS = ["qwen2.5-coder:1.5b", "qwen2.5-coder:7b"]


# ---------------------------------------------------------------- HTTP helpers

def _post(url: str, body: dict, timeout: float = 600):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url: str, timeout: float = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def unload(base: str, model: str) -> None:
    """keep_alive=0 with no prompt unloads the model from memory."""
    with _post(f"{base}/api/generate", {"model": model, "keep_alive": 0}) as r:
        r.read()
    for _ in range(50):                       # unloading is asynchronous; wait until /api/ps drops it
        if not any(m.get("name") == model for m in _get_json(f"{base}/api/ps").get("models", [])):
            return
        time.sleep(0.2)


def chat_stream(base: str, model: str, messages: list[dict], options: dict) -> dict:
    """One streamed /api/chat request. Client-side TTFT and wall time, server-side token counts."""
    body = {"model": model, "messages": messages, "stream": True, "options": options, "keep_alive": "10m"}
    t0 = time.perf_counter()
    ttft, final, n_chunks = None, {}, 0
    with _post(f"{base}/api/chat", body) as r:
        for line in r:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                raise RuntimeError(chunk["error"])
            if ttft is None and chunk.get("message", {}).get("content"):
                ttft = time.perf_counter() - t0
            n_chunks += 1
            if chunk.get("done"):
                final = chunk
    wall = time.perf_counter() - t0
    ns = 1e9
    eval_count = final.get("eval_count") or 0
    eval_s = (final.get("eval_duration") or 0) / ns
    prompt_count = final.get("prompt_eval_count") or 0
    prompt_s = (final.get("prompt_eval_duration") or 0) / ns
    return {
        "ttft_s": ttft, "wall_s": wall, "prompt_tokens": prompt_count, "output_tokens": eval_count,
        "decode_tok_s": eval_count / eval_s if eval_s else None,
        "prefill_tok_s": prompt_count / prompt_s if prompt_s else None,
        "client_decode_tok_s": _client_decode(eval_count, wall, ttft),
        "load_s": (final.get("load_duration") or 0) / ns, "done_reason": final.get("done_reason"),
    }


def _client_decode(out_tokens: int, wall: float, ttft: float | None) -> float | None:
    """Output tokens after the first, per second after the first token (same method for every server)."""
    if not ttft or out_tokens < 2 or wall <= ttft:
        return None
    return (out_tokens - 1) / (wall - ttft)


def chat_stream_openai(base: str, model: str, messages: list[dict], options: dict) -> dict:
    """One streamed /v1/chat/completions request (vLLM, or Ollama's OpenAI-compatible endpoint).

    Token counts come from the final usage chunk; there are no server-side timings, so decode speed
    is measured by the client only.
    """
    body = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True},
            "temperature": options["temperature"], "seed": options["seed"], "max_tokens": options["num_predict"]}
    t0 = time.perf_counter()
    ttft, usage, finish = None, {}, None
    with _post(f"{base}/v1/chat/completions", body) as r:
        for line in r:
            line = line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if "error" in chunk:
                raise RuntimeError(chunk["error"])
            for ch in chunk.get("choices") or []:
                if ttft is None and (ch.get("delta") or {}).get("content"):
                    ttft = time.perf_counter() - t0
                finish = ch.get("finish_reason") or finish
            if chunk.get("usage"):
                usage = chunk["usage"]
    wall = time.perf_counter() - t0
    out = usage.get("completion_tokens") or 0
    cd = _client_decode(out, wall, ttft)
    return {"ttft_s": ttft, "wall_s": wall, "prompt_tokens": usage.get("prompt_tokens") or 0, "output_tokens": out,
            "decode_tok_s": cd, "prefill_tok_s": None, "client_decode_tok_s": cd, "load_s": 0.0,
            "done_reason": finish}


def loopback_ip(url: str) -> str:
    """Use 127.0.0.1 instead of "localhost".

    On Windows, urllib tries IPv6 ::1 first; Ollama and Docker's port mapping listen on IPv4 only,
    so every new connection to "localhost" waited about 2 s before falling back (measured:
    2.04 s vs 0.002 s for /api/version). This script opens one connection per request, so that
    delay would be counted as time to first token.
    """
    if "://localhost" in url:
        new = url.replace("://localhost", "://127.0.0.1", 1)
        print(f"note: using {new} instead of {url} (avoids a ~2 s IPv6 fallback per connection on Windows)")
        return new
    return url


# ---------------------------------------------------------------- VRAM

def gpu_sample() -> tuple[int | None, float | None]:
    """(GPU memory in use in MiB, GPU board power draw in W) from nvidia-smi."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,power.draw", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        mem, power = out.strip().splitlines()[0].split(",")
        try:
            return int(mem), float(power)
        except ValueError:                    # power.draw can be "[N/A]"
            return int(mem), None
    except Exception:
        return None, None


def idle_gpu_sample(seconds: float = 8.0, interval_s: float = 0.5) -> tuple[int | None, float | None]:
    """Idle memory and power: the minimum over a few seconds, since the GPU stays clocked up for a
    while after the previous model unloads (a single sample read 60-70 W instead of about 5 W)."""
    mems, powers = [], []
    for _ in range(int(seconds / interval_s)):
        m, w = gpu_sample()
        if m is not None:
            mems.append(m)
        if w is not None:
            powers.append(w)
        time.sleep(interval_s)
    return (min(mems) if mems else None), (min(powers) if powers else None)


def gpu_memory_used_mib() -> int | None:
    return gpu_sample()[0]


class VramSampler:
    """Polls nvidia-smi in a background thread: peak memory, mean and peak GPU power."""

    def __init__(self, interval_s: float = 0.25):
        self.interval_s, self.peak, self._stop = interval_s, None, threading.Event()
        self.power: list[float] = []
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            v, w = gpu_sample()
            if w is not None:
                self.power.append(w)
            if v is not None:
                self.peak = v if self.peak is None else max(self.peak, v)
            self._stop.wait(self.interval_s)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()


# ---------------------------------------------------------------- benchmark

def build_prompts(split: str = "dev") -> list[tuple[str, list[dict]]]:
    data = ROOT / "data"
    return [(t, build_messages(load_public(data, t))) for t in list_tasks(data, split)]


def bench_model(target: str, base: str, model: str, prompts, concurrency: list[int], rounds: int,
                options: dict, sample_vram: bool = True, kind: str = "ollama") -> tuple[list[dict], list[dict], dict]:
    """kind "ollama": native /api/chat, model unloaded before and after, memory from /api/ps.
    kind "openai": /v1/chat/completions (vLLM or Ollama); the server keeps its model loaded, so the
    idle reading includes it (vLLM also pre-allocates its KV cache at start-up)."""
    requests, batches = [], []
    chat = chat_stream if kind == "ollama" else chat_stream_openai
    if kind == "ollama":
        unload(base, model)
    baseline, idle_w = idle_gpu_sample() if sample_vram else (None, None)
    # Cold load + warm-up (not measured as a request): loads weights and fills caches.
    cold = chat(base, model, prompts[0][1], options)
    ps = {}
    if kind == "ollama":
        ps = {m["name"]: m for m in _get_json(f"{base}/api/ps").get("models", [])}.get(model, {})
    sampler = VramSampler() if sample_vram else None
    if sampler:
        sampler.__enter__()
    try:
        for c in concurrency:
            for rnd in range(rounds):
                # Each batch sends c different prompts at once, rotating through the tasks.
                batch = [prompts[(rnd * c + i) % len(prompts)] for i in range(c)]
                t0 = time.perf_counter()
                with ThreadPoolExecutor(max_workers=c) as pool:
                    results = list(pool.map(lambda p: (p[0], chat(base, model, p[1], options)), batch))
                wall = time.perf_counter() - t0
                out_tokens = sum(r["output_tokens"] for _, r in results)
                in_tokens = sum(r["prompt_tokens"] for _, r in results)
                batches.append({"target": target, "model": model, "concurrency": c, "round": rnd,
                                "batch_wall_s": round(wall, 4), "batch_prompt_tokens": in_tokens,
                                "batch_output_tokens": out_tokens,
                                "aggregate_tok_s": round(out_tokens / wall, 2) if wall else None,
                                "aggregate_total_tok_s": round((in_tokens + out_tokens) / wall, 2) if wall else None})
                for task, r in results:
                    requests.append({"target": target, "model": model, "concurrency": c, "round": rnd, "task": task,
                                     **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}})
    finally:
        if sampler:
            sampler.__exit__(None, None, None)
    if kind == "ollama":
        unload(base, model)
    power = sampler.power if sampler else []
    vram = {"gpu_used_baseline_mib": baseline, "gpu_used_peak_mib": sampler.peak if sampler else None,
            "gpu_power_idle_w": idle_w,
            "gpu_power_mean_under_load_w": round(statistics.mean(power), 1) if power else None,
            "gpu_power_peak_w": max(power) if power else None,
            "ollama_size_vram_mib": round(ps.get("size_vram", 0) / 2**20) if ps else None,
            "ollama_size_total_mib": round(ps.get("size", 0) / 2**20) if ps else None,
            "context_length": ps.get("context_length"), "cold_load_s": round(cold["load_s"], 3) if kind == "ollama" else None,
            "cold_ttft_s": round(cold["ttft_s"], 3) if cold["ttft_s"] else None}
    return requests, batches, vram


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def summarise(requests: list[dict], batches: list[dict], vram: dict[tuple, dict]) -> list[dict]:
    rows = []
    keys = sorted({(r["target"], r["model"], r["concurrency"]) for r in requests},
                  key=lambda k: (k[0], k[1], k[2]))
    for target, model, c in keys:
        rq = [r for r in requests if (r["target"], r["model"], r["concurrency"]) == (target, model, c)]
        bt = [b for b in batches if (b["target"], b["model"], b["concurrency"]) == (target, model, c)]
        v = vram.get((target, model), {})
        peak, base = v.get("gpu_used_peak_mib"), v.get("gpu_used_baseline_mib")
        rows.append({
            "target": target, "model": model, "concurrency": c, "n_requests": len(rq),
            "median_ttft_s": _median([r["ttft_s"] for r in rq]),
            "median_decode_tok_s_per_request": _median([r["decode_tok_s"] for r in rq]),
            "median_client_decode_tok_s": _median([r.get("client_decode_tok_s") for r in rq]),
            "median_prefill_tok_s": _median([r["prefill_tok_s"] for r in rq]),
            "median_aggregate_tok_s": _median([b["aggregate_tok_s"] for b in bt]),
            "median_aggregate_total_tok_s": _median([b["aggregate_total_tok_s"] for b in bt]),
            "median_output_tokens": _median([r["output_tokens"] for r in rq]),
            "median_request_wall_s": _median([r["wall_s"] for r in rq]),
            "ollama_model_vram_mib": v.get("ollama_size_vram_mib"),
            "gpu_peak_mib": peak, "gpu_peak_minus_baseline_mib": (peak - base) if peak and base is not None else None,
        })
    return rows


def throughput_svg(rows: list[dict]) -> str:
    """Aggregate output tokens/s vs concurrency, one line per target x model."""
    W, H, L, R, T, B = 640, 380, 60, 200, 30, 50
    series = {}
    for r in rows:
        if r["median_aggregate_tok_s"] is not None:
            series.setdefault(f'{r["target"]} {r["model"]}', []).append((r["concurrency"], r["median_aggregate_tok_s"]))
    cs = sorted({r["concurrency"] for r in rows}) or [1]
    ymax = max([y for s in series.values() for _, y in s] or [1]) * 1.1
    x = lambda c: L + (cs.index(c) / max(len(cs) - 1, 1)) * (W - L - R)
    y = lambda v: T + (1 - v / ymax) * (H - T - B)
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    dashes = {"native": "", "docker": "6,3", "ollama": "6,3", "vllm": ""}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="11">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{L}" y="18" font-size="13">Aggregate decode throughput vs concurrent requests</text>',
           f'<line x1="{L}" y1="{H - B}" x2="{W - R}" y2="{H - B}" stroke="black"/>',
           f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" stroke="black"/>',
           f'<text x="{(L + W - R) / 2}" y="{H - 12}" text-anchor="middle">concurrent requests</text>',
           f'<text x="16" y="{(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 16 {(T + H - B) / 2})">output tokens/s (all requests)</text>']
    for c in cs:
        out.append(f'<text x="{x(c):.1f}" y="{H - B + 16}" text-anchor="middle">{c}</text>')
    for i in range(5):
        v = ymax * i / 4
        out.append(f'<line x1="{L}" y1="{y(v):.1f}" x2="{W - R}" y2="{y(v):.1f}" stroke="#ddd"/>')
        out.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.0f}</text>')
    for i, (name, pts) in enumerate(sorted(series.items())):
        col = colors[i % len(colors)]
        dash = dashes.get(name.split()[0], "")
        pts = sorted(pts)
        path = " ".join(f"{x(c):.1f},{y(v):.1f}" for c, v in pts)
        out.append(f'<polyline points="{path}" fill="none" stroke="{col}" stroke-width="2" stroke-dasharray="{dash}"/>')
        for c, v in pts:
            out.append(f'<circle cx="{x(c):.1f}" cy="{y(v):.1f}" r="3" fill="{col}"/>')
        ly = T + 10 + i * 18
        out.append(f'<line x1="{W - R + 12}" y1="{ly}" x2="{W - R + 36}" y2="{ly}" stroke="{col}" stroke-width="2" stroke-dasharray="{dash}"/>')
        out.append(f'<text x="{W - R + 42}" y="{ly + 4}">{name}</text>')
    out.append("</svg>")
    return "\n".join(out)


def environment(targets: dict[str, str], models: list[str]) -> dict:
    env = {"timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "targets": {}}
    try:
        q = "--query-gpu=name,memory.total,driver_version"
        env["gpu"] = subprocess.run(["nvidia-smi", q, "--format=csv,noheader"], capture_output=True,
                                    text=True, timeout=10).stdout.strip()
    except Exception as exc:
        env["gpu"] = f"unavailable: {exc}"
    for name, (kind, base) in targets.items():
        if kind == "ollama":
            t = {"kind": kind, "url": base, "server": "Ollama " + str(_get_json(f"{base}/api/version").get("version")),
                 "models": {}}
            for m in models:
                with _post(f"{base}/api/show", {"model": m}) as r:
                    d = json.loads(r.read()).get("details", {})
                t["models"][m] = {k: d.get(k) for k in ("parameter_size", "quantization_level", "format", "family")}
        else:
            try:
                server = "vLLM " + str(_get_json(f"{base}/version").get("version"))
            except Exception:
                server = "OpenAI-compatible server"
            served = {m["id"]: m for m in _get_json(f"{base}/v1/models").get("data", [])}
            t = {"kind": kind, "url": base, "server": server,
                 "models": {m: {"served": m in served, "root": (served.get(m) or {}).get("root"),
                                "max_model_len": (served.get(m) or {}).get("max_model_len")} for m in models}}
        env["targets"][name] = t
    return env


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def summary_md(rows: list[dict], env: dict, args) -> str:
    lines = [f"# Serving benchmark: {args.campaign}", "",
             "Added November 2026, after the original study. Serving speed only, not repair quality.", "",
             f"- When (UTC): {env['timestamp_utc']}", f"- GPU: {env.get('gpu')}"]
    for name, t in env["targets"].items():
        if t["kind"] == "ollama":
            q = ", ".join(f"{m} {d['parameter_size']} {d['quantization_level']}" for m, d in t["models"].items())
        else:
            q = ", ".join(f"{m} = {d['root']}" for m, d in t["models"].items())
        lines.append(f"- {name}: {t['server']} at {t['url']}, {t['kind']} API ({q})")
    lines += [f"- Prompts: first-attempt repair prompts for the {args.n_prompts} dev tasks; "
              f"temperature {args.temperature}, num_predict {args.num_predict}, num_ctx {args.num_ctx}, "
              f"{args.rounds} batches per concurrency level", "",
              "| Target | Model | Concurrent | Requests | TTFT (median) | Decode tok/s per request | "
              "Client decode tok/s | Aggregate output tok/s | Aggregate total tok/s | Model VRAM (Ollama) | "
              "GPU peak (nvidia-smi) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        fmt = lambda v, s="": "n/a" if v is None else f"{v}{s}"
        lines.append(f"| {r['target']} | {r['model']} | {r['concurrency']} | {r['n_requests']} | "
                     f"{fmt(r['median_ttft_s'], ' s')} | {fmt(r['median_decode_tok_s_per_request'])} | "
                     f"{fmt(r['median_client_decode_tok_s'])} | "
                     f"{fmt(r['median_aggregate_tok_s'])} | {fmt(r['median_aggregate_total_tok_s'])} | "
                     f"{fmt(r['ollama_model_vram_mib'], ' MiB')} | "
                     f"{fmt(r['gpu_peak_mib'], ' MiB')} |")
    lines += ["", "TTFT is measured by the client (request sent to first streamed token). Decode tok/s is "
              "Ollama's eval_count / eval_duration where available (native API), otherwise the client value. "
              "Client decode tok/s is (output tokens - 1) / (wall time - TTFT), the same method for every server. Aggregate tok/s is all output tokens in a concurrent batch "
              "divided by the batch wall time (the total column also counts prompt tokens, which is what "
              "per-token APIs bill). GPU peak is total GPU memory in use, including the desktop.",
              "", "![Throughput](throughput.svg)", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", action="append", required=True,
                    help="name=url (Ollama native API), or name=openai+url for an OpenAI-compatible server such as "
                         "vLLM, e.g. native=http://localhost:11434 or vllm=openai+http://localhost:8000")
    ap.add_argument("--model", action="append", help=f"default: {' '.join(DEFAULT_MODELS)}")
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4])
    ap.add_argument("--rounds", type=int, default=10, help="concurrent batches per concurrency level")
    ap.add_argument("--num-predict", type=int, default=256)
    ap.add_argument("--num-ctx", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--campaign", default="serving_bench_v1")
    ap.add_argument("--results-dir", default=str(ROOT / "results"))
    ap.add_argument("--no-vram", action="store_true", help="skip nvidia-smi sampling")
    ap.add_argument("--plot", nargs="+", metavar=("OUT_SVG", "SUMMARY_CSV"),
                    help="only redraw: one throughput plot from one or more existing summary.csv files")
    if argv is None and "--plot" in sys.argv:
        argv = sys.argv[1:] + ["--target", "unused=x"]
    args = ap.parse_args(argv)
    if args.plot:
        out_svg, *csvs = args.plot
        rows = []
        for c in csvs:
            for r in csv.DictReader(open(c, encoding="utf-8")):
                v = r.get("median_aggregate_tok_s")
                rows.append({"target": r["target"], "model": r["model"], "concurrency": int(r["concurrency"]),
                             "median_aggregate_tok_s": float(v) if v else None})
        Path(out_svg).write_text(throughput_svg(rows) + "\n", encoding="utf-8")
        print(f"wrote {out_svg}")
        return

    targets = {}
    for t in args.target:
        name, url = t.split("=", 1)
        kind = "ollama"
        if url.startswith("openai+"):
            kind, url = "openai", url[len("openai+"):]
        targets[name] = (kind, loopback_ip(url.rstrip("/")))
    models = args.model or DEFAULT_MODELS
    prompts = build_prompts()
    args.n_prompts = len(prompts)
    options = {"temperature": args.temperature, "seed": 0, "num_predict": args.num_predict, "num_ctx": args.num_ctx}

    out = Path(args.results_dir) / args.campaign
    out.mkdir(parents=True, exist_ok=True)
    env = environment(targets, models)
    requests, batches, vram = [], [], {}
    for name, (kind, base) in targets.items():
        for m in models:
            print(f"[{name}] {m} ...", flush=True)
            rq, bt, v = bench_model(name, base, m, prompts, args.concurrency, args.rounds, options,
                                    sample_vram=not args.no_vram, kind=kind)
            requests += rq
            batches += bt
            vram[(name, m)] = v
            print(f"[{name}] {m} done: {len(rq)} requests, model VRAM {v['ollama_size_vram_mib']} MiB", flush=True)
    rows = summarise(requests, batches, vram)
    env["vram"] = {f"{t} {m}": v for (t, m), v in vram.items()}
    env["options"] = options
    write_csv(out / "requests.csv", requests)
    write_csv(out / "batches.csv", batches)
    write_csv(out / "summary.csv", rows)
    (out / "environment.json").write_text(json.dumps(env, indent=2) + "\n", encoding="utf-8")
    (out / "throughput.svg").write_text(throughput_svg(rows) + "\n", encoding="utf-8")
    md = summary_md(rows, env, args)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
