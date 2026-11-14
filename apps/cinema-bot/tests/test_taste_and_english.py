from conftest import BY_KEY

from cinebot.services import taste
from cinebot.services.english import english_fit


def test_profile_learns_likes_and_dislikes():
    items = [
        (BY_KEY["movie:27205"], 10),
        (BY_KEY["movie:157336"], 9),
        (BY_KEY["tv:1668"], 3),
    ]
    profile = taste.build_profile(items)
    assert profile.n_ratings == 3
    assert profile.weights["director:525"] > 0          # Nolan up
    assert profile.weights["genre:35"] < 0              # comedy down
    likes, dislikes = taste.describe(profile)
    assert "Christopher Nolan" in likes
    assert "комедия" in dislikes
    # A Nolan-like film scores above a sitcom
    assert taste.score(profile, BY_KEY["movie:374720"]) > taste.score(profile, BY_KEY["tv:2316"])


def test_empty_profile_scores_zero():
    assert taste.score(taste.build_profile([]), BY_KEY["movie:27205"]) == 0.0


def test_english_gate_requires_us_and_english():
    assert english_fit(BY_KEY["movie:194"]).eligible is False  # French
    uk_only = BY_KEY["movie:27205"].model_copy(update={"countries": ["GB"]})
    assert english_fit(uk_only).eligible is False


def test_english_scoring_prefers_modern_sitcom_over_war_drama():
    office = english_fit(BY_KEY["tv:2316"])
    dunkirk = english_fit(BY_KEY["movie:374720"])
    assert office.eligible and dunkirk.eligible
    assert office.score > dunkirk.score
    assert any("британск" in t for t in dunkirk.tags)  # US/GB co-production
