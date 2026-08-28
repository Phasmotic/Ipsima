#!/usr/bin/env python3
"""Strict, verdict-free analysis of paired transport-link launch observations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any, Callable

if __package__:
    from scripts import check_launch_metrics as metrics_checker
else:  # direct execution from the scripts directory
    import check_launch_metrics as metrics_checker  # type: ignore[no-redef]


PAIR_COUNT = 10
VARIANTS = ("control", "linked")
ANALYSIS_SCHEMA_VERSION = 1
LAUNCH_LINK_SYMBOL = "_talaria_" + "launch_link_anchor"
TRANSPORT_FACTORY_SYMBOL = "_talaria_" + "transport_factory_link_anchor"
MEASUREMENT_EVIDENCE_INVALID = "measurement_" + "evidence_invalid"
LINK_SYMBOL_EVIDENCE_INVALID = "link_symbol_" + "evidence_invalid"
LINK_CHECK_NAMES = (
    "control_has_one_launch_anchor",
    "linked_has_one_launch_anchor",
    "control_omits_transport_factory_anchor",
    "linked_has_one_transport_factory_anchor",
    "transport_initializer_control_absent_linked_present",
    "http_loader_witness_control_absent_linked_present",
    "ticket_acquirer_witness_control_absent_linked_present",
    "websocket_connector_witness_control_absent_linked_present",
)
SEMANTIC_SYMBOL_SPECS = {
    "transport_initializer_control_absent_linked_present": (
        "HermesKit.WebSocketHermesTransport.__allocating_init(configuration:",
        ("tokenProvider:",),
    ),
    "http_loader_witness_control_absent_linked_present": (
        "protocol witness for HermesKit.HermesHTTPDataLoading.data(for:",
        ("in conformance HermesKit.URLSessionHTTPDataLoader",),
    ),
    "ticket_acquirer_witness_control_absent_linked_present": (
        "protocol witness for HermesKit.HermesWebSocketTicketAcquiring.acquireTicket()",
        ("in conformance HermesKit.URLSessionWebSocketTicketAcquirer",),
    ),
    "websocket_connector_witness_control_absent_linked_present": (
        "protocol witness for HermesKit.HermesWebSocketConnecting.connect(to:",
        ("in conformance HermesKit.URLSessionWebSocketConnector",),
    ),
}
LINK_COLLECTION_BLOCKER_CODES = frozenset(
    f"{variant}_{stage}"
    for variant in VARIANTS
    for stage in (
        "build_failed",
        "build_log_invalid",
        "debug_dylib_ambiguous",
        "symbol_inventory_failed",
        "symbol_inventory_empty",
        "symbol_inventory_stderr",
        "symbol_demangle_failed",
        "symbol_demangle_empty",
        "symbol_demangle_stderr",
    )
)
MEASUREMENT_COLLECTION_BLOCKER_CODES = frozenset(
    f"{variant}_{stage}"
    for variant in VARIANTS
    for stage in (
        "test_failed",
        "test_log_invalid",
        "metrics_export_failed",
        "metrics_empty",
        "metrics_stderr",
    )
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


def _parse_symbol_names(raw: bytes, label: str) -> tuple[str, ...]:
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
    names = tuple(line.strip() for line in text.splitlines())
    if not names or any(not name for name in names):
        raise AnalysisBlocked(f"{label} contains an ambiguous symbol record")
    return names


def _parse_status_token(raw: bytes, label: str) -> str:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AnalysisBlocked(f"{label} is not valid UTF-8") from error
    if not text.endswith("\n") or text.count("\n") != 1:
        raise AnalysisBlocked(f"{label} is not one canonical line")
    token = text[:-1]
    if not token or token != token.strip() or "\x00" in token:
        raise AnalysisBlocked(f"{label} token is invalid")
    return token


def _parse_link_collection_status(raw: bytes) -> str | None:
    token = _parse_status_token(raw, "link collection status")
    if token == "complete":
        return None
    if token not in LINK_COLLECTION_BLOCKER_CODES:
        raise AnalysisBlocked("link collection blocker code is unsupported")
    return token


def _parse_measurement_collection_status(
    raw: bytes,
) -> tuple[str | None, int | None]:
    token = _parse_status_token(raw, "measurement collection status")
    if token == "complete":
        return None, None
    if token == "order_generation_failed":
        return token, None
    pieces = token.rsplit(":", maxsplit=1)
    if len(pieces) != 2 or pieces[0] not in MEASUREMENT_COLLECTION_BLOCKER_CODES:
        raise AnalysisBlocked("measurement collection blocker code is unsupported")
    pair_text = pieces[1]
    if len(pair_text) != 2 or not pair_text.isascii() or not pair_text.isdigit():
        raise AnalysisBlocked("measurement collection pair is invalid")
    pair = int(pair_text)
    if pair < 1 or pair > PAIR_COUNT or pair_text != f"{pair:02d}":
        raise AnalysisBlocked("measurement collection pair is out of range")
    return pieces[0], pair


def _semantic_symbol_count(
    names: tuple[str, ...], required_prefix: str, required_tokens: tuple[str, ...]
) -> int:
    return sum(
        name.startswith(required_prefix)
        and all(token in name for token in required_tokens)
        for name in names
    )


def summarize_link_symbols(
    control_raw: bytes, linked_raw: bytes
) -> dict[str, object]:
    """Summarize an exact semantic-symbol contrast without retaining raw names."""

    control = _parse_symbol_names(control_raw, "control symbol evidence")
    linked = _parse_symbol_names(linked_raw, "linked symbol evidence")
    checks = {
        "control_has_one_launch_anchor": control.count(LAUNCH_LINK_SYMBOL) == 1,
        "linked_has_one_launch_anchor": linked.count(LAUNCH_LINK_SYMBOL) == 1,
        "control_omits_transport_factory_anchor": (
            control.count(TRANSPORT_FACTORY_SYMBOL) == 0
        ),
        "linked_has_one_transport_factory_anchor": (
            linked.count(TRANSPORT_FACTORY_SYMBOL) == 1
        ),
    }
    for name, (prefix, tokens) in SEMANTIC_SYMBOL_SPECS.items():
        checks[name] = (
            _semantic_symbol_count(control, prefix, tokens) == 0
            and _semantic_symbol_count(linked, prefix, tokens) == 1
        )
    verified = all(checks.values())
    return {
        "status": "verified" if verified else "blocked",
        "checks": checks,
        "control_symbol_count": len(control),
        "linked_symbol_count": len(linked),
        "blocker_code": None if verified else "link_contrast_not_established",
    }


def verify_link_symbols(control_raw: bytes, linked_raw: bytes) -> dict[str, object]:
    """Prove the linked debug dylib retains the real transport factory thunk."""

    result = summarize_link_symbols(control_raw, linked_raw)
    if result["status"] != "verified":
        raise AnalysisBlocked("semantic link-symbol contrast was not established")
    return result


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


def _blocked_measurements(
    blocker_code: str | None = None,
    blocker_pair: int | None = None,
) -> dict[str, object]:
    return {
        "status": "blocked",
        "validated_pair_count": 0,
        "pairs": [],
        "summary": None,
        "blocker_code": blocker_code or MEASUREMENT_EVIDENCE_INVALID,
        "blocker_pair": blocker_pair,
    }


def _blocked_link_contrast(
    blocker_code: str | None = None,
) -> dict[str, object]:
    return {
        "status": "blocked",
        "checks": {name: None for name in LINK_CHECK_NAMES},
        "control_symbol_count": None,
        "linked_symbol_count": None,
        "blocker_code": blocker_code or LINK_SYMBOL_EVIDENCE_INVALID,
    }


def render_link_preflight(arguments: argparse.Namespace) -> dict[str, object]:
    try:
        collection_blocker = _parse_link_collection_status(
            _read_bytes(arguments.collection_status, "link collection status")
        )
    except AnalysisBlocked:
        link_contrast = _blocked_link_contrast("link_collection_status_invalid")
    else:
        if collection_blocker is not None:
            link_contrast = _blocked_link_contrast(collection_blocker)
        else:
            try:
                link_contrast = summarize_link_symbols(
                    _read_bytes(arguments.control_symbols, "control symbol evidence"),
                    _read_bytes(arguments.linked_symbols, "linked symbol evidence"),
                )
            except AnalysisBlocked:
                link_contrast = _blocked_link_contrast()
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis": "transport_link_preflight",
        "status": link_contrast["status"],
        "link_contrast": link_contrast,
    }


def render_analysis(arguments: argparse.Namespace) -> dict[str, object]:
    try:
        measurement_blocker, blocker_pair = _parse_measurement_collection_status(
            _read_bytes(
                arguments.measurement_collection_status,
                "measurement collection status",
            )
        )
    except AnalysisBlocked:
        measurements = _blocked_measurements("measurement_collection_status_invalid")
    else:
        if measurement_blocker is not None:
            measurements = _blocked_measurements(measurement_blocker, blocker_pair)
        else:
            try:
                schema_raw = _read_bytes(arguments.schema_json, "metrics schema")
                try:
                    metrics_checker.verify_schema_bytes(schema_raw)
                except metrics_checker.EvidenceBlocked as error:
                    raise AnalysisBlocked("metrics schema is invalid") from error
                orders = _parse_order_document(
                    _read_bytes(arguments.order_json, "order JSON")
                )
                observations = load_observations(arguments.evidence_dir, orders)
                analysis = summarize(observations)
            except AnalysisBlocked:
                measurements = _blocked_measurements()
            else:
                measurements = {
                    "status": "observed",
                    "validated_pair_count": PAIR_COUNT,
                    "pairs": analysis["pairs"],
                    "summary": analysis["summary"],
                    "blocker_code": None,
                    "blocker_pair": None,
                }

    try:
        link_collection_blocker = _parse_link_collection_status(
            _read_bytes(
                arguments.link_collection_status,
                "link collection status",
            )
        )
    except AnalysisBlocked:
        link_contrast = _blocked_link_contrast("link_collection_status_invalid")
    else:
        if link_collection_blocker is not None:
            link_contrast = _blocked_link_contrast(link_collection_blocker)
        else:
            try:
                link_contrast = summarize_link_symbols(
                    _read_bytes(arguments.control_symbols, "control symbol evidence"),
                    _read_bytes(arguments.linked_symbols, "linked symbol evidence"),
                )
            except AnalysisBlocked:
                link_contrast = _blocked_link_contrast()

    status = (
        "observed"
        if measurements["status"] == "observed"
        and link_contrast["status"] == "verified"
        else "blocked"
    )
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis": "transport_link_launch_ab",
        "status": status,
        "delta_definition": "linked_seconds_minus_control_seconds",
        "expected_pair_count": PAIR_COUNT,
        "measurements": measurements,
        "link_contrast": link_contrast,
    }


def _canonical_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise AnalysisBlocked(f"{label} must be a decimal string")
    try:
        parsed = Decimal(value)
    except DecimalException as error:
        raise AnalysisBlocked(f"{label} is not decimal") from error
    if not parsed.is_finite() or format(parsed, "f") != value:
        raise AnalysisBlocked(f"{label} is not a canonical finite decimal")
    return parsed


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise AnalysisBlocked(f"{label} must be an integer")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed != parsed.to_integral_value() or parsed < 0:
        raise AnalysisBlocked(f"{label} must be a nonnegative integer")
    return int(parsed)


def _validate_measurements(value: object) -> str:
    measurements = _exact_object(
        value,
        frozenset(
            {
                "status",
                "validated_pair_count",
                "pairs",
                "summary",
                "blocker_code",
                "blocker_pair",
            }
        ),
        "measurements",
    )
    status = measurements["status"]
    if status == "blocked":
        if (
            _integer(measurements["validated_pair_count"], "validated pair count")
            != 0
            or measurements["pairs"] != []
            or measurements["summary"] is not None
        ):
            raise AnalysisBlocked("blocked measurement evidence is contradictory")
        blocker_code = measurements["blocker_code"]
        blocker_pair = measurements["blocker_pair"]
        if blocker_code in {
            MEASUREMENT_EVIDENCE_INVALID,
            "measurement_collection_status_invalid",
            "order_generation_failed",
        }:
            if blocker_pair is not None:
                raise AnalysisBlocked("non-pair blocker names a pair")
        elif blocker_code in MEASUREMENT_COLLECTION_BLOCKER_CODES:
            pair = _integer(blocker_pair, "measurement blocker pair")
            if pair < 1 or pair > PAIR_COUNT:
                raise AnalysisBlocked("measurement blocker pair is out of range")
        else:
            raise AnalysisBlocked("measurement blocker code is unsupported")
        return status
    if status != "observed":
        raise AnalysisBlocked("measurement status is unsupported")
    if (
        _integer(measurements["validated_pair_count"], "validated pair count")
        != PAIR_COUNT
        or measurements["blocker_code"] is not None
        or measurements["blocker_pair"] is not None
    ):
        raise AnalysisBlocked("observed measurement evidence is contradictory")

    raw_pairs = measurements["pairs"]
    if not isinstance(raw_pairs, list) or len(raw_pairs) != PAIR_COUNT:
        raise AnalysisBlocked("observed measurement pairs are incomplete")
    observations: list[PairObservation] = []
    for index, value_pair in enumerate(raw_pairs, start=1):
        pair = _exact_object(
            value_pair,
            frozenset(
                {
                    "control_seconds",
                    "delta_seconds",
                    "linked_seconds",
                    "order",
                    "pair",
                }
            ),
            f"analysis pair {index}",
        )
        if _integer(pair["pair"], f"analysis pair {index} number") != index:
            raise AnalysisBlocked(f"analysis pair {index} has the wrong number")
        expected_order = (
            ["control", "linked"] if index % 2 == 1 else ["linked", "control"]
        )
        if pair["order"] != expected_order:
            raise AnalysisBlocked(f"analysis pair {index} has the wrong order")
        control = _canonical_decimal(
            pair["control_seconds"], f"analysis pair {index} control"
        )
        linked = _canonical_decimal(
            pair["linked_seconds"], f"analysis pair {index} linked"
        )
        delta = _canonical_decimal(
            pair["delta_seconds"], f"analysis pair {index} delta"
        )
        if linked - control != delta:
            raise AnalysisBlocked(f"analysis pair {index} delta is contradictory")
        observations.append(
            PairObservation(index, tuple(expected_order), control, linked)
        )

    expected = summarize(tuple(observations))
    if raw_pairs != expected["pairs"] or measurements["summary"] != expected["summary"]:
        raise AnalysisBlocked("observed measurement statistics are contradictory")
    return status


def _validate_link_contrast(value: object) -> str:
    contrast = _exact_object(
        value,
        frozenset(
            {
                "status",
                "checks",
                "control_symbol_count",
                "linked_symbol_count",
                "blocker_code",
            }
        ),
        "link contrast",
    )
    checks = _exact_object(
        contrast["checks"], frozenset(LINK_CHECK_NAMES), "link checks"
    )
    status = contrast["status"]
    if status == "verified":
        if (
            contrast["blocker_code"] is not None
            or not all(result is True for result in checks.values())
            or _integer(contrast["control_symbol_count"], "control symbol count") < 1
            or _integer(contrast["linked_symbol_count"], "linked symbol count") < 1
        ):
            raise AnalysisBlocked("verified link evidence is contradictory")
        return status
    if status != "blocked":
        raise AnalysisBlocked("link status is unsupported")

    blocker_code = contrast["blocker_code"]
    if blocker_code in {
        LINK_SYMBOL_EVIDENCE_INVALID,
        "link_collection_status_invalid",
        *LINK_COLLECTION_BLOCKER_CODES,
    }:
        if (
            not all(result is None for result in checks.values())
            or contrast["control_symbol_count"] is not None
            or contrast["linked_symbol_count"] is not None
        ):
            raise AnalysisBlocked("invalid link-symbol evidence is contradictory")
        return status
    if blocker_code == "link_contrast_not_established":
        if any(not isinstance(result, bool) for result in checks.values()):
            raise AnalysisBlocked("link contrast checks are ambiguous")
        if all(checks.values()):
            raise AnalysisBlocked("blocked link contrast contains no failed check")
        _integer(contrast["control_symbol_count"], "control symbol count")
        _integer(contrast["linked_symbol_count"], "linked symbol count")
        return status
    raise AnalysisBlocked("link blocker code is unsupported")


def validate_link_preflight_document(value: object) -> dict[str, Any]:
    document = _exact_object(
        value,
        frozenset({"schema_version", "analysis", "status", "link_contrast"}),
        "link preflight root",
    )
    if (
        _integer(document["schema_version"], "link preflight schema version")
        != ANALYSIS_SCHEMA_VERSION
        or document["analysis"] != "transport_link_preflight"
    ):
        raise AnalysisBlocked("link preflight identity is invalid")
    link_status = _validate_link_contrast(document["link_contrast"])
    if document["status"] != link_status:
        raise AnalysisBlocked("link preflight status is contradictory")
    return document


def validate_analysis_document(value: object) -> dict[str, Any]:
    document = _exact_object(
        value,
        frozenset(
            {
                "schema_version",
                "analysis",
                "status",
                "delta_definition",
                "expected_pair_count",
                "measurements",
                "link_contrast",
            }
        ),
        "analysis root",
    )
    if (
        _integer(document["schema_version"], "analysis schema version")
        != ANALYSIS_SCHEMA_VERSION
        or document["analysis"] != "transport_link_launch_ab"
        or document["delta_definition"]
        != "linked_seconds_minus_control_seconds"
        or _integer(document["expected_pair_count"], "expected pair count")
        != PAIR_COUNT
    ):
        raise AnalysisBlocked("analysis identity is invalid")

    measurement_status = _validate_measurements(document["measurements"])
    link_status = _validate_link_contrast(document["link_contrast"])
    expected_status = (
        "observed"
        if measurement_status == "observed" and link_status == "verified"
        else "blocked"
    )
    if document["status"] != expected_status:
        raise AnalysisBlocked("overall analysis status is contradictory")
    return document


def _parse_analysis_bytes(raw: bytes) -> dict[str, Any]:
    try:
        value = metrics_checker.parse_json_bytes(raw, "A/B analysis JSON")
    except metrics_checker.EvidenceBlocked as error:
        raise AnalysisBlocked("analysis JSON is invalid") from error
    return validate_analysis_document(value)


def _parse_link_preflight_bytes(raw: bytes) -> dict[str, Any]:
    try:
        value = metrics_checker.parse_json_bytes(raw, "A/B link preflight JSON")
    except metrics_checker.EvidenceBlocked as error:
        raise AnalysisBlocked("link preflight JSON is invalid") from error
    return validate_link_preflight_document(value)


def _write_json(
    path: Path,
    document: dict[str, object],
    validator: Callable[[bytes], object],
) -> None:
    raw = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    validator(raw)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AnalysisBlocked("analysis evidence could not be written") from error


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    render_link = commands.add_parser("render-link")
    render_link.add_argument("--collection-status", type=Path, required=True)
    render_link.add_argument("--control-symbols", type=Path, required=True)
    render_link.add_argument("--linked-symbols", type=Path, required=True)
    render_link.add_argument("--output-json", type=Path, required=True)

    enforce_link = commands.add_parser("enforce-link")
    enforce_link.add_argument("--input-json", type=Path, required=True)

    render = commands.add_parser("render")
    render.add_argument("--evidence-dir", type=Path, required=True)
    render.add_argument("--link-collection-status", type=Path, required=True)
    render.add_argument("--measurement-collection-status", type=Path, required=True)
    render.add_argument("--order-json", type=Path, required=True)
    render.add_argument("--schema-json", type=Path, required=True)
    render.add_argument("--output-json", type=Path, required=True)
    render.add_argument("--control-symbols", type=Path, required=True)
    render.add_argument("--linked-symbols", type=Path, required=True)

    enforce = commands.add_parser("enforce")
    enforce.add_argument("--input-json", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    if arguments.command == "render-link":
        document = render_link_preflight(arguments)
        try:
            _write_json(
                arguments.output_json,
                document,
                _parse_link_preflight_bytes,
            )
        except AnalysisBlocked:
            print(
                "G12 LINK A/B BLOCKED: sanitized link evidence could not be retained",
                file=sys.stderr,
            )
            return 2
        print(
            "G12 LINK A/B PREFLIGHT EVIDENCE RETAINED: "
            f"status={document['status']}"
        )
        return 0

    if arguments.command == "enforce-link":
        try:
            document = _parse_link_preflight_bytes(
                _read_bytes(arguments.input_json, "link preflight evidence")
            )
        except AnalysisBlocked:
            print(
                "G12 LINK A/B BLOCKED: retained link evidence is invalid",
                file=sys.stderr,
            )
            return 2
        if document["status"] != "verified":
            print(
                "G12 LINK A/B BLOCKED: retained link evidence is blocked",
                file=sys.stderr,
            )
            return 2
        print("G12 LINK A/B LINKAGE VERIFIED: defined production symbols differ")
        return 0

    if arguments.command == "render":
        document = render_analysis(arguments)
        try:
            _write_json(arguments.output_json, document, _parse_analysis_bytes)
        except AnalysisBlocked:
            print(
                "G12 LINK A/B BLOCKED: sanitized analysis evidence could not be retained",
                file=sys.stderr,
            )
            return 2
        print(f"G12 LINK A/B EVIDENCE RETAINED: status={document['status']}")
        return 0

    try:
        document = _parse_analysis_bytes(
            _read_bytes(arguments.input_json, "analysis evidence")
        )
    except AnalysisBlocked:
        print("G12 LINK A/B BLOCKED: retained analysis evidence is invalid", file=sys.stderr)
        return 2
    if document["status"] != "observed":
        print("G12 LINK A/B BLOCKED: retained analysis evidence is blocked", file=sys.stderr)
        return 2

    measurements = document["measurements"]
    assert isinstance(measurements, dict)
    summary = measurements["summary"]
    assert isinstance(summary, dict)
    print(
        "G12 LINK A/B OBSERVED: "
        f"pairs={PAIR_COUNT} median_delta={summary['median_delta_seconds']} s "
        f"mad={summary['mad_delta_seconds']} s mean={summary['mean_delta_seconds']} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
