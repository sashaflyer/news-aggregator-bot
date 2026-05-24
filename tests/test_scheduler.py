from unittest.mock import MagicMock, patch

from aggregator.config import load_config
from aggregator.storage import Storage


def test_build_scheduler_registers_one_job_per_topic(tmp_path):
    from aggregator import scheduler as sched_mod

    cfg = load_config("config.example.toml")
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(
        general_subreddits=["CryptoCurrency"], general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL"], watchlist_schedule="30 8 * * *",
    )

    with patch.object(sched_mod, "AsyncIOScheduler") as FakeSched:
        instance = MagicMock()
        FakeSched.return_value = instance
        result = sched_mod.build_scheduler(cfg, s)

    assert result is instance
    assert instance.add_job.call_count == 2
    # Each add_job call's positional args have run_digest in args[0]; the topic_id is in args=("crypto_general", cfg, storage) tuple passed via kwarg
    job_topic_args = sorted(
        call.kwargs["args"][0] for call in instance.add_job.call_args_list
    )
    assert job_topic_args == ["crypto_general", "crypto_watchlist"]
