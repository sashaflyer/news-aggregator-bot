from unittest.mock import MagicMock, patch

from aggregator.config import load_config
from aggregator.storage import Storage


def test_build_scheduler_registers_one_job_per_topic(tmp_path):
    from aggregator import scheduler as sched_mod

    cfg = load_config("config.example.toml")
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(cfg.topics)

    with patch.object(sched_mod, "AsyncIOScheduler") as FakeSched:
        instance = MagicMock()
        FakeSched.return_value = instance
        result = sched_mod.build_scheduler(cfg, s)

    assert result is instance
    # One job per topic in the example config; assert the set matches what
    # config.example.toml declares (so adding/removing example topics here
    # doesn't break the test).
    expected = sorted(cfg.topics.keys())
    assert instance.add_job.call_count == len(expected)
    job_topic_args = sorted(
        call.kwargs["args"][0] for call in instance.add_job.call_args_list
    )
    assert job_topic_args == expected
