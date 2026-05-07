"""Entry point for the Summer 2027 intern monitor.

Run order:
  1. Load firms.yaml — profiles + sources + firm list.
  2. Fetch SimplifyJobs and Northwestern in parallel (high-recall).
  3. Run ATS detection (cached) for each firm with a careers_url.
  4. Fetch Greenhouse/Lever/Ashby in parallel (max 5 concurrent).
  5. For each profile: filter by title, diff against profile state, notify
     profile webhook, save profile state. A posting matching multiple
     profiles is sent to all matching channels.

Flags:
  --dry-run          : hit real APIs, print would-notify, don't post or write state.
  --seed             : force seed behavior on every profile (single summary, no diff pings).
  --profile NAME     : only process this profile (default: all profiles in firms.yaml).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

import detect
import notify
from filters import FilterRules, title_passes
from sources import ashby, greenhouse, lever, northwestern, simplify
from sources.base import Posting

ROOT = Path(__file__).resolve().parent
UNSUPPORTED_LOG = ROOT / "unsupported.log"

log = logging.getLogger("q27bot")


# ---------- state ----------


def load_state(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.warning("state: %s corrupt, treating as empty", path)
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def diff(current: list[Posting], previous: dict) -> tuple[list[Posting], list[str], dict]:
    """Compare current postings to previous state.

    Returns (new_postings, gone_keys, updated_state).
    `gone` keeps records in state with last_seen stamped — never deleted.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current_by_key = {p.key(): p for p in current}
    new_state = dict(previous)

    new_postings: list[Posting] = []
    for key, p in current_by_key.items():
        if key not in previous:
            new_postings.append(p)
            new_state[key] = {**p.to_dict(), "first_seen": now, "last_seen": now}
        else:
            prev = previous[key]
            new_state[key] = {**prev, **p.to_dict(), "last_seen": now}

    gone: list[str] = []
    for key in previous:
        if key not in current_by_key:
            gone.append(key)
            entry = dict(previous[key])
            entry.setdefault("last_seen", entry.get("first_seen", now))
            new_state[key] = entry

    return new_postings, gone, new_state


# ---------- fetch ----------


def fetch_community(cfg: dict) -> list[Posting]:
    s_cfg = cfg["sources"]["simplify"]
    n_cfg = cfg["sources"]["northwestern"]

    out: list[Posting] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_simp = pool.submit(simplify.fetch, s_cfg["primary"], s_cfg["fallback"])
        f_nu = pool.submit(northwestern.fetch, n_cfg["api"])
        try:
            out.extend(simplify.parse(f_simp.result()))
        except Exception as e:
            log.exception("simplify fetch failed: %s", e)
        try:
            out.extend(northwestern.parse(f_nu.result()))
        except Exception as e:
            log.exception("northwestern fetch failed: %s", e)
    return out


def fetch_one_firm(firm: dict, cache: dict) -> tuple[str, list[Posting], Optional[str]]:
    name = firm["name"]
    careers = firm.get("careers_url")
    override_ats = firm.get("ats")
    override_slug = firm.get("slug")
    if not careers and not (override_ats and override_slug):
        return name, [], None
    try:
        det = detect.detect_with_cache(
            name, careers, cache, override_ats=override_ats, override_slug=override_slug
        )
    except Exception as e:
        return name, [], f"detect error: {e}"

    try:
        if det.ats == "greenhouse":
            jobs = greenhouse.fetch(det.slug)
            if jobs is None:
                return name, [], None
            return name, greenhouse.parse(name, jobs), None
        if det.ats == "lever":
            jobs = lever.fetch(det.slug)
            if jobs is None:
                return name, [], None
            return name, lever.parse(name, jobs), None
        if det.ats == "ashby":
            jobs = ashby.fetch(det.slug)
            if jobs is None:
                return name, [], None
            return name, ashby.parse(name, jobs), None
        if det.ats == "workday":
            return name, [], "workday: needs implementation"
        return name, [], "unsupported"
    except Exception as e:
        return name, [], f"fetch error: {e}"


