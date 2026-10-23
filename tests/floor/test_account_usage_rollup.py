"""Floor test entrypoint; groups keep the suite reviewable by behavior."""

import pytest

from tests.floor.account_rollup_floor_group_1 import (
    test_account_delete_removes_rollup_row as test_account_delete_removes_rollup_row,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_accounts_api_request_usage_unchanged_by_folding as test_accounts_api_request_usage_unchanged_by_folding,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_backfill_slices_span_long_history as test_backfill_slices_span_long_history,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_backfill_start_skips_excluded_prefix as test_backfill_start_skips_excluded_prefix,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_concurrent_fold_passes_do_not_double_count as test_concurrent_fold_passes_do_not_double_count,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_fold_absorbs_widened_watermark_gap as test_fold_absorbs_widened_watermark_gap,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_fold_pass_is_idempotent as test_fold_pass_is_idempotent,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_fold_preserves_summary_totals_and_dedupe as test_fold_preserves_summary_totals_and_dedupe,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_fold_respects_safety_lag as test_fold_respects_safety_lag,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_identity_merge_preserves_folded_usage as test_identity_merge_preserves_folded_usage,
)
from tests.floor.account_rollup_floor_group_1 import (
    test_identity_merge_racing_fold_loses_no_usage as test_identity_merge_racing_fold_loses_no_usage,
)
from tests.floor.account_rollup_floor_group_2 import (
    test_summary_cache_cleared_on_account_delete as test_summary_cache_cleared_on_account_delete,
)
from tests.floor.account_rollup_floor_group_2 import (
    test_summary_cache_cleared_on_identity_consolidation as test_summary_cache_cleared_on_identity_consolidation,
)
from tests.floor.account_rollup_floor_group_2 import (
    test_summary_cache_fill_discarded_when_invalidated_mid_flight as test_summary_cache_fill_discarded_when_invalidated_mid_flight,
)
from tests.floor.account_rollup_floor_group_2 import (
    test_summary_cache_serves_within_ttl_per_signature as test_summary_cache_serves_within_ttl_per_signature,
)

pytestmark = pytest.mark.integration
