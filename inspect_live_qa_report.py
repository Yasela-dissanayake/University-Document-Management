#!/usr/bin/env python3
"""Safely inspect QA evaluation report sections without printing document plaintext."""
import argparse
import json
from pathlib import Path

SENSITIVE = (
    "question", "answer", "response", "document", "content", "plaintext",
    "expected_facts", "actual_facts", "context", "traceback",
    "exception_message", "error_detail"
)

def redact(value, key=""):
    if any(token in key.lower() for token in SENSITIVE):
        return "<suppressed>"
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    return value

def show_section(name, value):
    print(f"\\n===== {name} =====")
    if isinstance(value, list):
        print("Record count:", len(value))
        for i, item in enumerate(value, 1):
            print(f"\\nRecord {i}:")
            print(json.dumps(redact(item), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(redact(value), indent=2, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", help="Path to evaluation report JSON")
    args = parser.parse_args()
    path = Path(args.report).expanduser()
    if not path.is_file():
        raise SystemExit(f"Report not found: {path}")
    with path.open(encoding="utf-8") as f:
        report = json.load(f)
    print("Top-level keys:", list(report.keys()))
    show_section("SUMMARY", report.get("summary"))
    show_section("QA RESULTS", report.get("qa_results"))
    show_section("SECURITY RESULTS", report.get("security_results"))
    print("\\nSensitive text fields were suppressed.")

if __name__ == "__main__":
    main()
