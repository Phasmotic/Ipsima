#!/usr/bin/env python3
"""Strict, verdict-free analysis of paired transport-link launch observations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any

if __package__:
    from scripts import check_launch_metrics as metrics_checker
else:  # direct execution from the scripts directory
    import check_launch_metrics as metrics_checker  # type: ignore[no-redef]


PAIR_COUNT = 10
VARIANTS = ("control", "linked")
TRANSPORT_OBJECT_NAMES = (
    "HermesTransport",
    "WebSocketNetworking",
    "WebSocketHermesTransport",
)


class AnalysisBlocked(RuntimeError):
    """The supplied evidence cannot produce a trustworthy paired analysis."""


@dataclass(frozen=True)
class PairObservation:
    pair: int
    order: tuple[str, str]
    control_seconds: Decimal
    linked_seconds: Decimal

    @property
    def delta_seconds(self) -> Decimal:
        """Linked minus control; positive means the linked observation was slower."""

        return self.linked_seconds - self.control_seconds


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        if not path.is_file():
            raise AnalysisBlocked(f"{label} file does not exist")
        return path.read_bytes()
    except AnalysisBlocked:
        raise
    except OSError as error:
        raise AnalysisBlocked(
            f"{label} file could not be read ({type(error).__name__})"
        ) from error


def _exact_object(
    value: object, expected_keys: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisBlocked(f"{label} must be an object")
    actual_keys = frozenset(value)
    if actual_keys != expected_keys:
        raise AnalysisBlocked(f"{label} keys are ambiguous")
    return value


def _parse_order_document(raw: bytes) -> tuple[tuple[str, str], ...]:
    try:
        document = metrics_checker.parse_json_bytes(raw, "order JSON")
    except metrics_checker.EvidenceBlocked as error:
        raise AnalysisBlocked(str(error)) from error

    root = _exact_object(document, frozenset({"pairs"}), "order JSON root")
    pairs = root["pairs"]
    if not isinstance(pairs, list) or len(pairs) != PAIR_COUNT:
        actual = len(pairs) if isinstance(pairs, list) else "non-array"
        raise AnalysisBlocked(
            f"order pairs must contain exactly {PAIR_COUNT} entries, found {actual}"
        )

    result: list[tuple[str, str]] = []
    for index, raw_pair in enumerate(pairs, start=1):
        pair = _exact_object(
            raw_pair, frozenset({"pair", "order"}), f"order pair {index}"
        )
        pair_number = pair["pair"]
        if (
            isinstance(pair_number, bool)
            or not isinstance(pair_number, Decimal)
            or pair_number != Decimal(index)
        ):
            raise AnalysisBlocked(f"order pair {index} has the wrong pair number")
        order = pair["order"]
        expected_order = (
            ["control", "linked"] if index % 2 == 1 else ["linked", "control"]
        )
        if order != expected_order:
            raise AnalysisBlocked(
                f"order pair {index} must be {' then '.join(expected_order)}"
            )
        result.append((expected_order[0], expected_order[1]))
    return tuple(result)


def _decode_link_map(raw: bytes, label: str) -> str:
    if not raw:
        raise AnalysisBlocked(f"{label} is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AnalysisBlocked(f"{label} is not valid UTF-8") from error
    if not text.strip():
        raise AnalysisBlocked(f"{label} contains only whitespace")
    if "\x00" in text:
        raise AnalysisBlocked(f"{label} contains a NUL byte")
    return text


def _link_map_object_records(link_map: str, label: str) -> tuple[str, ...]:
    lines = link_map.splitlines()
    headers = [index for index, line in enumerate(lines) if line == "# Object files:"]
    if len(headers) != 1:
        raise AnalysisBlocked(f"{label} must contain one object-files section")

    records: list[str] = []
    for line in lines[headers[0] + 1 :]:
        if line.startswith("# "):
            break
        match = re.fullmatch(r"\[\s*\d+\]\s+(.+)", line)
        if match is not None:
            records.append(match.group(1))
    if not records:
        raise AnalysisBlocked(f"{label} object-files section is empty")
    return tuple(records)


def _records_contain_object(records: tuple[str, ...], object_name: str) -> bool:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(object_name)}\.o(?:\)|\Z)"
    )
    return any(pattern.search(record) is not None for record in records)


def verify_link_maps(control_raw: bytes, linked_raw: bytes) -> None:
    """Prove the control omits and linked binary includes transport objects."""

    control = _decode_link_map(control_raw, "control link map")
    linked = _decode_link_map(linked_raw, "linked link map")
    control_records = _link_map_object_records(control, "control link map")
    linked_records = _link_map_object_records(linked, "linked link map")
    for object_name in TRANSPORT_OBJECT_NAMES:
        if _records_contain_object(control_records, object_name):
            raise AnalysisBlocked(
                f"control link map unexpectedly contains {object_name}.o"
            )
        if not _records_contain_object(linked_records, object_name):
            raise AnalysisBlocked(
                f"linked link map does not contain {object_name}.o"
            )


def _expected_pair_names() -> frozenset[str]:
    return frozenset(
        f"pair-{pair:02d}-{variant}.json"
        for pair in range(1, PAIR_COUNT + 1)
        for variant in VARIANTS
    )


def _validate_pair_file_set(evidence_dir: Path) -> None:
    try:
        if not evidence_dir.is_dir():
            raise AnalysisBlocked("evidence directory does not exist")
        actual = frozenset(path.name for path in evidence_dir.glob("pair-*.json"))
    except AnalysisBlocked:
        raise
    except OSError as error:
        raise AnalysisBlocked(
            f"evidence directory could not be read ({type(error).__name__})"
        ) from error
    if actual != _expected_pair_names():
        missing_count = len(_expected_pair_names() - actual)
        unexpected_count = len(actual - _expected_pair_names())
        raise AnalysisBlocked(
            "paired evidence file set is incomplete or ambiguous "
            f"(missing={missing_count}, unexpected={unexpected_count})"
        )


def load_observations(
    evidence_dir: Path, orders: tuple[tuple[str, str], ...]
) -> tuple[PairObservation, ...]:
    _validate_pair_file_set(evidence_dir)
    if len(orders) != PAIR_COUNT:
        raise AnalysisBlocked("validated order evidence has the wrong pair count")

    observations: list[PairObservation] = []
    expected_device: tuple[str, str] | None = None
    for pair in range(1, PAIR_COUNT + 1):
        values: dict[str, Decimal] = {}
        for variant in VARIANTS:
            raw = _read_bytes(
                evidence_dir / f"pair-{pair:02d}-{variant}.json",
                f"pair {pair} {variant} metrics",
            )
            try:
                document = metrics_checker.parse_metrics_json(raw)
                evidence = metrics_checker.verify_metrics_document(
                    document, metrics_checker.LINK_AB_EXPECTATION
                )
            except metrics_checker.EvidenceBlocked as error:
                raise AnalysisBlocked(
                    f"pair {pair} {variant} metrics are invalid: {error}"
                ) from error

            device = (evidence.device_id, evidence.device_name)
            if expected_device is None:
                expected_device = device
            elif device != expected_device:
                raise AnalysisBlocked(
                    "paired observations were not collected on one device"
                )
            values[variant] = evidence.measurements[0]

        observations.append(
            PairObservation(
                pair,
                orders[pair - 1],
                values["control"],
                values["linked"],
            )
        )
    return tuple(observations)


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise AnalysisBlocked("cannot calculate a median without observations")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def summarize(observations: tuple[PairObservation, ...]) -> dict[str, object]:
    if len(observations) != PAIR_COUNT:
        raise AnalysisBlocked(f"analysis requires exactly {PAIR_COUNT} pairs")
    try:
        deltas = tuple(observation.delta_seconds for observation in observations)
        median = _median(deltas)
        mad = _median(tuple(abs(delta - median) for delta in deltas))
        mean = sum(deltas, Decimal(0)) / Decimal(PAIR_COUNT)
    except (DecimalException, OverflowError) as error:
        raise AnalysisBlocked("paired deltas cannot produce finite statistics") from error
    if not all(value.is_finite() for value in (*deltas, median, mad, mean)):
        raise AnalysisBlocked("paired deltas cannot produce finite statistics")

    sign_count = {
        "negative": sum(delta < 0 for delta in deltas),
        "positive": sum(delta > 0 for delta in deltas),
        "zero": sum(delta == 0 for delta in deltas),
    }
    return {
        "analysis": "transport_link_launch_ab",
        "delta_definition": "linked_seconds_minus_control_seconds",
        "pair_count": PAIR_COUNT,
        "pairs": [
            {
                "control_seconds": format(observation.control_seconds, "f"),
                "delta_seconds": format(observation.delta_seconds, "f"),
                "linked_seconds": format(observation.linked_seconds, "f"),
                "order": list(observation.order),
                "pair": observation.pair,
            }
            for observation in observations
        ],
        "summary": {
            "mad_delta_seconds": format(mad, "f"),
            "mean_delta_seconds": format(mean, "f"),
            "median_delta_seconds": format(median, "f"),
            "sign_count": sign_count,
        },
    }


def _write_json(path: Path, document: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as error:
        raise AnalysisBlocked(
            f"analysis evidence could not be written ({type(error).__name__})"
        ) from error


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--order-json", type=Path, required=True)
    parser.add_argument("--schema-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--control-link-map", type=Path, required=True)
    parser.add_argument("--linked-link-map", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        schema_raw = _read_bytes(arguments.schema_json, "metrics schema")
        try:
            metrics_checker.verify_schema_bytes(schema_raw)
        except metrics_checker.EvidenceBlocked as error:
            raise AnalysisBlocked(str(error)) from error
        orders = _parse_order_document(
            _read_bytes(arguments.order_json, "order JSON")
        )
        verify_link_maps(
            _read_bytes(arguments.control_link_map, "control link map"),
            _read_bytes(arguments.linked_link_map, "linked link map"),
        )
        observations = load_observations(arguments.evidence_dir, orders)
        analysis = summarize(observations)
        _write_json(arguments.output_json, analysis)
    except AnalysisBlocked as error:
        print(f"G12 LINK A/B BLOCKED: {error}", file=sys.stderr)
        return 2

    summary = analysis["summary"]
    assert isinstance(summary, dict)
    print(
        "G12 LINK A/B OBSERVED: "
        f"pairs={PAIR_COUNT} median_delta={summary['median_delta_seconds']} s "
        f"mad={summary['mad_delta_seconds']} s mean={summary['mean_delta_seconds']} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