def fetch_direct(cfg: dict) -> tuple[list[Posting], list[tuple[str, str]]]:
    cache = detect.load_cache()
    firms = cfg.get("firms") or []
    out: list[Posting] = []
    failures: list[tuple[str, str]] = []
    unsupported: list[str] = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(fetch_one_firm, f, cache): f["name"] for f in firms}
        for fut in as_completed(futs):
            name, postings, err = fut.result()
            if err:
                if "needs implementation" in err or err == "unsupported":
                    unsupported.append(f"{name}\t{err}")
                else:
                    failures.append((name, err))
            out.extend(postings)

    detect.save_cache(cache)
    if unsupported:
        UNSUPPORTED_LOG.write_text("\n".join(sorted(unsupported)) + "\n")
    return out, failures


# ---------- per-profile pipeline ----------


def run_profile(
    profile_name: str,
    profile_cfg: dict,
    deduped: dict[str, Posting],
    failure_count: int,
    *,
    dry_run: bool,
    force_seed: bool,
) -> tuple[int, int, int]:
    """Apply one profile's filter+diff+notify. Returns (matched, new, gone)."""
    rules = FilterRules.from_dict(profile_cfg["filters"])
    state_path = ROOT / profile_cfg["state_file"]
    webhook_env = profile_cfg["webhook_env"]
    webhook = os.environ.get(webhook_env, "")

    matched = [p for p in deduped.values() if title_passes(p.title, rules)]
    previous = load_state(state_path)
    is_first_run = not previous
    new_postings, gone, updated_state = diff(matched, previous)

    log.info(
        "profile=%s matched=%d new=%d gone=%d (state=%s)",
        profile_name, len(matched), len(new_postings), len(gone), state_path.name,
    )

    if not webhook and not dry_run:
        log.error("profile=%s: %s is unset — skipping notify (state still saved)",
                  profile_name, webhook_env)
    else:
        if force_seed or is_first_run:
            msg = (
                f"🎯 q27bot [{profile_name}] initialized — tracking "
                f"**{len(matched)}** Summer 2027 postings. Future runs will alert on new entries only."
            )
            notify.send_text(webhook, msg, dry_run=dry_run)
        elif new_postings:
            notify.send_postings(webhook, new_postings, dry_run=dry_run)

        if failure_count > 5:
            notify.send_text(
                webhook,
                f"⚠️ [{profile_name}] {failure_count} firms failed this run.",
                dry_run=dry_run,
            )

    if not dry_run:
        save_state(state_path, updated_state)

    return len(matched), len(new_postings), len(gone)


# ---------- main ----------


def run(*, dry_run: bool, seed: bool, only_profile: Optional[str]) -> int:
    cfg = yaml.safe_load((ROOT / "firms.yaml").read_text())
    profiles = cfg.get("profiles") or {}
    if not profiles:
        log.error("firms.yaml has no `profiles:` block")
        return 2

    if only_profile:
        if only_profile not in profiles:
            log.error("profile %r not in firms.yaml (have: %s)", only_profile, list(profiles))
            return 2
        profiles = {only_profile: profiles[only_profile]}

    log.info("fetching community sources…")
    community = fetch_community(cfg)
    log.info("community: %d raw postings", len(community))

    log.info("fetching direct firms…")
    direct, failures = fetch_direct(cfg)
    log.info("direct: %d raw postings, %d failures", len(direct), len(failures))

    raw = community + direct
    deduped: dict[str, Posting] = {}
    for p in raw:
        deduped.setdefault(p.key(), p)
    log.info("deduped pool: %d postings", len(deduped))

    summary: list[str] = []
    for name, pcfg in profiles.items():
        m, n, g = run_profile(
            name, pcfg, deduped, len(failures),
            dry_run=dry_run, force_seed=seed,
        )
        summary.append(f"{name}: matched={m} new={n} gone={g}")

    print(f"summary: " + " | ".join(summary) + f" | failures={len(failures)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Summer 2027 intern monitor (multi-profile)")
    parser.add_argument("--dry-run", action="store_true", help="don't post or write state")
    parser.add_argument("--seed", action="store_true", help="force seed behavior on all profiles")
    parser.add_argument("--profile", help="only run this profile (default: all)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    t0 = time.time()
    rc = run(dry_run=args.dry_run, seed=args.seed, only_profile=args.profile)
    log.info("done in %.1fs", time.time() - t0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
