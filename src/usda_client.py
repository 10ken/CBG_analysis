"""USDA FoodData Central client with caching for meal macro enrichment.

Pulls per-100g nutrients for normalized food names so meal items gain
calorie/macro estimates used in the glucose analysis pipeline.
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher

import pandas as pd
import requests

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback when tqdm is unavailable
    def tqdm(iterable=None, **kwargs):
        return iterable if iterable is not None else []

from . import units

USDA_SEARCH_ENDPOINT = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_DETAILS_ENDPOINT = "https://api.nal.usda.gov/fdc/v1/foods"
logger = logging.getLogger("cbg_pipeline")

THROTTLE_DEFAULTS = {
    "enabled": True,
    "min_seconds": 1,
    "max_seconds": 5,
    "min_decimals": 1,
    "max_decimals": 10,
    "batch_size": None,
    "batch_pause_seconds": None,
}

_THROTTLE_CALL_COUNT = 0


def _load_api_key(api_keys_path: Path) -> str:
    with open(api_keys_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    key = data.get("USDA_food_data")
    if not key:
        raise ValueError("USDA_food_data key missing in api_keys.json")
    return key


def throttle(config: Optional[Dict] = None) -> None:
    global _THROTTLE_CALL_COUNT
    cfg = {**THROTTLE_DEFAULTS, **(config or {})}
    if not cfg.get("enabled", True):
        return

    batch_size = cfg.get("batch_size")
    batch_pause = cfg.get("batch_pause_seconds")

    # If batch throttling is configured, sleep only after every Nth call
    if batch_size and batch_pause is not None:
        _THROTTLE_CALL_COUNT += 1
        if _THROTTLE_CALL_COUNT % batch_size == 0:
            time.sleep(batch_pause)
        return

    base = random.uniform(cfg["min_seconds"], cfg["max_seconds"])
    decimals = random.randint(cfg["min_decimals"], cfg["max_decimals"])
    sleep_time = round(base, decimals)
    time.sleep(sleep_time)


def _load_cache(cache_path: Path) -> pd.DataFrame:
    base_cols = [
        "food_name_std",
        "fdcId",
        "usda_description",
        "calories_kcal_100g",
        "carbs_g_100g",
        "protein_g_100g",
        "fat_g_100g",
        "match_source",
        "usda_similarity_pct",
        "dataType",
        "query_used",
    ]
    if cache_path.exists():
        logger.info("Loading USDA cache from %s", cache_path)
        try:
            cache = pd.read_parquet(cache_path, engine="fastparquet")
        except Exception:
            cache = pd.read_parquet(cache_path, engine="pyarrow")
        for col in base_cols:
            if col not in cache.columns:
                cache[col] = None
        logger.info("Loaded USDA cache: %d rows", len(cache))
        return cache[base_cols]
    return pd.DataFrame(columns=base_cols)


def _save_cache(cache: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = cache.copy()
    if "fdcId" in cache.columns:
        cache["fdcId"] = cache["fdcId"].apply(lambda v: None if pd.isna(v) else str(v))
    cache.sort_values("food_name_std", inplace=True)
    try:
        cache.to_parquet(cache_path, index=False, engine="fastparquet")
    except ValueError:
        cache.to_parquet(cache_path, index=False, engine="pyarrow")
    logger.info("Saved USDA cache to %s (%d rows)", cache_path, len(cache))


def _stored_info_path(path: Optional[Path]) -> Path:
    if path is not None:
        return path
    return Path("data") / "cache_data" / "stored_fdc_id_info.csv"


def _load_stored_info(path: Optional[Path]) -> pd.DataFrame:
    cols = [
        "fdcId",
        "description",
        "dataType",
        "query_used",
        "similarity_pct",
        "nutrients_full_json",
        "energy_per_100g",
        "protein_per_100g",
        "carbs_per_100g",
        "fat_per_100g",
        "retrieval_timestamp",
    ]
    resolved = _stored_info_path(path)
    if not resolved.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(resolved)
    for col in cols:
        if col not in df.columns:
            df[col] = None
    return df[cols]


def _save_stored_info(df: pd.DataFrame, path: Optional[Path]) -> None:
    resolved = _stored_info_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    info = df.copy()
    if "fdcId" in info.columns:
        info["fdcId"] = info["fdcId"].apply(lambda v: None if pd.isna(v) else str(v))
    info.drop_duplicates(subset=["query_used"], keep="last", inplace=True)
    info.drop_duplicates(subset=["fdcId"], keep="last", inplace=True)
    info.to_csv(resolved, index=False)
    logger.info("Saved stored USDA info to %s (%d rows)", resolved, len(info))


def _extract_nutrients(food: Dict) -> Dict:
    nutrients = {n.get("nutrientName"): n.get("value") for n in food.get("foodNutrients", [])}
    return {
        "calories_kcal_100g": nutrients.get("Energy"),
        "carbs_g_100g": nutrients.get("Carbohydrate, by difference"),
        "protein_g_100g": nutrients.get("Protein"),
        "fat_g_100g": nutrients.get("Total lipid (fat)"),
    }


def usda_search_candidates(query: str, api_key: str, page_size: int = 5, throttle_config: Optional[Dict] = None) -> List[Dict]:
    """Run a USDA search for a single query string with retry on 500; log 400 but do not mutate the query."""
    throttle(throttle_config)
    params = {
        "query": query,
        "pageSize": page_size,
        "dataType": ["Foundation", "SR Legacy"],
        "api_key": api_key,
    }

    resp = requests.get(USDA_SEARCH_ENDPOINT, params=params, timeout=20)
    if resp.status_code == 500:
        for delay in (10, 20):
            logger.warning("USDA 500 for query '%s'; retrying after %ss", query, delay)
            time.sleep(delay)
            resp = requests.get(USDA_SEARCH_ENDPOINT, params=params, timeout=20)
            if resp.status_code != 500:
                break
    if resp.status_code == 500:
        logger.warning("USDA 500 persisted for query '%s'; skipping", query)
        return []
    if resp.status_code == 400:
        logger.warning("USDA 400 for query '%s' (raw='%s')", query, query)
        return []
    if resp.status_code != 200:
        logger.warning("USDA API non-200 (%s) for %s", resp.status_code, query)
        return []
    payload = resp.json()
    return payload.get("foods", []) or []


def fdc_get_foods_batch(api_key: str, fdc_ids: List[str], throttle_config: Optional[Dict] = None) -> List[Dict]:
    if not fdc_ids:
        return []
    throttle(throttle_config)
    payload = {"fdcIds": fdc_ids, "format": "full"}
    resp = requests.post(USDA_DETAILS_ENDPOINT, params={"api_key": api_key}, json=payload, timeout=25)
    if resp.status_code == 500:
        for delay in (10, 20):
            logger.warning("USDA detail 500 for ids %s; retrying after %ss", fdc_ids, delay)
            time.sleep(delay)
            resp = requests.post(USDA_DETAILS_ENDPOINT, params={"api_key": api_key}, json=payload, timeout=25)
            if resp.status_code != 500:
                break
    if resp.status_code != 200:
        logger.warning("USDA detail non-200 (%s) for ids %s", resp.status_code, fdc_ids)
        return []
    data = resp.json()
    if isinstance(data, dict):
        return data.get("foods", []) or []
    return data or []


def pick_best_candidate(query: str, candidates: List[Dict]) -> Tuple[Optional[Dict], float]:
    best = None
    best_sim = 0.0
    for cand in candidates:
        desc = (cand.get("description") or "").lower()
        sim = SequenceMatcher(None, query.lower(), desc).ratio() * 100
        if sim > best_sim:
            best_sim = sim
            best = cand
    if best_sim < 25:
        return None, best_sim
    return best, best_sim


def _merge_lookup(items: pd.DataFrame, lookup: pd.DataFrame, left_key: str, right_key: str, source_label: str) -> pd.DataFrame:
    if lookup.empty:
        return items
    cols = [
        "fdcId",
        "description",
        "dataType",
        "similarity_pct",
        "energy_per_100g",
        "protein_per_100g",
        "carbs_per_100g",
        "fat_per_100g",
        "query_used",
    ]
    if not set(cols).issubset(lookup.columns):
        return items

    renamed = lookup[cols].rename(
        columns={
            "description": "usda_description",
            "similarity_pct": "usda_similarity_pct",
            "energy_per_100g": "calories_kcal_100g",
            "protein_per_100g": "protein_g_100g",
            "carbs_per_100g": "carbs_g_100g",
            "fat_per_100g": "fat_g_100g",
        }
    )

    merged = items.merge(renamed, how="left", left_on=left_key, right_on=right_key, suffixes=("", "_stored"))
    for col in [
        "fdcId",
        "usda_description",
        "dataType",
        "usda_similarity_pct",
        "calories_kcal_100g",
        "carbs_g_100g",
        "protein_g_100g",
        "fat_g_100g",
        "query_used",
    ]:
        stored_col = f"{col}_stored"
        if stored_col in merged.columns:
            merged[col] = merged[col].combine_first(merged[stored_col])
            merged.drop(columns=[stored_col], inplace=True)

    if "match_source" not in merged.columns:
        merged["match_source"] = None
    merged.loc[merged["fdcId"].notna() & merged["match_source"].isna(), "match_source"] = source_label
    return merged


def enrich_items_with_usda(
    items: pd.DataFrame,
    api_keys_path: Path,
    cache_path: Path,
    stored_info_path: Optional[Path] = None,
    text_replacements_path: Optional[Path] = None,
    throttle_config: Optional[Dict] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Attach USDA macros to items, update caches, and persist detail cache."""

    api_key = _load_api_key(api_keys_path)
    throttle_cfg = throttle_config or THROTTLE_DEFAULTS
    replacements = units.load_text_replacements(text_replacements_path)

    cache = _load_cache(cache_path)
    stored_info = _load_stored_info(stored_info_path)

    items = items.copy()
    expected_cols = [
        "fdcId",
        "usda_description",
        "calories_kcal_100g",
        "carbs_g_100g",
        "protein_g_100g",
        "fat_g_100g",
        "match_source",
        "usda_similarity_pct",
        "dataType",
        "query_used",
    ]
    for col in expected_cols:
        if col not in items.columns:
            items[col] = None
    if "usda_match_status" not in items.columns:
        items["usda_match_status"] = "missing"

    items["usda_query"] = items.get("usda_query")
    items["usda_query"] = items["usda_query"].fillna(items.get("food_name_std", "")).astype(str)
    items["usda_query"] = items["usda_query"].apply(
        lambda t: units.normalize_food_text_for_usda(t, replacements=replacements)
    )

    # Drop USDA queries for blank/fasting rows so they do not enter API grouping/concat
    items["usda_query"] = items["usda_query"].replace({"": pd.NA, "nan": pd.NA, "none": pd.NA})
    if "event_type" in items.columns:
        items.loc[items["event_type"].str.lower() == "fasting", "usda_query"] = pd.NA

    # Prefer stored detail cache, then parquet cache
    items = _merge_lookup(items, stored_info, "usda_query", "query_used", source_label="stored_cache")
    if not cache.empty:
        items = items.merge(cache, how="left", on="food_name_std", suffixes=("", "_cache"))
        for col in expected_cols:
            cache_col = f"{col}_cache"
            if cache_col in items.columns:
                # Prefer cache values only where primary is null; avoid combine_first concat path that warns on empty arrays
                items[col] = items[col].where(items[col].notna(), items[cache_col])
                items.drop(columns=[cache_col], inplace=True)
        items.loc[items["fdcId"].notna() & items["match_source"].isna(), "match_source"] = "cache_hit"

    macro_cols = ["calories_kcal_100g", "carbs_g_100g", "protein_g_100g", "fat_g_100g"]
    missing_mask = items[macro_cols].isna().any(axis=1) & (items.get("event_type") != "fasting")
    queries_to_fetch = [q for q in items.loc[missing_mask, "usda_query"].dropna().unique() if str(q).strip()]
    items_non_empty = items[
        items["usda_query"].notna()
        & items.get("food_name_std", "").astype(str).str.strip().astype(bool)
    ]
    if items_non_empty.empty:
        items_by_query = {}
    else:
        items_by_query = (
            items_non_empty.groupby("usda_query")["food_name_std"]
            .apply(lambda s: list(s.dropna().unique()))
            .to_dict()
        )

    best_hits: List[Dict] = []
    composite_hits: List[Dict] = []
    for query in tqdm(queries_to_fetch, desc="USDA queries", unit="item"):
        if "/" in str(query):
            parts = [p.strip() for p in str(query).split("/") if p.strip()]
            part_hits = []
            for part in parts:
                candidates = usda_search_candidates(part, api_key, page_size=5, throttle_config=throttle_cfg)
                best, sim = pick_best_candidate(part, candidates)
                if best is not None:
                    part_hits.append({"part": part, "candidate": best, "similarity_pct": sim})
            if part_hits:
                composite_hits.append({"query": query, "parts": part_hits})
            continue

        candidates = usda_search_candidates(query, api_key, page_size=5, throttle_config=throttle_cfg)
        best, similarity_pct = pick_best_candidate(query, candidates)
        if best is None:
            continue
        best_hits.append({"query": query, "candidate": best, "similarity_pct": similarity_pct})

    query_to_fdc = {hit["query"]: str(hit["candidate"].get("fdcId")) for hit in best_hits if hit.get("candidate")}
    fdc_ids = [fid for fid in query_to_fdc.values() if fid]
    fdc_ids = list(dict.fromkeys(fdc_ids))

    # Collect fdc_ids from composite parts
    composite_part_ids: Dict[str, List[str]] = {}
    for comp in composite_hits:
        ids = []
        for part_hit in comp.get("parts", []):
            cand = part_hit.get("candidate") or {}
            fid = cand.get("fdcId")
            if fid:
                fid_str = str(fid)
                ids.append(fid_str)
                fdc_ids.append(fid_str)
        composite_part_ids[comp["query"]] = ids
    fdc_ids = list(dict.fromkeys(fdc_ids))

    details_by_id: Dict[str, Dict] = {}
    if fdc_ids:
        for i in tqdm(range(0, len(fdc_ids), 20), desc="USDA detail batches", unit="batch"):
            batch_ids = fdc_ids[i : i + 20]
            details = fdc_get_foods_batch(api_key, batch_ids, throttle_config=throttle_cfg)
            for detail in details:
                fdc_id = str(detail.get("fdcId")) if detail.get("fdcId") is not None else None
                if fdc_id:
                    details_by_id[fdc_id] = detail

    new_stored_rows = []
    new_cache_entries = []
    now_ts = pd.Timestamp.utcnow().isoformat()

    # Single-query hits
    for hit in best_hits:
        query = hit["query"]
        candidate = hit["candidate"]
        fdc_id = query_to_fdc.get(query)
        detail = details_by_id.get(fdc_id)
        macros = _extract_nutrients(detail or candidate)
        description = (detail or {}).get("description") or candidate.get("description")
        data_type = (detail or {}).get("dataType") or candidate.get("dataType")
        new_stored_rows.append(
            {
                "fdcId": fdc_id,
                "description": description,
                "dataType": data_type,
                "query_used": query,
                "similarity_pct": hit.get("similarity_pct"),
                "nutrients_full_json": json.dumps(detail or candidate),
                "energy_per_100g": macros.get("calories_kcal_100g"),
                "protein_per_100g": macros.get("protein_g_100g"),
                "carbs_per_100g": macros.get("carbs_g_100g"),
                "fat_per_100g": macros.get("fat_g_100g"),
                "retrieval_timestamp": now_ts,
            }
        )

        for food_name in items_by_query.get(query, []):
            new_cache_entries.append(
                {
                    "food_name_std": food_name,
                    "fdcId": fdc_id,
                    "usda_description": description,
                    "usda_similarity_pct": hit.get("similarity_pct"),
                    "dataType": data_type,
                    "query_used": query,
                    **macros,
                    "match_source": "api_hit",
                }
            )

    # Composite (slash-split) hits aggregated per parent query
    for comp in composite_hits:
        parent_query = comp["query"]
        parts = comp.get("parts", [])
        if not parts:
            continue

        part_macros = []
        part_descriptions = []
        part_similarities = []
        part_data_types = []
        part_fdcs = []

        for ph in parts:
            cand = ph.get("candidate") or {}
            fid = cand.get("fdcId")
            fid_str = str(fid) if fid is not None else None
            detail = details_by_id.get(fid_str)
            macros = _extract_nutrients(detail or cand)
            part_macros.append(macros)
            part_descriptions.append((detail or {}).get("description") or cand.get("description") or ph.get("part"))
            part_data_types.append((detail or {}).get("dataType") or cand.get("dataType"))
            part_similarities.append(ph.get("similarity_pct"))
            if fid_str:
                part_fdcs.append(fid_str)

        def _sum_macro(key: str) -> Optional[float]:
            vals = [m.get(key) for m in part_macros if m.get(key) is not None]
            return float(sum(vals)) if vals else None

        aggregated_macros = {
            "calories_kcal_100g": _sum_macro("calories_kcal_100g"),
            "carbs_g_100g": _sum_macro("carbs_g_100g"),
            "protein_g_100g": _sum_macro("protein_g_100g"),
            "fat_g_100g": _sum_macro("fat_g_100g"),
        }

        agg_description = "Composite: " + " | ".join([d for d in part_descriptions if d]) if part_descriptions else None
        agg_similarity = min([s for s in part_similarities if s is not None], default=None)
        agg_data_type = "composite"
        agg_fdc_id = "|".join(part_fdcs) if part_fdcs else None

        new_stored_rows.append(
            {
                "fdcId": agg_fdc_id,
                "description": agg_description,
                "dataType": agg_data_type,
                "query_used": parent_query,
                "similarity_pct": agg_similarity,
                "nutrients_full_json": json.dumps({"parts": [ph.get("candidate") for ph in parts]}),
                "energy_per_100g": aggregated_macros.get("calories_kcal_100g"),
                "protein_per_100g": aggregated_macros.get("protein_g_100g"),
                "carbs_per_100g": aggregated_macros.get("carbs_g_100g"),
                "fat_per_100g": aggregated_macros.get("fat_g_100g"),
                "retrieval_timestamp": now_ts,
            }
        )

        for food_name in items_by_query.get(parent_query, []):
            new_cache_entries.append(
                {
                    "food_name_std": food_name,
                    "fdcId": agg_fdc_id,
                    "usda_description": agg_description,
                    "usda_similarity_pct": agg_similarity,
                    "dataType": agg_data_type,
                    "query_used": parent_query,
                    **aggregated_macros,
                    "match_source": "api_hit_composite",
                }
            )

    if new_cache_entries:
        cache = pd.concat([cache, pd.DataFrame(new_cache_entries)], ignore_index=True)
        cache.drop_duplicates(subset=["food_name_std"], keep="last", inplace=True)
        _save_cache(cache, cache_path)

    if new_stored_rows:
        stored_info = pd.concat([stored_info, pd.DataFrame(new_stored_rows)], ignore_index=True)
        stored_info.drop_duplicates(subset=["query_used"], keep="last", inplace=True)
        stored_info.drop_duplicates(subset=["fdcId"], keep="last", inplace=True)
        _save_stored_info(stored_info, stored_info_path)

    # Reapply lookups with updated data
    items = _merge_lookup(items, stored_info, "usda_query", "query_used", source_label="stored_cache")
    if not cache.empty:
        items = items.drop(columns=[c for c in items.columns if c.endswith("_cache")], errors="ignore")
        items = items.merge(cache, how="left", on="food_name_std", suffixes=("", "_cache"))
        for col in expected_cols:
            cache_col = f"{col}_cache"
            if cache_col in items.columns:
                items[col] = items[col].combine_first(items[cache_col])
                items.drop(columns=[cache_col], inplace=True)
        items.loc[items["fdcId"].notna() & items["match_source"].isna(), "match_source"] = "cache_hit"

    for col in macro_cols + ["usda_similarity_pct"]:
        if col in items.columns:
            items[col] = pd.to_numeric(items[col], errors="coerce")

    items.loc[items[macro_cols].notna().all(axis=1), "usda_match_status"] = "matched"
    items.loc[items[macro_cols].isna().any(axis=1), "usda_match_status"] = "missing"

    logger.info(
        "USDA enrichment complete; match counts=%s",
        items["usda_match_status"].value_counts(dropna=False).to_dict(),
    )

    return items, cache, stored_info
