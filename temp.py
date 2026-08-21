import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UTILS = ROOT / "utils"
if str(UTILS) not in sys.path:
    sys.path.insert(0, str(UTILS))

import recorder


def fix_saved_recording(
    input_path: str,
    output_path: str | None = None,
    *,
    tolerance: float = 0.08,
    min_group_size: int = 2,
) -> Path:
    """Open a saved recording JSON, merge stick jitter, and save the result."""
    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Recording file not found: {source}")

    destination = (
        Path(output_path)
        if output_path is not None
        else source.with_name(f"{source.stem}_optimized{source.suffix}")
    )

    with source.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    fixed_data = recorder.merge_stick_jitter(
        data,
        tolerance=tolerance,
        min_group_size=min_group_size,
    )

    with destination.open("w", encoding="utf-8") as handle:
        json.dump(fixed_data, handle, indent=2)

    return destination

fix_saved_recording(
    "saved_outputs/recording_20260415_132252.json",
    "saved_outputs/recording_20260415_132252_optimized.json",
    tolerance=0.08
)
