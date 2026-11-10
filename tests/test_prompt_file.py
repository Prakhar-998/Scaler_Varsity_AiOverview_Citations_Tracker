import pytest

from tracker.config import Cluster
from tracker.prompts import load_prompt_file

CLUSTERS = [Cluster("fde", "Forward Deployed Engineer", 1.0, True, ["fde"]),
            Cluster("genai", "Generative AI", 0.5, True, ["genai"])]


def test_defaults_source_manual_and_classifies_intent(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("Prompt,Cluster\n  Best FDE Course in India? ,FDE\nfde course fees,fde\n,fde\n")
    cands = load_prompt_file(path, CLUSTERS)
    assert [(c.prompt, c.cluster, c.source, c.intent) for c in cands] == [
        ("best fde course in india", "fde", "manual", "best"), ("fde course fees", "fde", "manual", "fees")]


def test_keeps_given_source_and_intent(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("prompt,cluster,source,intent,source blog\nfde vs sde salary,fde,llm,salary,Blog A\n")
    c = load_prompt_file(path, CLUSTERS)[0]
    assert (c.source, c.intent, c.intent_weight, c.source_blog) == ("llm", "salary", 0.5, "Blog A")


def test_unknown_cluster_skipped_with_warning(tmp_path, caplog):
    path = tmp_path / "p.csv"
    path.write_text("prompt,cluster\nbest data science course,ds\n")
    assert load_prompt_file(path, CLUSTERS) == []
    assert "not an active cluster" in caplog.text


def test_missing_columns_is_a_clear_error(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("query,impressions\nx,1\n")
    with pytest.raises(ValueError, match="needs 'prompt' and 'cluster'"):
        load_prompt_file(path, CLUSTERS)


def test_prompt_set_id_ignores_order_and_case():
    from tracker.prompts import prompt_set_id

    assert prompt_set_id(["b", "A"]) == prompt_set_id(["a", "B "]) != prompt_set_id(["a", "c"])
    assert prompt_set_id(["a"]).startswith("set-")
