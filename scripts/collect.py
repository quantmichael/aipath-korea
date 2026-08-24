from __future__ import annotations

import argparse
import json

from collect.service import run_collection


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run AI PATH KOREA source collection.",
    )
    parser.add_argument("--max-sources", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--method", default=None)
    args = parser.parse_args()

    result = run_collection(
        max_sources=args.max_sources,
        max_candidates_per_source=args.max_candidates,
        method=args.method,
    )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
