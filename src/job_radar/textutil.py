"""Token normalization, skill matching and a dependency-free TF-IDF cosine."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Sequence
from urllib.parse import parse_qs, urlparse, urlunparse

_WORD = re.compile(r"[a-z0-9][a-z0-9+#._-]*")
_WORD_ONLY = re.compile(r"[^a-z0-9]", re.I)

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "was",
    "we", "will", "with", "you", "your", "our", "their", "they", "us", "not", "but",
    "job", "role", "work", "team", "years", "year", "experience", "candidate",
    "company", "please", "apply", "applicants", "ago", "week", "day", "new",
}


def normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip zero-width junk from email HTML."""
    text = text.replace("‌", " ").replace("​", " ").replace("\xa0", " ")
    return " ".join(text.lower().split())


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    toks = _WORD.findall(normalize(text))
    if drop_stopwords:
        toks = [t for t in toks if t not in STOPWORDS and len(t) > 1]
    return toks


def _skill_pattern(skill: str) -> re.Pattern[str]:
    """Word-boundary matcher tolerant of '.', ' ' and '-' inside a skill name."""
    parts = [re.escape(p) for p in re.split(r"[\s._/-]+", normalize(skill)) if p]
    if not parts:
        return re.compile(r"(?!x)x")
    body = r"[\s._/-]*".join(parts)
    lead = r"(?<![a-z0-9+#])"
    trail = r"(?![a-z0-9#])" if not skill.rstrip().endswith("+") else r"(?![a-z0-9])"
    return re.compile(lead + body + trail)


def find_skills(text: str, skills: Sequence[str], aliases: dict[str, list[str]] | None = None) -> list[str]:
    """Return the subset of `skills` mentioned in `text`, matched via aliases too."""
    haystack = normalize(text)
    hits: list[str] = []
    for skill in skills:
        extra = list((aliases or {}).get(skill, []))
        # "go" or "r" on their own match ordinary prose, so a very short id is
        # only ever matched through an alias ("golang").
        variants = extra if len(_WORD_ONLY.sub("", skill)) <= 2 and extra else [skill] + extra
        if any(_skill_pattern(v).search(haystack) for v in variants):
            hits.append(skill)
    return hits


def tfidf_cosine(doc_a: str, doc_b: str) -> float:
    """Cosine similarity of two docs using in-pair IDF. Returns 0.0-1.0."""
    a, b = tokenize(doc_a), tokenize(doc_b)
    if not a or not b:
        return 0.0
    ca, cb = Counter(a), Counter(b)
    vocab = set(ca) | set(cb)
    # With a two-document corpus, idf collapses to "in one doc" vs "in both".
    idf = {t: math.log(2.0 / (1 + (t in ca) + (t in cb)) + 1.0) for t in vocab}

    def vec(counts: Counter[str], total: int) -> dict[str, float]:
        return {t: (c / total) * idf[t] for t, c in counts.items()}

    va, vb = vec(ca, len(a)), vec(cb, len(b))
    dot = sum(va[t] * vb.get(t, 0.0) for t in va)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


# --- URL handling -----------------------------------------------------------

_JOB_ID_PATTERNS = [
    re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})"),
    re.compile(r"[?&](?:currentJobId|jobId|refId)=(\d{6,})"),
    re.compile(r"/jobs/view/(\d{6,})"),
]

TRACKING_PARAMS = {
    "trackingid", "refid", "trk", "trkinfo", "midtoken", "midsig", "eid", "otpToken",
    "lipi", "licu", "lici", "utm_source", "utm_medium", "utm_campaign", "utm_content",
    "utm_term", "originalsubdomain", "position", "pagenum", "src", "recommendedflavor",
}


def extract_job_id(url: str) -> str | None:
    if not url:
        return None
    for pat in _JOB_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def clean_apply_url(url: str, job_id: str | None = None) -> str:
    """Canonical, tracking-free job URL. Falls back to a cleaned original."""
    jid = job_id or extract_job_id(url)
    if jid:
        return f"https://www.linkedin.com/jobs/view/{jid}/"
    if not url:
        return ""
    parsed = urlparse(url)
    kept = {
        k: v
        for k, v in parse_qs(parsed.query).items()
        if k.lower() not in TRACKING_PARAMS
    }
    query = "&".join(f"{k}={v[0]}" for k, v in kept.items())
    host = parsed.netloc.replace("www.linkedin.com", "www.linkedin.com")
    return urlunparse((parsed.scheme or "https", host, parsed.path.replace("/comm/", "/"), "", query, ""))
