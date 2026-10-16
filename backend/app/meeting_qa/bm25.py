"""BM25 발췌(파이썬 내 처리). 한글은 2-gram 토큰으로 쪼갠다(형태소 분석기·DB 전용 검색 문법을 쓰지 않는다)."""
import math
import re
from collections import Counter

_RUN = re.compile(r"[0-9A-Za-z가-힣]+")


def tokenize(text: str) -> list[str]:
    """글자·숫자 덩어리마다 2-gram(한 글자 덩어리는 그대로). 영문은 소문자로."""
    tokens: list[str] = []
    for run in _RUN.findall((text or "").lower()):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class Bm25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        self._docs = [Counter(tokenize(d)) for d in documents]
        self._len = [sum(c.values()) for c in self._docs]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        self._k1, self._b = k1, b
        df: Counter[str] = Counter()
        for c in self._docs:
            df.update(c.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        out: list[float] = []
        for counts, length in zip(self._docs, self._len):
            score = 0.0
            for t in terms:
                tf = counts.get(t, 0)
                if not tf:
                    continue
                denom = tf + self._k1 * (1 - self._b + self._b * (length / self._avg if self._avg else 0))
                score += self._idf.get(t, 0.0) * tf * (self._k1 + 1) / denom
            out.append(score)
        return out


def coverage(query: str, corpus: str) -> float:
    """질문의 2-gram 중 corpus 에 들어 있는 비율(질문 토큰이 없으면 1.0)."""
    terms = set(tokenize(query))
    if not terms:
        return 1.0
    have = set(tokenize(corpus))
    return len(terms & have) / len(terms)
