from tracker.config import ROOT, assign_cluster, load_blogs, load_clusters, load_gsc, load_settings


def test_load_config_files():
    clusters = load_clusters()
    assert {c.key for c in clusters} == {"fde", "genai"}
    assert load_settings()["max_serp_calls"] > 0


def test_assign_cluster():
    clusters = load_clusters()
    assert assign_cluster("Best GenAI course", clusters) == "genai"
    assert assign_cluster("forward deployed engineer salary", clusters) == "fde"
    assert assign_cluster("python tutorial", clusters) is None


def test_load_sample_inputs():
    clusters = load_clusters()
    blogs = load_blogs(ROOT / "data/sample/blogs.csv", clusters)
    assert [b.cluster for b in blogs] == ["fde", "genai"]
    gsc = load_gsc(ROOT / "data/sample/gsc.csv", clusters)
    assert gsc[0].impressions == 5400 and gsc[0].cluster == "fde"


def test_raw_gsc_export_headers(tmp_path):
    p = tmp_path / "Queries.csv"
    p.write_text('Top queries,Clicks,Impressions,CTR,Position\n"GenAI Course Fees",3,"1,200",0.2%,8.1\nzero,0,0,0%,1\n')
    rows = load_gsc(p, load_clusters())
    assert len(rows) == 1 and rows[0].query == "genai course fees" and rows[0].impressions == 1200
