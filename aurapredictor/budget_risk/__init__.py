"""Budget_Opportunity_Risk: the highest budget a movie with given pre-investment
parameters can carry and still be expected to earn HIT_RATIO x its budget back.
A budget below that line is an opportunity, one above it a risk."""

from pathlib import Path

from aurapredictor.constants import FLOP_MAX_RATIO

MODEL_NAME = "Budget_Opportunity_Risk"

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "budget_opportunity_risk"
MODEL_PATH = ARTIFACTS_DIR / "model.cbm"
ENCODERS_PATH = ARTIFACTS_DIR / "encoders.pkl"
LAYOUT_PATH = ARTIFACTS_DIR / "layout.json"

# revenue >= 2x budget is a hit -- the same bar as constants.FLOP_MAX_RATIO, so this
# model's risk line and the revenue model's flop/hit tiers agree.
HIT_RATIO = FLOP_MAX_RATIO
