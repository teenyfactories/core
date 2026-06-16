"""Unit tests for teenyfactories.claims — atomic claim primitive for handler dispatch.

DB-touching tests run against a real Postgres if POSTGRES_HOST is set in env,
otherwise skipped. Pure-function tests (hash determinism, timestamp
normalisation) run unconditionally.
"""

import hashlib
import os
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from teenyfactories import claims


# ── Pure-function tests (no DB) ─────────────────────────────────────────────


class TestNormalizeTimestamp:
    def test_naive_datetime_assumed_utc(self):
        ts = datetime(2026, 6, 2, 11, 0, 0, 123456)
        out = claims._normalize_timestamp(ts)
        assert out == "2026-06-02T11:00:00.123456+0000"

    def test_aware_datetime_converted_to_utc(self):
        from datetime import timezone, timedelta
        sydney = timezone(timedelta(hours=10))
        ts = datetime(2026, 6, 2, 21, 0, 0, 123456, tzinfo=sydney)
        out = claims._normalize_timestamp(ts)
        # 21:00 +1000 → 11:00 UTC
        assert out == "2026-06-02T11:00:00.123456+0000"

    def test_none_returns_empty(self):
        assert claims._normalize_timestamp(None) == ""

    def test_string_passed_through_as_str(self):
        # Defensive — driver shouldn't return strings, but if it does, no crash.
        assert claims._normalize_timestamp("2026-06-02T11:00:00") == "2026-06-02T11:00:00"


class TestHashClaimKey:
    def test_deterministic(self):
        ts = datetime(2026, 6, 2, 11, 0, 0, 123456, tzinfo=timezone.utc)
        with patch.object(claims.config, 'FACTORY_NAME', 'demo'):
            h1 = claims.hash_claim_key("messages", "k1", "approved", ts)
            h2 = claims.hash_claim_key("messages", "k1", "approved", ts)
        assert h1 == h2
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_different_inputs_different_hashes(self):
        ts = datetime(2026, 6, 2, 11, 0, 0, 0, tzinfo=timezone.utc)
        with patch.object(claims.config, 'FACTORY_NAME', 'demo'):
            h_base = claims.hash_claim_key("messages", "k1", "approved", ts)
            assert claims.hash_claim_key("OTHER", "k1", "approved", ts) != h_base
            assert claims.hash_claim_key("messages", "OTHER", "approved", ts) != h_base
            assert claims.hash_claim_key("messages", "k1", "OTHER", ts) != h_base
            # Different timestamp (1 microsecond later) → different hash
            ts_plus = ts + timedelta(microseconds=1)
            assert claims.hash_claim_key("messages", "k1", "approved", ts_plus) != h_base

    def test_factory_name_in_hash(self):
        ts = datetime(2026, 6, 2, 11, 0, 0, 0, tzinfo=timezone.utc)
        with patch.object(claims.config, 'FACTORY_NAME', 'demo'):
            h_demo = claims.hash_claim_key("c", "k", "s", ts)
        with patch.object(claims.config, 'FACTORY_NAME', 'other'):
            h_other = claims.hash_claim_key("c", "k", "s", ts)
        assert h_demo != h_other

    def test_known_vector(self):
        """Lock the hash output for a known input — protects against accidental
        changes to the hash format (which would break in-flight claims after
        a deploy)."""
        ts = datetime(2026, 6, 2, 11, 0, 0, 123456, tzinfo=timezone.utc)
        with patch.object(claims.config, 'FACTORY_NAME', 'demo'):
            h = claims.hash_claim_key("messages", "k1", "approved", ts)
        # Compute the expected hash from the locked format.
        blob = "demo|messages|k1|approved|2026-06-02T11:00:00.123456+0000".encode()
        expected = hashlib.sha256(blob).hexdigest()
        assert h == expected


class TestWorkerId:
    def test_hostname_used_when_set(self):
        with patch.dict(os.environ, {'HOSTNAME': 'pod-abc-123'}):
            assert claims._worker_id() == 'pod-abc-123'

    def test_pid_fallback_when_hostname_unset(self):
        env = {k: v for k, v in os.environ.items() if k != 'HOSTNAME'}
        with patch.dict(os.environ, env, clear=True):
            wid = claims._worker_id()
            assert wid.startswith('pid-')
            assert wid[4:].isdigit()


# ── DB-touching tests (real Postgres required) ──────────────────────────────

DB_AVAILABLE = bool(os.environ.get('POSTGRES_HOST'))
SKIP_NO_DB = pytest.mark.skipif(not DB_AVAILABLE, reason="requires POSTGRES_HOST + factory_job_claims table")


def _seed_source_row(coll: str, key: str, state: str):
    """Insert (or replace) a factory_data row and return its state_changed_at.

    The CTE form of try_claim predicates on the source row existing at the
    exact (collection, key, state, state_changed_at) tuple — so DB tests
    must seed a real row before attempting to claim.
    """
    cursor = claims._claim_cursor()
    cursor.execute(
        """
        INSERT INTO public.factory_data (factory_name, collection, key, state, value)
        VALUES (%s, %s, %s, %s, '{}'::jsonb)
        ON CONFLICT (factory_name, collection, key)
        DO UPDATE SET state = EXCLUDED.state
        RETURNING state_changed_at
        """,
        (claims.config.FACTORY_NAME, coll, key, state),
    )
    return cursor.fetchone()[0]


def _cleanup_source_row(coll: str, key: str):
    cursor = claims._claim_cursor()
    cursor.execute(
        "DELETE FROM public.factory_data WHERE factory_name=%s AND collection=%s AND key=%s",
        (claims.config.FACTORY_NAME, coll, key),
    )


