from __future__ import annotations

import argparse
import json

from collect.promote import promote_candidates


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote verified candidates to draft opportunities.",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--candidate-id", default=None)
    args = parser.parse_args()

    result = promote_candidates(
        limit=args.limit,
        candidate_id=args.candidate_id,
    )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
