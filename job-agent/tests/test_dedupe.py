from datetime import date

from jobagent.dedupe import SeenStore, split_new
from jobagent.models import Job, merge


class TestScoreFreezing:
    def test_first_verdict_is_kept_forever(self):
        # The whole point: an AI asked twice gives two answers, and a job that
        # drifts across the push threshold pings her on random days.
        store = SeenStore()
        store.remember("abc", score=7, reason="decent match")

        store.remember("abc", score=9, reason="actually great")

        assert store.get("abc").score == 7
        assert store.get("abc").reason == "decent match"

    def test_remember_returns_the_existing_entry(self):
        store = SeenStore()
        first = store.remember("abc", score=7, reason="first")

        second = store.remember("abc", score=2, reason="second")

        assert second == first


class TestPrune:
    def test_drops_entries_past_the_retention_window(self):
        store = SeenStore()
        store.remember("old", 5, "", today=date(2026, 1, 1))
        store.remember("recent", 5, "", today=date(2026, 3, 1))

        removed = store.prune(60, today=date(2026, 3, 15))

        assert removed == 1
        assert "old" not in store
        assert "recent" in store

    def test_keeps_an_entry_exactly_on_the_boundary(self):
        store = SeenStore()
        store.remember("edge", 5, "", today=date(2026, 1, 1))

        store.prune(60, today=date(2026, 3, 2))  # exactly 60 days later

        assert "edge" in store

    def test_drops_undated_legacy_entries(self):
        store = SeenStore({"junk": type("E", (), {"first_seen": "not-a-date"})()})
        store._entries["junk"] = SeenStore().remember("junk", 5, "")
        object.__setattr__(store._entries["junk"], "first_seen", "")

        store.prune(60, today=date(2026, 3, 1))

        assert "junk" not in store


class TestPersistence:
    def test_round_trips_through_a_file(self, tmp_path):
        path = tmp_path / "seen.json"
        store = SeenStore()
        store.remember("abc", 8, "strong ops match", today=date(2026, 2, 2))
        store.mark_notified("abc")
        store.save(path)

        reloaded = SeenStore.load(path)

        entry = reloaded.get("abc")
        assert entry.score == 8
        assert entry.reason == "strong ops match"
        assert entry.first_seen == "2026-02-02"
        assert entry.notified is True

    def test_missing_file_is_an_empty_store(self, tmp_path):
        assert len(SeenStore.load(tmp_path / "nope.json")) == 0

    def test_corrupt_file_does_not_take_the_run_down(self, tmp_path):
        # Losing the memory costs one duplicated digest. Crashing costs every
        # alert until somebody notices, which is far worse.
        path = tmp_path / "seen.json"
        path.write_text("{ this is not json", encoding="utf-8")

        assert len(SeenStore.load(path)) == 0


class TestSplitNew:
    def test_separates_unseen_from_known(self):
        jobs = merge(
            [
                Job(source="adzuna", title="Ops Associate", company="A", url="https://a"),
                Job(source="adzuna", title="Ops Manager", company="B", url="https://b"),
            ]
        )
        store = SeenStore()
        store.remember(jobs[0].fingerprint, 7, "seen before")

        fresh, known = split_new(jobs, store)

        assert [j.title for j in fresh] == ["Ops Manager"]
        assert [j.title for j in known] == ["Ops Associate"]
