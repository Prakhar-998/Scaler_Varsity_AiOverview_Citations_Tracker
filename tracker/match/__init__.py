from .citations import CitationMatch, is_brand, is_varsity, match_citations
from .urls import domain_of, is_google_redirect, normalise_url, unwrap_google_redirect

__all__ = ["CitationMatch", "match_citations", "is_varsity", "is_brand", "normalise_url", "domain_of",
           "is_google_redirect", "unwrap_google_redirect"]
