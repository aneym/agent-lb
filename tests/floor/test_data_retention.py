"""Floor test entrypoint; groups keep the suite reviewable by behavior."""

import pytest

from tests.floor.retention_floor_group_1 import (
    test_pruning_drains_backlog_across_batches as test_pruning_drains_backlog_across_batches,
)
from tests.floor.retention_floor_group_1 import (
    test_request_log_pruning_respects_watermark_and_preserves_totals as test_request_log_pruning_respects_watermark_and_preserves_totals,
)
from tests.floor.retention_floor_group_1 import (
    test_request_log_pruning_skipped_while_conversation_backfill_behind as test_request_log_pruning_skipped_while_conversation_backfill_behind,
)
from tests.floor.retention_floor_group_1 import (
    test_request_log_pruning_skipped_while_fold_is_not_current as test_request_log_pruning_skipped_while_fold_is_not_current,
)
from tests.floor.retention_floor_group_1 import (
    test_request_log_pruning_skipped_while_hourly_backfill_behind as test_request_log_pruning_skipped_while_hourly_backfill_behind,
)
from tests.floor.retention_floor_group_1 import (
    test_request_log_pruning_skipped_while_hourly_fold_stalled as test_request_log_pruning_skipped_while_hourly_fold_stalled,
)
from tests.floor.retention_floor_group_1 import (
    test_request_log_pruning_skipped_without_watermark as test_request_log_pruning_skipped_without_watermark,
)
from tests.floor.retention_floor_group_1 import (
    test_retention_disabled_by_default_deletes_nothing as test_retention_disabled_by_default_deletes_nothing,
)
from tests.floor.retention_floor_group_1 import (
    test_retention_settings_validation as test_retention_settings_validation,
)
from tests.floor.retention_floor_group_1 import (
    test_usage_history_protects_latest_by_recorded_at_not_insert_order as test_usage_history_protects_latest_by_recorded_at_not_insert_order,
)
from tests.floor.retention_floor_group_1 import (
    test_usage_history_pruning_keeps_latest_per_identity as test_usage_history_pruning_keeps_latest_per_identity,
)
from tests.floor.retention_floor_group_2 import (
    test_api_key_rollup_migration_resets_prior_fold_state as test_api_key_rollup_migration_resets_prior_fold_state,
)
from tests.floor.retention_floor_group_2 import (
    test_api_key_totals_survive_pruning_and_match_pre_fold as test_api_key_totals_survive_pruning_and_match_pre_fold,
)
from tests.floor.retention_floor_group_2 import (
    test_dashboard_retention_overrides_env_alias as test_dashboard_retention_overrides_env_alias,
)
from tests.floor.retention_floor_group_2 import (
    test_dashboard_zero_disables_retention_despite_env_alias as test_dashboard_zero_disables_retention_despite_env_alias,
)
from tests.floor.retention_floor_group_2 import (
    test_env_alias_applies_while_dashboard_value_unset as test_env_alias_applies_while_dashboard_value_unset,
)
from tests.floor.retention_floor_group_2 import (
    test_fold_start_covers_key_only_soft_deleted_history as test_fold_start_covers_key_only_soft_deleted_history,
)
from tests.floor.retention_floor_group_2 import (
    test_retention_settings_reject_absurd_values as test_retention_settings_reject_absurd_values,
)
from tests.floor.retention_floor_group_2 import (
    test_usage_history_backlog_drains_across_batches as test_usage_history_backlog_drains_across_batches,
)
from tests.floor.retention_floor_group_2 import (
    test_usage_history_pruning_null_window_and_multi_identity as test_usage_history_pruning_null_window_and_multi_identity,
)

pytestmark = pytest.mark.integration