@SKIP_NO_DB
class TestClaimRace:
    """Tests against a real Postgres. Requires the migration to have run."""

    def test_first_claim_wins(self):
        sca = _seed_source_row("test_coll", "k_race_1", "pending")
        try:
            assert claims.try_claim("test_coll", "k_race_1", "pending", sca, 60) is True
            with patch.object(claims, '_worker_id', return_value='other-worker'):
                assert claims.try_claim("test_coll", "k_race_1", "pending", sca, 60) is False
            claims.release_claim("test_coll", "k_race_1", "pending", sca)
        finally:
            _cleanup_source_row("test_coll", "k_race_1")

    def test_release_then_reclaim(self):
        sca = _seed_source_row("test_coll", "k_release_1", "pending")
        try:
            assert claims.try_claim("test_coll", "k_release_1", "pending", sca, 60) is True
            claims.release_claim("test_coll", "k_release_1", "pending", sca)
            # After release, while source row STILL in same state at same sca,
            # another worker can claim the SAME (coll,key,state,sca).
            with patch.object(claims, '_worker_id', return_value='other-worker'):
                assert claims.try_claim("test_coll", "k_release_1", "pending", sca, 60) is True
                claims.release_claim("test_coll", "k_release_1", "pending", sca)
        finally:
            _cleanup_source_row("test_coll", "k_release_1")

    def test_release_only_releases_own_claim(self):
        sca = _seed_source_row("test_coll", "k_own_1", "pending")
        try:
            with patch.object(claims, '_worker_id', return_value='worker-A'):
                assert claims.try_claim("test_coll", "k_own_1", "pending", sca, 60) is True
            with patch.object(claims, '_worker_id', return_value='worker-B'):
                claims.release_claim("test_coll", "k_own_1", "pending", sca)
            with patch.object(claims, '_worker_id', return_value='worker-C'):
                assert claims.try_claim("test_coll", "k_own_1", "pending", sca, 60) is False
            with patch.object(claims, '_worker_id', return_value='worker-A'):
                claims.release_claim("test_coll", "k_own_1", "pending", sca)
        finally:
            _cleanup_source_row("test_coll", "k_own_1")

    def test_stale_snapshot_cannot_reclaim_after_state_advanced(self):
        """The race the CTE form closes: worker B saw the row at T1, worker A
        claimed, ran, transitioned the row (state_changed_at → T2), and
        released. Worker B's late try_claim with sca=T1 MUST fail because the
        source row no longer matches the predicate."""
        sca_t1 = _seed_source_row("test_coll", "k_stale_1", "pending")
        try:
            # Worker A claims and releases (simulating handler completion).
            with patch.object(claims, '_worker_id', return_value='worker-A'):
                assert claims.try_claim("test_coll", "k_stale_1", "pending", sca_t1, 60) is True
                claims.release_claim("test_coll", "k_stale_1", "pending", sca_t1)

            # Source row state advances: 'pending' → 'done'. Trigger bumps
            # state_changed_at to T2.
            cursor = claims._claim_cursor()
            cursor.execute(
                "UPDATE public.factory_data SET state = 'done' "
                "WHERE factory_name=%s AND collection=%s AND key=%s",
                (claims.config.FACTORY_NAME, "test_coll", "k_stale_1"),
            )

            # Worker B, still operating on the stale sca_t1 snapshot, tries to
            # re-claim. The CTE's source-row predicate yields 0 rows → INSERT
            # inserts nothing → won=False. Race closed.
            with patch.object(claims, '_worker_id', return_value='worker-B'):
                assert claims.try_claim("test_coll", "k_stale_1", "pending", sca_t1, 60) is False
        finally:
            _cleanup_source_row("test_coll", "k_stale_1")


@SKIP_NO_DB
class TestJanitorSweep:
    def test_reaps_expired_claims(self):
        sca = datetime.now(timezone.utc)
        # Claim with tiny TTL so it expires before we sweep
        assert claims.try_claim("test_coll", "k_janitor_1", "pending", sca, 0.001) is True
        time.sleep(0.5)
        # Force the janitor to run regardless of interval gate
        claims._last_janitor_tick = 0.0
        claims.janitor_sweep_if_due()
        # Claim should be gone — another worker can re-acquire
        with patch.object(claims, '_worker_id', return_value='post-reap'):
            assert claims.try_claim("test_coll", "k_janitor_1", "pending", sca, 60) is True
            claims.release_claim("test_coll", "k_janitor_1", "pending", sca)

    def test_does_not_reap_active_claims(self):
        sca = datetime.now(timezone.utc)
        assert claims.try_claim("test_coll", "k_active_1", "pending", sca, 3600) is True
        claims._last_janitor_tick = 0.0
        claims.janitor_sweep_if_due()
        # Claim still active — another worker can't acquire
        with patch.object(claims, '_worker_id', return_value='other'):
            assert claims.try_claim("test_coll", "k_active_1", "pending", sca, 60) is False
        claims.release_claim("test_coll", "k_active_1", "pending", sca)

    def test_interval_gate(self):
        """janitor_sweep_if_due should be a no-op when called too soon."""
        claims._last_janitor_tick = time.monotonic()  # just ran
        # Calling again immediately should not hit the DB. Mock the cursor
        # method to confirm no execute calls.
        with patch.object(claims, '_claim_cursor') as mock_get_cursor:
            mock_get_cursor.return_value = MagicMock()
            claims.janitor_sweep_if_due()
            # Cursor was NOT fetched — interval gate held
            mock_get_cursor.assert_not_called()
