"""CI-only native renderer precondition, never imported by the product.

Plain Firefox153 and Camoufox156 produced the same first-host Arial pixel
transition in native control run38319076724. Keep that cold result visible,
and require two consecutive exact native drawings before manager acceptance.
Evidence: https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38319076724
Run from the repository root: python -m scripts.prepare_ci_windows_renderer.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from tests.browser.support import serve_local_sites
from tests.browser.test_fingerprint_stability import save_canvas_diagnostic
from tests.browser.test_native_canvas_control import capture_native_canvas


class RendererNotStable(RuntimeError):
    """The native renderer did not converge within the bounded precondition."""


async def prepare_renderer(output_directory: Path) -> list[dict]:
    output_directory.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix="cpm-ci-native-renderer-") as temporary:
        with serve_local_sites() as sites:
            for number in range(1, 4):
                # Fresh profile and native browser/driver each time: convergence
                # must describe the host, not a warmed persistent test profile.
                record, png = await asyncio.wait_for(
                    capture_native_canvas(Path(temporary) / f"native-{number}", sites.first),
                    timeout=180,
                )
                record["phase"] = f"host-preflight-{number}"
                records.append(record)
                save_canvas_diagnostic(output_directory, number, png, record)
                hashes = [entry["pngHash"] for entry in records]
                print(f"Native renderer preflight {number}: {hashes[-1]}", flush=True)
                if number == 2 and hashes[0] != hashes[1]:
                    print(
                        "::warning::Cold Windows native canvas changed on the second draw; "
                        "baseline PNGs and hashes are retained in renderer preflight artifacts.",
                        flush=True,
                    )
                converged = len(hashes) >= 2 and hashes[-1] == hashes[-2]
                (output_directory / "summary.json").write_text(
                    json.dumps(
                        {
                            "converged": converged,
                            "hashes": hashes,
                            "coldTransition": len(hashes) >= 2 and hashes[0] != hashes[1],
                            "engineVersion": records[-1]["engineVersion"],
                            "maxLaunches": 3,
                            "requiredConsecutiveMatches": 2,
                        },
                        indent=2,
                    )
                )
                if converged:
                    return records
    raise RendererNotStable("Native renderer did not stabilize within three launches")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        print("This renderer preflight is only for Windows CI", file=sys.stderr)
        return 2
    try:
        asyncio.run(prepare_renderer(args.output))
    except Exception as exc:
        print(f"::error::Windows renderer preflight failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
