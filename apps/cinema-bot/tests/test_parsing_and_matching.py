from cinebot.adapters import kinopoisk_csv
from cinebot.adapters.tmdb import pick_best_match
from cinebot.domain.models import MediaType


def test_ratings_semicolon_cp1251_with_aliases():
    csv_text = "Название;Оригинальное название;Год;Моя оценка;Дата оценки\nНачало;Inception;2010;10;05.03.2021\nДрузья (сериал);Friends;1994;0;\n"
    rows = kinopoisk_csv.parse_ratings(csv_text.encode("cp1251"))
    assert [r.title for r in rows] == ["Начало", "Друзья"]
    assert rows[0].original_title == "Inception"
    assert rows[0].rating == 10 and rows[0].year == 2010
    assert rows[0].rated_at.isoformat() == "2021-03-05"
    assert rows[1].media_hint is MediaType.TV
    assert rows[1].rating is None  # 0 means "no rating" -> seen, unrated


def test_ratings_comma_header_and_year_in_title():
    rows = kinopoisk_csv.parse_ratings("title,rating\nHeat (1995),9\n".encode())
    assert rows[0].title == "Heat" and rows[0].year == 1995 and rows[0].rating == 9


def test_missing_title_column_is_a_clear_error():
    try:
        kinopoisk_csv.parse_ratings("a;b\n1;2\n".encode())
    except ValueError as e:
        assert "Название" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_wishlist_plain_lines():
    text = "1. Начало (2010)\n- Интерстеллар, 2014\nДом Дракона (сериал)\n\n"
    rows = kinopoisk_csv.parse_wishlist(text)
    assert [(r.title, r.year) for r in rows] == [("Начало", 2010), ("Интерстеллар", 2014), ("Дом Дракона", None)]
    assert rows[2].media_hint is MediaType.TV


def test_best_match_prefers_year_and_name():
    results = [
        {"id": 1, "title": "Heat", "release_date": "2013-06-01", "popularity": 90},
        {"id": 2, "title": "Heat", "release_date": "1995-12-15", "popularity": 20},
    ]
    assert pick_best_match(results, "Heat", None, 1995)["id"] == 2


def test_best_match_rejects_wrong_year_and_name():
    results = [{"id": 1, "title": "Something Else", "release_date": "1980-01-01", "popularity": 5}]
    assert pick_best_match(results, "Heat", None, 1995) is None
    assert pick_best_match([], "Heat", None, None) is None
