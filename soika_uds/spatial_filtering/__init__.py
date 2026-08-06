"""Public API for deterministic spatial territory filtering."""

from .geometry import SpatialTarget, build_spatial_target, project_geo_point
from .models import (
    ALGORITHM_VERSION,
    SOURCE_CRS,
    SpatialDecision,
    SpatialFilterBatchResult,
    SpatialFilterConfig,
    SpatialFilterStats,
    SpatialMessageResult,
    SpatialRelation,
    TerritoryMode,
)
from .orchestration import SpatialFilteringStageHandler
from .postgis import (
    PostGISIndexSpec,
    PostGISQueryPlan,
    spatial_index_plan,
    spatial_query_plan,
)
from .runtime import SpatialFilterEngine

__all__ = [
    "ALGORITHM_VERSION",
    "SOURCE_CRS",
    "PostGISIndexSpec",
    "PostGISQueryPlan",
    "SpatialDecision",
    "SpatialFilterBatchResult",
    "SpatialFilterConfig",
    "SpatialFilterEngine",
    "SpatialFilterStats",
    "SpatialFilteringStageHandler",
    "SpatialMessageResult",
    "SpatialRelation",
    "SpatialTarget",
    "TerritoryMode",
    "build_spatial_target",
    "project_geo_point",
    "spatial_index_plan",
    "spatial_query_plan",
]
