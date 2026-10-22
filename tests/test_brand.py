import brand
import search_service as ss
import settings


def test_short_clear_names_score_high():
    assert brand.score("taghub", ["tag"]) == (100, [])
    assert brand.score("realestatehq", ["real", "estate"])[0] == 100


def test_long_stacked_names_score_low_with_reasons():
    points, notes = brand.score("automationplusnow", ["automation"])
    assert points < settings.MIN_BRAND_SCORE
    assert "too long" in notes and "two add-ons" in notes


def test_hard_to_say_clusters_are_penalised():
    points, notes = brand.score("tagbxkt", ["tag"])
    assert points < 100
    assert any("hard to say" in note or "awkward sound" in note for note in notes)


def test_easy_clusters_are_not_penalised():
    points, notes = brand.score("tagworld", ["tag"])
    assert not any("sound" in note or "hard to say" in note for note in notes)


def test_filler_words_are_noted():
    _, notes = brand.score("tagnowworld", ["tag"])
    assert "filler word 'world'" in notes


def test_every_generated_name_meets_the_brand_threshold():
    for keyword in ("automation", "real estate", "tag"):
        terms = ss.tokenize_keywords(keyword)
        for name in ss.word_candidates(keyword, ss.infer_industry(keyword), 100000):
            assert brand.score(name, terms)[0] >= settings.MIN_BRAND_SCORE, name


def test_awkward_patterns_are_no_longer_generated():
    pool = set(ss.word_candidates("real estate", "real_estate", 100000)) | set(ss.word_candidates("automation", None, 100000))
    for bad in ("automationers", "realestateers", "really", "myreal", "realio", "automationhqio", "realestatehqly"):
        assert bad not in pool, bad


def test_offers_carry_the_brand_score_and_notes():
    import godaddy_checker as gc
    offer = ss.to_offer(gc.CheckResult(domain="taghub.com", available=True, price=500, currency="INR"),
                        ss.budget_config(ss.SearchRequest(keyword="tag", tlds=(".com",))), ["tag"])
    assert offer["brand_score"] == 100
    assert offer["brand_notes"] == []
