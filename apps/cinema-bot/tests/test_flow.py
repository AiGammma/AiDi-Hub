"""End-to-end through the services with a fake TMDB and no LLM."""

from conftest import RATINGS_CSV

from cinebot.adapters import kinopoisk_csv
from cinebot.domain.models import Mode


async def _onboard(c):
    user = await c.users.get_or_create(1001)
    rows = kinopoisk_csv.parse_ratings(RATINGS_CSV)
    report = await c.importer.import_rows(user.id, rows, "ratings")
    await c.importer.import_rows(user.id, kinopoisk_csv.parse_wishlist("Superbad (2007)\nInception (2010)\n"), "wishlist")
    await c.directors.set_directors(user.id, ["Villeneuve"])
    return user, report


async def test_import_matches_and_reports_unmatched(container):
    user, report = await _onboard(container)
    assert report.total == 5 and report.matched == 4
    assert report.unmatched == ["Unknown Movie (2001)"]
    profile = await container.profiles.load(user.id)
    assert profile.n_ratings == 4 and profile.weights["director:525"] > 0


async def test_wishlist_never_overrides_a_rating(container):
    user, _ = await _onboard(container)
    # Inception is rated 10 and also in the wishlist file: it must stay rated and be excluded from picks.
    picks = await container.recommender.recommend(user.id, Mode.TASTE, n=10, use_llm=False)
    titles = [p.meta.title for p in picks]
    assert "Inception" not in titles and "Dunkirk" not in titles


async def test_taste_mode_ranks_liked_directors_first(container):
    user, _ = await _onboard(container)
    picks = await container.recommender.recommend(user.id, Mode.TASTE, n=5, use_llm=False)
    titles = [p.meta.title for p in picks]
    assert titles[0] in {"Arrival", "Blade Runner 2049"}
    assert titles.index("Arrival") < titles.index("The Office")
    assert all(p.reason for p in picks) and all(p.recommendation_id for p in picks)


async def test_english_mode_only_american_english(container):
    user, _ = await _onboard(container)
    picks = await container.recommender.recommend(user.id, Mode.ENGLISH, n=10, use_llm=False)
    assert picks, "expected at least one English-practice pick"
    assert all(p.english and p.english.eligible for p in picks)
    assert "Amélie" not in [p.meta.title for p in picks]


async def test_feedback_hides_title_next_time(container):
    user, _ = await _onboard(container)
    first = await container.recommender.recommend(user.id, Mode.TASTE, n=5, use_llm=False)
    top = first[0]
    assert await container.recommender.record_feedback(user.id, top.recommendation_id, "disliked")
    again = await container.recommender.recommend(user.id, Mode.TASTE, n=5, use_llm=False)
    assert top.meta.title not in [p.meta.title for p in again]


async def test_feedback_for_someone_elses_recommendation_is_rejected(container):
    user, _ = await _onboard(container)
    other = await container.users.get_or_create(2002)
    picks = await container.recommender.recommend(user.id, Mode.TASTE, n=1, use_llm=False)
    assert not await container.recommender.record_feedback(other.id, picks[0].recommendation_id, "liked")


async def test_new_user_with_no_data_gets_nothing(container):
    user = await container.users.get_or_create(3003)
    assert await container.recommender.recommend(user.id, Mode.TASTE, use_llm=False) == []
