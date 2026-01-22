from dagster import Definitions, load_assets_from_modules
from .assets import synthea, ignition

all_assets = load_assets_from_modules([synthea, ignition])

defs = Definitions(
    assets=all_assets,
)
