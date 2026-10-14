"""fetch_arxiv_corpus admits only UAV / mobile-robot papers and writes records
bulk_ingest_arxiv can read. Network-free: only the pure helpers are exercised."""

from scripts import fetch_arxiv_corpus as fac

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2409.03245v2</id>
    <published>2024-09-05T10:00:00Z</published>
    <title>Cooperative   Multi-Target
      Search with UAV Swarms</title>
    <summary>  We study search. </summary>
    <author><name>A. Author</name></author>
    <author><name>B. Author</name></author>
    <arxiv:journal_ref>IEEE PIMRC 2024</arxiv:journal_ref>
    <category term="cs.RO"/>
    <category term="cs.MA"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/math/0101001v1</id>
    <published>2001-01-01T00:00:00Z</published>
    <title>Old style</title>
    <summary>x</summary>
  </entry>
</feed>"""


def test_title_naming_a_platform_is_enough():
    assert fac.on_topic("Decentralized Exploration with Multi-Robot Teams", "")
    assert fac.on_topic("Quadrotor Trajectory Tracking", "")
    assert fac.on_topic("Energy-aware UAVs for coverage", "")


def test_a_single_passing_mention_in_the_abstract_is_not():
    abstract = "We propose a MARL method. Applications include drones. We test on SMAC."
    assert not fac.on_topic("Value Decomposition for Cooperative MARL", abstract)


def test_two_abstract_mentions_admit_a_marl_paper():
    abstract = "We train a team of UAVs with MARL. Each UAV observes only its neighbours."
    assert fac.on_topic("Scalable Cooperative MARL", abstract)


def test_generic_robots_and_cars_are_off_topic():
    abstract = "A robot arm grasps objects. The robot uses vision. Robot learning at scale."
    assert not fac.on_topic("Dexterous Manipulation", abstract)
    abstract = "Autonomous vehicles must plan. Autonomous vehicles drive on highways."
    assert not fac.on_topic("Motion Planning for Self-Driving Cars", abstract)


def test_platform_terms_need_word_boundaries():
    # "uas" inside "Guasch" or "persuasion" must not count as a platform.
    assert not fac.on_topic(
        "Persuasive language models", "Guasch et al. study persuasion twice: persuasion."
    )


def test_parse_feed_normalises_ids_whitespace_and_fields():
    total, recs = fac.parse_feed(_FEED)
    assert total == 2
    first, second = recs
    assert first["arxiv_id"] == "2409.03245"
    assert first["title"] == "Cooperative Multi-Target Search with UAV Swarms"
    assert first["abstract"] == "We study search."
    assert first["authors"] == ["A. Author", "B. Author"]
    assert first["year"] == 2024
    assert first["journal_ref"] == "IEEE PIMRC 2024"
    assert first["categories"] == ["cs.RO", "cs.MA"]
    assert second["arxiv_id"] == "math/0101001"
    assert second["journal_ref"] is None


def test_parse_feed_skips_the_idless_error_entry():
    feed = _FEED.replace("http://arxiv.org/abs/math/0101001v1", "http://arxiv.org/api/errors#x")
    _, recs = fac.parse_feed(feed)
    assert [r["arxiv_id"] for r in recs] == ["2409.03245"]


def test_interleave_round_robins_dedupes_and_stops_at_target():
    per_query = {
        "a": [{"arxiv_id": "1"}, {"arxiv_id": "2"}, {"arxiv_id": "3"}],
        "b": [{"arxiv_id": "2"}, {"arxiv_id": "4"}],
        "c": [{"arxiv_id": "5"}],
    }
    out = fac.interleave(per_query, target=4)
    assert [r["arxiv_id"] for r in out] == ["1", "2", "5", "4"]


def test_pdf_name_matches_bulk_ingest_lookup():
    assert fac.pdf_name("2409.03245") == "2409.03245.pdf"
    assert fac.pdf_name("math/0101001") == "math_0101001.pdf"
